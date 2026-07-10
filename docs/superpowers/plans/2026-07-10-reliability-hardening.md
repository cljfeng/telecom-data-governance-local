# Reliability Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make database upgrades, backup/restore, repeated audits, heavy operations, uploads, and pull-request checks safe and deterministic without changing the local single-machine product architecture.

**Architecture:** Keep the current SQLite and standard-library HTTP stack. Add a small versioned migration runner, SQLite-native backup primitives, an audit issue synchronizer, and a workspace-scoped in-process operation coordinator; integrate them at existing service and route seams while preserving successful API shapes.

**Tech Stack:** Python 3.12, SQLite, openpyxl, pytest, native ES modules, Bash, GitHub Actions.

## Global Constraints

- Preserve existing successful API response shapes and Excel formats.
- Preserve Python standard-library HTTP, SQLite, native ES modules, and PyInstaller delivery.
- Do not add runtime dependencies.
- Use test-first red-green-refactor for every production behavior.
- Keep existing version-1 data readable and upgrade it automatically.
- Reject databases newer than the running application.
- Use a 100 MiB HTTP request-body limit.

---

## File Structure

- Create `src/governance_app/migrations.py`: ordered migration definitions, schema-version inspection, and transactional application.
- Modify `src/governance_app/db.py`: connection pragmas and initialization orchestration only.
- Modify `src/governance_app/backup.py`: SQLite-native backup, integrity checking, and restore primitives.
- Modify `src/governance_app/settings_service.py`: safe restore workflow and rollback.
- Create `src/governance_app/operation_guard.py`: workspace-scoped non-blocking operation lock.
- Modify `src/governance_app/audit_engine.py`: current-issue upsert and missing-issue resolution.
- Modify `src/governance_app/electricity_analysis.py`: ignore issues resolved by re-audit.
- Modify `src/governance_app/tower_rent_analysis.py`: ignore issues resolved by re-audit.
- Modify `src/governance_app/workflow.py`: allow and label the new issue status and record manual issue events.
- Modify `src/governance_app/server.py`: guarded heavy routes and request-length validation.
- Modify `src/governance_app/routes/system.py`: guarded backup/restore/reset/maintenance routes and deterministic error mapping.
- Modify `scripts/check.sh`: discover all Python and JavaScript modules and check both Bash scripts.
- Create `.github/workflows/quality.yml`: PR and main-branch quality gate.
- Modify `pyproject.toml`, `src/governance_app/version.py`, and `README.md`: publish version 0.2.0 and document upgrade safety.

### Task 1: Versioned Schema Migration Runner

**Files:**
- Create: `src/governance_app/migrations.py`
- Modify: `src/governance_app/db.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Produces: `SCHEMA_VERSION: int = 2`
- Produces: `current_schema_version(conn: sqlite3.Connection) -> int`
- Produces: `apply_migrations(conn: sqlite3.Connection, migrations: tuple[Migration, ...] = MIGRATIONS) -> None`
- Consumes: `initialize_database(config)` remains the public initialization entry point.

- [ ] **Step 1: Write failing tests for fresh version 2 schema**

Add assertions to `tests/test_db.py`:

```python
from governance_app.db import SCHEMA_VERSION, connect, initialize_database


def test_initialize_database_applies_current_schema(app_config):
    initialize_database(app_config)
    with connect(app_config) as conn:
        versions = [row["version"] for row in conn.execute("select version from schema_migrations order by version")]
        issue_columns = {row["name"] for row in conn.execute("pragma table_info(issues)")}
        event_columns = {row["name"] for row in conn.execute("pragma table_info(issue_events)")}
    assert versions == list(range(1, SCHEMA_VERSION + 1))
    assert {"resolved_at", "last_seen_audit_run_id"}.issubset(issue_columns)
    assert {"issue_id", "from_status", "to_status", "source", "note", "created_at"}.issubset(event_columns)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `.venv/bin/python -m pytest tests/test_db.py::test_initialize_database_applies_current_schema -q`

