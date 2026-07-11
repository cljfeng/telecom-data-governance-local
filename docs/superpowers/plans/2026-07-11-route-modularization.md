# Backend Route Modularization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the monolithic HTTP route implementation into domain handlers while preserving every public API path, response shape, status code, upload behavior, and frontend flow.

**Architecture:** Keep `server.py` as the standard-library HTTP adapter and deterministic dispatcher. Move protocol helpers into `routes/common.py`, then move business route branches into chainable domain handlers that return `None` for requests outside their domain.

**Tech Stack:** Python 3.12, `http.server`, SQLite, openpyxl, pytest, native ES modules.

## Global Constraints

- Do not add runtime dependencies.
- Do not change API paths, successful response bodies, status codes, or user-facing error text.
- Do not change database, workflow, audit-rule, report, or analysis business behavior.
- Preserve `LocalApp.handle_test_request` and `LocalApp.handle_test_upload_request`.
- Use test-first red-green-refactor for each production extraction.
- A handler must return `None` for a path outside its domain.
- `server.py` must return the sole fallback `404 {"error": "not found"}`.

---

## File Structure

- Create `src/governance_app/routes/common.py`: protocol-only JSON, ID, pagination, and uploaded-file helpers.
- Create `src/governance_app/routes/batches.py`: batch, workflow, dashboard, progress, and ledger routes.
- Create `src/governance_app/routes/imports.py`: import, preview, recent-file, and import-upload routes.
- Create `src/governance_app/routes/audits.py`: audit, issue, issue-group, rule, and rule-setting routes.
- Create `src/governance_app/routes/analysis.py`: electricity and tower-rent analysis routes.
- Create `src/governance_app/routes/reports.py`: issue export, notice, correction, archive, and correction-upload routes.
- Modify `src/governance_app/routes/system.py`: use common helpers directly.
- Modify `src/governance_app/server.py`: keep HTTP adaptation and route chains only.
- Create `tests/routes/__init__.py` and focused route tests.
- Modify `tests/test_server.py` only for structural assertions and compatibility adjustments.

### Task 1: Common Protocol Helpers and System Handler

**Files:**
- Create: `src/governance_app/routes/common.py`
- Modify: `src/governance_app/routes/system.py`
- Create: `tests/routes/__init__.py`
- Create: `tests/routes/test_common.py`
- Create: `tests/routes/test_system.py`

**Interfaces:**
- Produces: `JsonResponse = tuple[int, dict[str, str], str]`
- Produces: `json_response(payload: dict, status: int = 200) -> JsonResponse`
- Produces: `json_body(body: str) -> tuple[dict, JsonResponse | None]`
- Produces: `batch_id_from_payload(payload: dict) -> tuple[int, JsonResponse | None]`
- Produces: `batch_id_from_query(query_string: str) -> tuple[int, JsonResponse | None]`
- Produces: `pagination_from_query(query: dict[str, list[str]]) -> tuple[int | None, int]`
- Produces: `save_uploaded_workbook(config, filename, content) -> Path`
- Changes: `handle_system_route(config, method, parsed, body) -> JsonResponse | None`

- [ ] **Step 1: Write failing common-helper tests**

