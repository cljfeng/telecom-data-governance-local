# Audit Rule Modularization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split audit rule implementations by business domain while preserving the `governance_app.audit_rules` API and producing byte-for-byte compatible audit findings for the same inputs.

**Architecture:** Keep explicit ordered rule factories. Domain modules own implementations, `rules/factories.py` owns stateless reusable factories, and `audit_rules.py` remains the stable facade that assembles rules in the historical order.

**Tech Stack:** Python 3.12, dataclasses, SQLite, pytest.

## Global Constraints

- Preserve every rule ID, order, ledger type, severity, threshold, field, message, and suggestion.
- Preserve public imports from `governance_app.audit_rules`.
- Do not add, remove, enable, disable, or tune rules.
- Do not change stable issue codes or analysis outputs.
- Domain modules must not import `audit_rules.py`.
- Use test-first red-green-refactor for structural boundaries.

---

### Task 1: Lock the Compatibility Snapshot

**Files:**
- Create: `tests/rules/__init__.py`
- Create: `tests/rules/test_rule_snapshot.py`

**Interfaces:**
- Snapshot: ordered `(rule_id, ledger_type, severity)` tuples for `all_rules()` and `all_batch_rules()`.
- Snapshot: representative finding payloads for site, rent, generator, electricity, and cross-ledger rules.

- [ ] Write tests with the exact current ordered rule tuples and representative findings.
- [ ] Run `.venv/bin/python -m pytest tests/rules/test_rule_snapshot.py -q` and verify the snapshot passes against the pre-refactor facade.
- [ ] Commit the characterization tests.

### Task 2: Extract Stateless Rule Factories

**Files:**
- Create: `src/governance_app/rules/factories.py`
- Create: `tests/rules/test_factories.py`
- Modify: `src/governance_app/audit_rules.py`

**Interfaces:**
- `required(field_name, message, suggestion)`
- `number_range(...)`, `optional_number_range(...)`, `number_above(...)`, `greater_than_zero(...)`
- `duplicate_positive_fee(field_name, short_name, message)`
- `inconsistent_in_group(group_fields, value_fields, short_name, message, suggestion)`

- [ ] Write failing direct factory tests for empty, boundary, duplicate, and inconsistent inputs.
- [ ] Move implementations without changing expressions or messages.
- [ ] Update the facade temporarily to import factory names.
- [ ] Run factory and snapshot tests; commit.

### Task 3: Extract Site and Cross-Ledger Rules

**Files:**
- Create: `src/governance_app/rules/site.py`
- Create: `src/governance_app/rules/cross_ledger.py`
- Create: `tests/rules/test_site_rules.py`
- Create: `tests/rules/test_cross_ledger_rules.py`
- Modify: `src/governance_app/audit_rules.py`

**Interfaces:**
- `site_rules(thresholds) -> list[AuditRule]`
- `site_batch_rules(thresholds) -> list[BatchAuditRule]`
- `cross_ledger_batch_rules(thresholds) -> list[BatchAuditRule]`

- [ ] Write failing ownership tests asserting exact rule ID sets and no foreign-domain IDs.
- [ ] Move site required rules, duplicate-name, master-site, negative amount, period spike, paid-without-master, and name-mismatch implementations.
- [ ] Assemble them through the facade while retaining the global order.
- [ ] Run site, cross-ledger, snapshot, audit-engine, and end-to-end tests; commit.

### Task 4: Extract Tower-Rent Rules

**Files:**
- Create: `src/governance_app/rules/tower_rent.py`
- Create: `tests/rules/test_tower_rent_rules.py`
- Modify: `src/governance_app/audit_rules.py`

**Interfaces:**
- `tower_rent_rules(thresholds) -> list[AuditRule]`
- `tower_rent_batch_rules(thresholds) -> list[BatchAuditRule]`

- [ ] Write failing ownership and representative finding tests.
- [ ] Move height, product, sharing, duplicate fee, discount, ownership, and stopped-site rules unchanged.
- [ ] Preserve global order through ID-based facade assembly.
- [ ] Run rent, snapshot, audit-engine, and tower analysis tests; commit.

### Task 5: Extract Generator Rules and Complete Electricity Entry

**Files:**
- Create: `src/governance_app/rules/generator.py`
- Create: `tests/rules/test_generator_rules.py`
- Modify: `src/governance_app/rules/electricity.py`
- Modify: `tests/test_audit_engine.py`
- Modify: `src/governance_app/audit_rules.py`

**Interfaces:**
- `generator_rules(thresholds) -> list[AuditRule]`
- `generator_batch_rules(thresholds) -> list[BatchAuditRule]`
- `electricity_rules(thresholds) -> list[AuditRule]`

- [ ] Write failing ownership tests for generator and electricity row rules.
- [ ] Move generator duration, responsibility, date, work-order, duration mismatch, and hourly-cost rules.
- [ ] Add electricity row-level entry using the extracted numeric factories.
- [ ] Run generator, electricity, snapshot, audit-engine, and analysis tests; commit.

### Task 6: Reduce `audit_rules.py` to the Compatibility Facade

**Files:**
- Modify: `src/governance_app/audit_rules.py`
- Create: `tests/rules/test_structure.py`

**Interfaces:**
- Preserve `DEFAULT_THRESHOLDS`, `parse_row`, `rule_metadata`, `all_rules`, `all_batch_rules`, and type re-exports.
- Add fixed `_ROW_RULE_ORDER` and `_BATCH_RULE_ORDER` ID tuples.

- [ ] Write failing structural tests prohibiting domain implementation prefixes and reverse imports.
- [ ] Delete all migrated implementations and unused field/helper imports.
- [ ] Assemble domain results by ID and return exact fixed order; fail loudly on missing or duplicate IDs.
- [ ] Verify public import compatibility and fresh-list behavior.
- [ ] Run `tests/rules`, `tests/test_audit_engine.py`, and `tests/test_end_to_end.py`; commit.

### Task 7: Full Verification and Review

**Files:**
- Verify all changed files.

- [ ] Run `scripts/check.sh`; expect all tests and syntax checks to pass.
- [ ] Run `.venv/bin/python -m pytest tests/rules tests/test_audit_engine.py tests/test_electricity_analysis.py tests/test_tower_rent_analysis.py tests/test_end_to_end.py -q`.
- [ ] Run `git diff main...HEAD --check` and confirm a clean worktree.
- [ ] Compare rule snapshots and representative findings against the pre-refactor characterization tests.
- [ ] Request independent review for ordering, hidden dependency, threshold, message, and circular-import regressions.
- [ ] Fix all critical or important findings and rerun the full quality gate.