Expected: FAIL because `SCHEMA_VERSION` is 1 and the new columns/table do not exist.

- [ ] **Step 3: Add the migration runner and move current schema creation into migration 1**

Implement the focused API in `migrations.py`:

```python
@dataclass(frozen=True)
class Migration:
    version: int
    apply: Callable[[sqlite3.Connection], None]


SCHEMA_VERSION = 2
MIGRATIONS = (Migration(1, _create_version_1_schema), Migration(2, _upgrade_to_version_2))


def apply_migrations(conn, migrations=MIGRATIONS):
    current = current_schema_version(conn)
    latest = migrations[-1].version
    if current > latest:
        raise RuntimeError(f"数据库版本 {current} 高于应用支持版本 {latest}，请使用更新版本的程序")
    for migration in migrations:
        if migration.version <= current:
            continue
        with conn:
            migration.apply(conn)
            conn.execute("insert into schema_migrations(version) values (?)", (migration.version,))
```

Migration 1 must reproduce the existing schema and indexes. Migration 2 must add `issues.resolved_at`, `issues.last_seen_audit_run_id`, create `issue_events`, and add an index on `(batch_id, status)` where useful.

- [ ] **Step 4: Make `db.py` delegate initialization and add connection wait behavior**

Keep `connect()` public, set `PRAGMA foreign_keys = on` and `PRAGMA busy_timeout = 5000`, and have `initialize_database()` call `apply_migrations(conn)`.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_db.py -q`

Expected: all database tests PASS.

- [ ] **Step 6: Add and verify legacy, idempotency, rollback, and future-version tests**

Create a version-1 database by applying only `MIGRATIONS[:1]`, insert a named batch, run `initialize_database`, and assert the row remains and version 2 exists. Add a local failing migration callable that creates a table and raises; assert neither the table nor version is persisted. Insert version `SCHEMA_VERSION + 1` and assert initialization raises the Chinese version incompatibility error.

Run: `.venv/bin/python -m pytest tests/test_db.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the migration runner**

```bash
git add src/governance_app/migrations.py src/governance_app/db.py tests/test_db.py
git commit -m "Add versioned database migrations"
```

### Task 2: SQLite-Native Backup and Safe Restore

**Files:**
- Modify: `src/governance_app/backup.py`
- Modify: `src/governance_app/settings_service.py`
- Modify: `src/governance_app/db.py`
- Modify: `tests/test_analytics_backup.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Produces: `check_database_integrity(path: Path) -> None`, raising `ValueError` unless SQLite reports `ok`.
- Produces: `database_schema_version(path: Path) -> int`.
- Preserves: `create_backup(config: AppConfig) -> Path`.
- Preserves: `restore_backup(config: AppConfig, backup_path: Path) -> None`.
- Preserves: `restore_backup_safely(config, backup_path) -> tuple[Path, str]`.

- [ ] **Step 1: Write failing integrity and corrupt-restore tests**

Add tests that call `create_backup`, then `check_database_integrity`, and a test that writes `b"not sqlite"` inside `backups/`, calls `restore_backup_safely`, expects `ValueError`, and asserts the active batch count is unchanged.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_analytics_backup.py -q`

Expected: FAIL because integrity checking does not exist and corrupt sources are copied directly.

- [ ] **Step 3: Implement SQLite backup and integrity primitives**

Use source and destination `sqlite3.Connection` objects:

```python
with sqlite3.connect(config.database_path) as source, sqlite3.connect(backup_path) as target:
    source.backup(target)
check_database_integrity(backup_path)
```

`check_database_integrity` must open the database, run `pragma integrity_check`, require exactly `ok`, and normalize `sqlite3.DatabaseError` to `ValueError("备份数据库完整性校验失败")`.

- [ ] **Step 4: Implement safe restore ordering and rollback**

Validate path containment and source integrity before creating the safety backup. Reject source versions above `SCHEMA_VERSION`. Restore with SQLite Backup API. If post-restore initialization fails, restore the safety backup and re-raise a `ValueError` that reports the rollback.