Test JSON serialization, invalid/non-object JSON, payload/query batch IDs, pagination limits, safe uploaded filename, rejected extensions, and that `handle_system_route` returns `None` for `/api/batches`.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv/bin/python -m pytest tests/routes/test_common.py tests/routes/test_system.py -q`

Expected: collection or assertions fail because the new common module and simplified system signature do not exist.

- [ ] **Step 3: Implement `routes/common.py` by moving existing helpers unchanged**

Move `_json`, `_json_body`, `_batch_id_from_payload`, `_batch_id_from_query`, `_pagination_from_query`, and `_save_uploaded_workbook`. Rename only the leading underscores. Keep the existing 500-row pagination cap and Excel extension allowlist.

- [ ] **Step 4: Update `routes/system.py` to import common helpers**

Remove injected `json_response` and `json_body` parameters. Preserve all route branches and exception mapping.

- [ ] **Step 5: Run focused and system compatibility tests**

Run: `.venv/bin/python -m pytest tests/routes/test_common.py tests/routes/test_system.py tests/test_server.py -k 'health or version or settings or backup or restore or reset or maintenance' -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/governance_app/routes/common.py src/governance_app/routes/system.py tests/routes
git commit -m "Extract common route protocol helpers"
```

### Task 2: Analysis Route Handler

**Files:**
- Create: `src/governance_app/routes/analysis.py`
- Create: `tests/routes/test_analysis.py`
- Modify: `src/governance_app/server.py`

**Interfaces:**
- Produces: `handle_analysis_route(config, method, parsed, body) -> JsonResponse | None`
- Produces: `analysis_path(path: str) -> tuple[str, int, str] | None`

- [ ] **Step 1: Write failing handler-boundary tests**

Assert `/api/batches/1/electricity-analysis/summary` is handled, `/api/batches/bad/electricity-analysis/summary` returns 400, an invalid analysis action returns 404, and `/api/health` returns `None`.

- [ ] **Step 2: Run focused test and verify RED**

Run: `.venv/bin/python -m pytest tests/routes/test_analysis.py -q`

Expected: FAIL because `routes.analysis` is missing.

- [ ] **Step 3: Move analysis path parsing and route branches**

Move both electricity and tower-rent imports, action dispatch, filter parsing, and `ValueError` mapping into `routes/analysis.py`. The handler must ignore `body` because current analysis routes do not consume it.

- [ ] **Step 4: Replace analysis branches in `server._route` with the handler call**

Temporarily keep remaining route branches in place. Call analysis after the existing system handler.

- [ ] **Step 5: Run focused and endpoint tests**

Run: `.venv/bin/python -m pytest tests/routes/test_analysis.py tests/test_server.py -k 'analysis' -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/governance_app/routes/analysis.py src/governance_app/server.py tests/routes/test_analysis.py
git commit -m "Extract analysis routes"
```

### Task 3: Batch and Dashboard Route Handler

**Files:**
- Create: `src/governance_app/routes/batches.py`
- Create: `tests/routes/test_batches.py`
- Modify: `src/governance_app/server.py`

**Interfaces:**
- Produces: `handle_batch_route(config, method, parsed, body) -> JsonResponse | None`

- [ ] **Step 1: Write failing ownership tests**

Test representative batch list, batch creation validation, current-batch selection, workflow lookup, dashboard invalid batch ID, ledger filtering, and `None` for `/api/audit`.

- [ ] **Step 2: Run focused test and verify RED**

Run: `.venv/bin/python -m pytest tests/routes/test_batches.py -q`

Expected: FAIL because the handler is missing.

- [ ] **Step 3: Move batch-domain branches**

Move service imports and branches for `/api/batches`, `/api/batches/current`, `/api/workflow`, `/api/dashboard`, `/api/city-progress`, and `/api/ledger-rows`. Reuse common ID parsing and JSON helpers.

- [ ] **Step 4: Add the handler to the chain and remove old branches**

Call batches after system and before analysis. Remove only the migrated imports and conditions.

- [ ] **Step 5: Verify**

Run: `.venv/bin/python -m pytest tests/routes/test_batches.py tests/test_server.py -k 'batch or workflow or dashboard or ledger or city_progress' -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/governance_app/routes/batches.py src/governance_app/server.py tests/routes/test_batches.py
git commit -m "Extract batch routes"
```

### Task 4: Import and Import-Upload Handler

**Files:**
- Create: `src/governance_app/routes/imports.py`
- Create: `tests/routes/test_imports.py`
- Modify: `src/governance_app/server.py`

**Interfaces:**
- Produces: `handle_import_route(config, method, parsed, body) -> JsonResponse | None`
- Produces: `handle_import_upload(config, path, fields, files) -> JsonResponse | None`

- [ ] **Step 1: Write failing route and upload ownership tests**

Cover JSON preview, JSON import, recent files, successful preview upload, missing upload file, invalid extension, operation conflict for formal import, and `None` for correction upload.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv/bin/python -m pytest tests/routes/test_imports.py -q`

Expected: FAIL because import handlers are missing.

- [ ] **Step 3: Move preview/import response assembly and import locking**

Move `_preview_from_payload` and `_import_from_payload` as private helpers inside `routes/imports.py`. Preserve openpyxl exception handling and 409 conflict behavior.

- [ ] **Step 4: Move import upload handling**

Use `save_uploaded_workbook` from common. Return `None` for paths outside the two import-upload endpoints.

- [ ] **Step 5: Wire both chains and remove old server branches**

Call import ordinary routes after batches. Call import upload first in `_route_upload`.

- [ ] **Step 6: Verify**

Run: `.venv/bin/python -m pytest tests/routes/test_imports.py tests/test_import_preview.py tests/test_importer.py tests/test_server.py -k 'import or upload or preview or recent' -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/governance_app/routes/imports.py src/governance_app/server.py tests/routes/test_imports.py
git commit -m "Extract import routes"
```

### Task 5: Audit, Issue, and Rule Handler

**Files:**
- Create: `src/governance_app/routes/audits.py`
- Create: `tests/routes/test_audits.py`
- Modify: `src/governance_app/server.py`

**Interfaces:**
- Produces: `handle_audit_route(config, method, parsed, body) -> JsonResponse | None`

- [ ] **Step 1: Write failing ownership tests**

