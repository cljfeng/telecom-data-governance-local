# Engineering Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the first engineering-hardening slice from the project review: version visibility, schema migration tracking, explicit workflow transitions, and a small routing seam.

**Architecture:** Keep the existing local `http.server` and SQLite architecture. Add focused helpers around migration/version/workflow behavior so future route and rule splits can happen without changing user-facing behavior.

**Tech Stack:** Python 3.12, SQLite, openpyxl, pytest, native ES modules.

---

### Task 1: Schema Version Tracking

**Files:**
- Modify: `src/governance_app/db.py`
- Test: `tests/test_db.py`

- [ ] Add a failing test that initializes the database and asserts a `schema_migrations` table contains the current schema version.
- [ ] Run `pytest tests/test_db.py -q` and confirm the new assertion fails because the table/version is missing.
- [ ] Add `SCHEMA_VERSION`, create `schema_migrations`, and record the current version during initialization.
- [ ] Run `pytest tests/test_db.py -q` and confirm it passes.

### Task 2: Version Endpoint

**Files:**
- Create: `src/governance_app/version.py`
- Modify: `src/governance_app/server.py`
- Test: `tests/test_server.py`

- [ ] Add a failing test for `GET /api/version` asserting app version, template version, schema version, and Python version are returned.
- [ ] Run the focused server test and confirm it returns 404 before implementation.
- [ ] Add a `version_payload` helper and route `/api/version` to it.
- [ ] Run the focused server test and confirm it passes.

### Task 3: Explicit Workflow Transitions

**Files:**
- Modify: `src/governance_app/workflow.py`
- Modify: `src/governance_app/importer.py`
- Modify: `src/governance_app/audit_engine.py`
- Modify: `src/governance_app/exporter.py`
- Modify: `src/governance_app/corrections.py`
- Modify: `src/governance_app/archive.py`
- Test: `tests/test_workflow.py`

- [ ] Add tests for valid and invalid status transitions.
- [ ] Run `pytest tests/test_workflow.py -q` and confirm the new tests fail because `transition_batch` does not exist.
- [ ] Add `transition_batch` and replace scattered status updates in core workflow actions.
- [ ] Run `pytest tests/test_workflow.py -q` and confirm it passes.

### Task 4: Route Handler Seam

**Files:**
- Create: `src/governance_app/routes/__init__.py`
- Create: `src/governance_app/routes/system.py`
- Modify: `src/governance_app/server.py`
- Test: `tests/test_server.py`

- [ ] Add a route module for health/version/settings/backup/restore/reset/maintenance.
- [ ] Keep existing response shapes unchanged.
- [ ] Run `pytest tests/test_server.py -q` and confirm all server tests pass.

### Task 5: Full Verification

**Files:**
- Verify all changed files.

- [ ] Run `scripts/check.sh`.
- [ ] Inspect `git diff --stat` and `git diff --check`.
- [ ] Summarize what changed, what was verified, and any remaining recommended follow-ups.