- [ ] **Step 5: Add pre-migration backup behavior**

In `initialize_database`, when an existing non-empty database has a supported version below `SCHEMA_VERSION`, call `create_backup` before `apply_migrations`. Avoid recursion by keeping backup primitives independent from `initialize_database`.

- [ ] **Step 6: Verify upgrade backup behavior**

Add a version-1 database test that records `backups/` before and after initialization and asserts exactly one valid pre-upgrade backup was added. Assert fresh initialization does not create a backup.

Run: `.venv/bin/python -m pytest tests/test_db.py tests/test_analytics_backup.py -q`

Expected: PASS.

- [ ] **Step 7: Commit backup safety**

```bash
git add src/governance_app/backup.py src/governance_app/settings_service.py src/governance_app/db.py tests/test_db.py tests/test_analytics_backup.py
git commit -m "Harden database backup and restore"
```

### Task 3: Repeated Audit Issue Synchronization

**Files:**
- Modify: `src/governance_app/audit_engine.py`
- Modify: `src/governance_app/workflow.py`
- Modify: `src/governance_app/electricity_analysis.py`
- Modify: `src/governance_app/tower_rent_analysis.py`
- Modify: `tests/test_audit_engine.py`
- Modify: `tests/test_workflow.py`
- Modify: `tests/test_electricity_analysis.py`
- Modify: `tests/test_tower_rent_analysis.py`

**Interfaces:**
- Produces: `_sync_issue(..., audit_run_id: int) -> str`, returning `created`, `updated`, or `reopened`.
- Produces: `_resolve_missing_issues(conn, batch_id: int, audit_run_id: int, seen_codes: set[str]) -> int`.
- Extends: `ISSUE_STATUSES` with `resolved_by_reaudit`.

- [ ] **Step 1: Write a failing repeated-hit test**

Import a workbook, force an electricity rule hit, run audit, capture `issues.audit_result_id`, edit its manual `status`, `correction_value`, and `correction_note`, run audit again, and assert:

```python
assert issue["audit_result_id"] != first_audit_result_id
assert issue["last_seen_audit_run_id"] == second.audit_run_id
assert issue["status"] == "needs_review"
assert issue["correction_value"] == "0.8"
assert issue["correction_note"] == "已核查"
```

- [ ] **Step 2: Run the test and verify RED**

Run: `.venv/bin/python -m pytest tests/test_audit_engine.py -k repeated -q`

Expected: FAIL because `insert or ignore` leaves the old result link.

- [ ] **Step 3: Replace `insert or ignore` with explicit issue synchronization**

For each finding, insert its `audit_result`, compute the stable code, query the existing issue, and then create, update, or reopen it. Add a `seen_codes` set in `run_audit`. After rule evaluation, call `_resolve_missing_issues` before transitioning the batch.

Event rows must contain the prior status, new status, source (`audit`, `reaudit_resolve`, or `reaudit_reopen`), and a concise note.

- [ ] **Step 4: Add resolution and reopen tests**

After the first hit, change the source row so the rule no longer hits and run audit again. Assert status `resolved_by_reaudit`, non-null `resolved_at`, and one resolution event. Restore the bad source value and run again; assert `pending_export`, null `resolved_at`, and one reopen event.

- [ ] **Step 5: Verify manual history preservation and archived rejection**

Assert correction fields survive both continued hits and reopen. Add an archived batch test and expect `ValueError("batch is archived")` before a new `audit_run` is inserted.

- [ ] **Step 6: Exclude resolved issues from workflows and analyses**

Add `i.status <> 'resolved_by_reaudit'` to electricity and tower-rent `_issue_rows` queries and to counts representing current/open issues. Keep historical exports and operation logs intact. Add focused analysis tests proving resolved issues generate zero opportunities.

- [ ] **Step 7: Run focused and regression tests**

Run: `.venv/bin/python -m pytest tests/test_audit_engine.py tests/test_workflow.py tests/test_electricity_analysis.py tests/test_tower_rent_analysis.py -q`