Cover audit execution, operation conflict, paginated issues, issue groups, rule list, invalid rule setting, issue status, group status, and `None` for `/api/export`.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv/bin/python -m pytest tests/routes/test_audits.py -q`

Expected: FAIL because the audit handler is missing.

- [ ] **Step 3: Move route branches and private rule helpers**

Move `_rule_settings_payload`, `_rule_effectiveness_by_rule`, `_empty_rule_effectiveness`, `_rule_tuning_recommendation`, `_RULE_PARAMETERS`, and `_rule_parameters` unchanged into `routes/audits.py`.

- [ ] **Step 4: Wire the handler and remove old imports/helpers**

Call audits after imports and before analysis. Keep audit execution guarded by `exclusive_operation`.

- [ ] **Step 5: Verify**

Run: `.venv/bin/python -m pytest tests/routes/test_audits.py tests/test_audit_engine.py tests/test_workflow.py tests/test_server.py -k 'audit or issue or rule' -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/governance_app/routes/audits.py src/governance_app/server.py tests/routes/test_audits.py
git commit -m "Extract audit and issue routes"
```

### Task 6: Reports, Corrections, and Archive Handler

**Files:**
- Create: `src/governance_app/routes/reports.py`
- Create: `tests/routes/test_reports.py`
- Modify: `src/governance_app/server.py`

**Interfaces:**
- Produces: `handle_report_route(config, method, parsed, body) -> JsonResponse | None`
- Produces: `handle_report_upload(config, path, fields, files) -> JsonResponse | None`

- [ ] **Step 1: Write failing ownership tests**

Cover issue-package export, notice export, correction JSON import, correction upload, archive conflict/error mapping, archive precheck, and `None` for import upload.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv/bin/python -m pytest tests/routes/test_reports.py -q`

Expected: FAIL because report handlers are missing.

- [ ] **Step 3: Move ordinary report branches**

Move exporter, notice, correction, archive, and precheck imports and branches. Preserve existing status selection and error responses.

- [ ] **Step 4: Move correction upload handling**

Use the common uploaded-workbook helper and retain the current `matched_count`, `errors`, `review_warnings`, and `auto_review` response.

- [ ] **Step 5: Wire both chains and remove old branches**

Call reports last in the ordinary chain and second in the upload chain.

- [ ] **Step 6: Verify**

Run: `.venv/bin/python -m pytest tests/routes/test_reports.py tests/test_export_and_corrections.py tests/test_analytics_backup.py tests/test_server.py -k 'export or correction or archive or notice' -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/governance_app/routes/reports.py src/governance_app/server.py tests/routes/test_reports.py
git commit -m "Extract report and archive routes"
```

### Task 7: Final Dispatcher and Structural Guardrails

**Files:**
- Modify: `src/governance_app/server.py`
- Modify: `tests/test_server.py`
- Create: `tests/routes/test_dispatch.py`

**Interfaces:**
- `_route` iterates the exact ordinary handler tuple.
- `_route_upload` iterates the exact upload handler tuple.
- Both return the common 404 only after the chain is exhausted.

- [ ] **Step 1: Write failing structural tests**

Read `server.py` and assert it does not import `analytics`, `archive`, `audit_engine`, `corrections`, `electricity_analysis`, `exporter`, `importer`, `rule_settings`, `tower_rent_analysis`, or `workflow`. Assert handler tuples have the specified order and unknown ordinary/upload paths return the same JSON 404.

- [ ] **Step 2: Run structural tests and verify RED**

Run: `.venv/bin/python -m pytest tests/routes/test_dispatch.py -q`

Expected: FAIL until all residual business imports and branches are removed.

- [ ] **Step 3: Reduce `server.py` to adapter and dispatcher responsibilities**

Delete migrated helpers and unused imports. Use `json_response` from common for fallback and request-length errors. Keep Multipart parsing, request handler, static headers, server start, and CLI unchanged.

- [ ] **Step 4: Run full server and end-to-end tests**

Run: `.venv/bin/python -m pytest tests/routes tests/test_server.py tests/test_end_to_end.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/governance_app/server.py tests/test_server.py tests/routes/test_dispatch.py
git commit -m "Finalize domain route dispatcher"
```

### Task 8: Full Verification

**Files:**
- Verify all changed files.

- [ ] **Step 1: Run complete quality gate**

Run: `scripts/check.sh`

Expected: all tests pass; Python compilation, every JavaScript module, and both Bash scripts exit 0.

- [ ] **Step 2: Run compatibility groups independently**

Run: `.venv/bin/python -m pytest tests/routes tests/test_server.py tests/test_end_to_end.py tests/test_export_and_corrections.py -q`

Expected: PASS.

- [ ] **Step 3: Inspect diff and repository state**

Run: `git diff main...HEAD --check`

Expected: no whitespace errors.

Run: `git status --short --branch`

Expected: clean feature branch.

- [ ] **Step 4: Requirements review**

Confirm each API domain has one handler module, each handler returns `None` outside its domain, upload routing remains split, the fallback 404 is centralized, `server.py` has no business-service imports, and all existing compatibility tests pass.

- [ ] **Step 5: Independent code review**

Request review against the design and plan. Fix every critical or important compatibility issue, then rerun the complete quality gate.
