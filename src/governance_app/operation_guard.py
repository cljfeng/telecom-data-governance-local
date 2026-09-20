from contextlib import contextmanager
from threading import Lock
from typing import Iterator

from governance_app.config import AppConfig, RuntimeMode
from governance_app.database_runtime import database_for


class OperationConflict(ValueError):
    pass


_registry_lock = Lock()
_workspace_locks: dict[str, Lock] = {}


@contextmanager
def exclusive_operation(config: AppConfig, operation: str) -> Iterator[None]:
    key = str(config.workspace_dir.resolve())
    if config.runtime_mode is RuntimeMode.ONLINE:
        with database_for(config).operation_lock(
            f"governance:{operation}:{key}"
        ) as acquired:
            if not acquired:
                raise OperationConflict(
                    "系统正在执行其他数据操作，请稍后重试"
                )
            yield
        return
    with _registry_lock:
        lock = _workspace_locks.setdefault(key, Lock())
    if not lock.acquire(blocking=False):
        raise OperationConflict("系统正在执行其他数据操作，请稍后重试")
    try:
        yield
    finally:
        lock.release()