Expected: PASS.

- [ ] **Step 8: Commit repeated-audit semantics**

```bash
git add src/governance_app/audit_engine.py src/governance_app/workflow.py src/governance_app/electricity_analysis.py src/governance_app/tower_rent_analysis.py tests/test_audit_engine.py tests/test_workflow.py tests/test_electricity_analysis.py tests/test_tower_rent_analysis.py
git commit -m "Synchronize issues across repeated audits"
```

### Task 4: Workspace-Scoped Heavy Operation Guard

**Files:**
- Create: `src/governance_app/operation_guard.py`
- Modify: `src/governance_app/server.py`
- Modify: `src/governance_app/routes/system.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Produces: `OperationConflict(ValueError)`.
- Produces: `exclusive_operation(config: AppConfig, operation: str) -> ContextManager[None]`.
- Produces: `run_exclusive(config, operation, callable, *args, **kwargs)` route helper.

- [ ] **Step 1: Write failing lock conflict and release tests**

Hold `exclusive_operation(app_config, "test")`, call `POST /api/audit`, and assert status 409 with the specified Chinese message. Add a direct context-manager test whose body raises; after catching it, acquiring the same workspace lock again must succeed.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_server.py -k 'operation_conflict or operation_lock' -q`

Expected: FAIL because no guard exists.

- [ ] **Step 3: Implement the coordinator**

Use a module-level dictionary keyed by `str(config.workspace_dir.resolve())`, protected by a short registry lock. Each workspace value is a `threading.Lock`; acquire with `blocking=False`, raise `OperationConflict` on failure, and release in `finally`.

- [ ] **Step 4: Guard heavy routes and normalize conflict responses**

Wrap formal import (including upload import), audit, restore, reset, compact, and archive calls. Do not guard preview, read routes, or exports. Catch only `OperationConflict` at the route boundary and return HTTP 409; retain existing `ValueError` behavior.

- [ ] **Step 5: Run focused and full server tests**

Run: `.venv/bin/python -m pytest tests/test_server.py -q`

Expected: PASS.

- [ ] **Step 6: Commit operation coordination**

```bash
git add src/governance_app/operation_guard.py src/governance_app/server.py src/governance_app/routes/system.py tests/test_server.py
git commit -m "Serialize heavy workspace operations"
```

### Task 5: HTTP Request Size Guard

**Files:**
- Modify: `src/governance_app/server.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Produces: `MAX_REQUEST_BODY_BYTES = 100 * 1024 * 1024`.
- Produces: `_content_length(value: str | None) -> tuple[int | None, JsonResponse | None]`.

- [ ] **Step 1: Write failing length-parser tests**

Assert a normal integer returns its length, missing/invalid/negative values return HTTP 400, and `MAX_REQUEST_BODY_BYTES + 1` returns HTTP 413 with “100 MiB”.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_server.py -k content_length -q`

Expected: FAIL because the parser and limit do not exist.

- [ ] **Step 3: Implement validation before `rfile.read`**

At the start of API `do_POST`, call `_content_length(self.headers.get("content-length"))`; if it returns an error, write the JSON response and return without calling `self.rfile.read`. Otherwise read exactly the validated length.

- [ ] **Step 4: Add a handler-level no-read regression test**

Construct a minimal handler fixture with an input stream whose `read` raises `AssertionError`; send an oversized content length and assert the response status is 413 without triggering the stream read.

- [ ] **Step 5: Run server tests**

Run: `.venv/bin/python -m pytest tests/test_server.py -q`

Expected: PASS.

- [ ] **Step 6: Commit upload protection**

```bash
git add src/governance_app/server.py tests/test_server.py
git commit -m "Limit HTTP request body size"
```

### Task 6: Complete Local and Pull-Request Quality Gates

**Files:**
- Modify: `scripts/check.sh`
- Create: `.github/workflows/quality.yml`
- Modify: `tests/test_server.py`

**Interfaces:**
- `scripts/check.sh` remains the single local and CI quality command.

