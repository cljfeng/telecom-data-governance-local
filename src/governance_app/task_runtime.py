from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any
from uuid import uuid4

from governance_app.archive import archive_batch, export_notice_report
from governance_app.audit_engine import run_audit
from governance_app.config import AppConfig, RuntimeMode
from governance_app.electricity_analysis import (
    export_electricity_opportunities,
    run_electricity_analysis,
)
from governance_app.exporter import export_issue_packages
from governance_app.file_storage_runtime import file_storage_for
from governance_app.identity_store import (
    TaskRecord,
    identity_store_for,
    task_payload,
)
from governance_app.importer import import_workbook
from governance_app.operation_guard import exclusive_operation
from governance_app.request_context import (
    RequestMetadata,
    current_principal,
    request_context,
)
from governance_app.routes.common import (
    JsonResponse,
    json_response,
    stored_file_payload,
)
from governance_app.security import claim_batch_for_current_principal
from governance_app.tower_rent_analysis import (
    export_tower_rent_clues,
    run_tower_rent_analysis,
)


class TaskManager:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._store = identity_store_for(config)
        self._executor = ThreadPoolExecutor(
            max_workers=config.task_worker_count,
            thread_name_prefix="governance-task",
        )

    def enqueue(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> tuple[TaskRecord, bool]:
        principal = current_principal()
        if principal is None:
            raise ValueError("authentication required")
        task, created = self._store.enqueue_task(
            principal,
            kind=kind,
            payload=payload,
            idempotency_key=idempotency_key or uuid4().hex,
        )
        if created or task.status == "retry":
            self._executor.submit(self._run, task.id)
        return task, created

    def recover(self) -> None:
        for task_id in self._store.recoverable_task_ids():
            self._executor.submit(self._run, task_id)

    def submit_existing(self, task_id: int) -> None:
        self._executor.submit(self._run, task_id)

    def _run(self, task_id: int) -> None:
        task = self._store.claim_task(task_id)
        if task is None:
            return
        principal = self._store.task_principal(task)
        metadata = RequestMetadata(
            request_id=f"task-{task.id}-{uuid4().hex}",
            method="TASK",
            path=f"task:{task.kind}",
            source_ip="background-worker",
            user_agent="governance-task-runner",
            task_id=task.id,
        )
        try:
            with request_context(principal, metadata):
                self._store.update_task_progress(task.id, 10)
                result = _execute_task(self._config, task)
                self._store.complete_task(task.id, result)
        except Exception as exc:
            if self._store.fail_task(task.id, str(exc)):
                self._executor.submit(self._run, task.id)


@lru_cache(maxsize=16)
def task_manager_for(config: AppConfig) -> TaskManager:
    return TaskManager(config)


def enqueue_online_task(
    config: AppConfig,
    *,
    kind: str,
    payload: dict[str, Any],
) -> JsonResponse | None:
    if config.runtime_mode is not RuntimeMode.ONLINE:
        return None
    raw_key = payload.get("idempotency_key")
    idempotency_key = (
        raw_key.strip()
        if isinstance(raw_key, str) and raw_key.strip()
        else None
    )
    task, created = task_manager_for(config).enqueue(
        kind=kind,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return json_response(
        {
            "task": task_payload(task),
            "deduplicated": not created,
        },
        status=202 if created else 200,
    )


def _execute_task(
    config: AppConfig,
    task: TaskRecord,
) -> dict[str, Any]:
    payload = task.payload
    if task.kind == "import":
        storage = file_storage_for(config)
        file_id = _required_string(payload, "file_id")
        workbook_path = storage.resolve(file_id).local_path
        strategy = str(payload.get("strategy", "new"))
        raw_batch_id = payload.get("batch_id")
        with exclusive_operation(config, "import"):
            result = import_workbook(
                config,
                workbook_path,
                strategy=strategy,
                batch_id=(
                    None
                    if raw_batch_id in (None, "")
                    else int(raw_batch_id)
                ),
                source_reference=file_id,
            )
            if result.batch_id is not None:
                claim_batch_for_current_principal(
                    config,
                    result.batch_id,
                )
        return {
            "batch_id": result.batch_id,
            "ledger_counts": result.ledger_counts,
            "errors": [error.__dict__ for error in result.errors],
        }
    batch_id = int(payload["batch_id"])
    if task.kind == "audit":
        with exclusive_operation(config, "audit"):
            result = run_audit(config, batch_id)
        return result.__dict__
    if task.kind == "electricity_analysis":
        return run_electricity_analysis(config, batch_id)
    if task.kind == "tower_rent_analysis":
        return run_tower_rent_analysis(config, batch_id)
    if task.kind == "export_issues":
        paths = export_issue_packages(
            config,
            batch_id,
            mode=str(payload.get("mode", "city")),
        )
        return {"files": [_published(config, path) for path in paths]}
    if task.kind == "notice_report":
        return {
            "file": _published(
                config,
                export_notice_report(config, batch_id),
            )
        }
    if task.kind == "archive":
        with exclusive_operation(config, "archive"):
            path = archive_batch(config, batch_id)
        return {"file": _published(config, path)}
    if task.kind == "electricity_export":
        return {
            "file": _published(
                config,
                export_electricity_opportunities(config, batch_id),
            )
        }
    if task.kind == "tower_rent_export":
        return {
            "file": _published(
                config,
                export_tower_rent_clues(config, batch_id),
            )
        }
    raise ValueError(f"unsupported task kind: {task.kind}")


def _published(config: AppConfig, path) -> dict[str, str]:
    stored = file_storage_for(config).publish(path)
    return stored_file_payload(config, stored)


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required")
    return value