- [ ] **Step 1: Add a regression test for static module discovery**

Add a test that reads `scripts/check.sh`, asserts it does not enumerate individual JS filenames, and asserts it contains a loop over `src/governance_app/static/*.js`, `compileall`, and checks for both `start.sh` and `build_app.sh`.

- [ ] **Step 2: Run the test and verify RED**

Run: `.venv/bin/python -m pytest tests/test_server.py -k check_script -q`

Expected: FAIL because the current script enumerates only five JS modules.

- [ ] **Step 3: Generalize `scripts/check.sh`**

Use:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src
for module in src/governance_app/static/*.js; do
  node --check "$module"
done
bash -n scripts/start.sh
bash -n scripts/build_app.sh
```

- [ ] **Step 4: Add the GitHub Actions quality workflow**

Configure `pull_request` and pushes to `main`, use `actions/checkout@v4`, `actions/setup-python@v5` with Python 3.12 and pip cache, `actions/setup-node@v4` with Node 22, install `.[test]`, then run `scripts/check.sh`.

- [ ] **Step 5: Run the quality script**

Run: `scripts/check.sh`

Expected: all pytest tests pass and all compile/syntax checks exit 0.

- [ ] **Step 6: Commit quality gates**

```bash
git add scripts/check.sh .github/workflows/quality.yml tests/test_server.py
git commit -m "Add pull request quality gate"
```

### Task 7: Publish Version 0.2.0 and Upgrade Documentation

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/governance_app/version.py`
- Modify: `src/governance_app/settings_service.py`
- Modify: `README.md`
- Modify: `tests/test_server.py`

**Interfaces:**
- `GET /api/version` returns `app_version = 0.2.0` and `schema_version = 2`.
- `GET /api/settings` uses the shared `TEMPLATE_VERSION` constant.

- [ ] **Step 1: Write failing version and shared-template tests**

Update the version endpoint test to expect `0.2.0` and schema version 2. Add an assertion that settings and version endpoints return the same template version.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_server.py -k 'version or settings' -q`

Expected: FAIL because package version remains 0.1.0.

- [ ] **Step 3: Update version sources without duplication**

Set project version to `0.2.0`. Keep `TEMPLATE_VERSION` in `version.py` as the single template constant and import it from `settings_service.py`.

- [ ] **Step 4: Document automatic upgrades and recovery**

Add a README section explaining that existing workspaces are backed up and migrated at startup, backups are integrity checked, newer-schema databases require a newer executable, and `backups/` must be retained during upgrades.

- [ ] **Step 5: Run focused tests**

Run: `.venv/bin/python -m pytest tests/test_server.py -k 'version or settings' -q`

Expected: PASS.

- [ ] **Step 6: Commit release metadata**

```bash
git add pyproject.toml src/governance_app/version.py src/governance_app/settings_service.py README.md tests/test_server.py
git commit -m "Publish reliability hardening version"
```

### Task 8: Full Verification

**Files:**
- Verify all changed files.

- [ ] **Step 1: Run the complete quality gate**

Run: `scripts/check.sh`

Expected: all tests pass; Python compilation, every JavaScript module, and both Bash scripts exit 0.

- [ ] **Step 2: Run high-risk test groups independently**

Run: `.venv/bin/python -m pytest tests/test_db.py tests/test_analytics_backup.py tests/test_audit_engine.py tests/test_server.py -q`

Expected: PASS.

- [ ] **Step 3: Inspect repository changes**

Run: `git diff --check HEAD~7..HEAD`

Expected: no whitespace errors.

Run: `git status --short`

Expected: clean after planned commits.

- [ ] **Step 4: Review the requirements against the design**

Confirm migration safety, corrupt-backup rejection, issue synchronization, 409 conflict handling, 413 pre-read rejection, complete quality discovery, and version 0.2.0 each have a passing test and implementation.

- [ ] **Step 5: Prepare completion summary**

Report the exact verification commands, test count, commits, remaining limitations, and recommend a real Windows artifact smoke test in GitHub Actions.
