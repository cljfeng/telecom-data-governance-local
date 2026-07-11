# Engineering Quality Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add enforceable Ruff, coverage, and staged mypy checks to the shared local/CI quality gate without changing application behavior.

**Architecture:** Store all tool policy in `pyproject.toml`; keep `scripts/check.sh` as the single executable quality entry point used by developers and CI. Ruff covers all Python source/tests, coverage covers the full `governance_app` package, and mypy starts at stable rule and route boundaries with untyped function bodies checked.

**Tech Stack:** Python 3.12, pytest 8, pytest-cov 6+, Coverage.py, Ruff 0.11+, mypy 1.15+, TOML, GitHub Actions.

## Global Constraints

- Do not change runtime behavior, HTTP response payloads, database schema, audit thresholds, or analysis algorithms.
- Do not run or configure Ruff formatter.
- Ruff checks `src` and `tests` with `E4`, `E7`, `E9`, `F`, `I`, and `B`.
- Coverage includes the complete `governance_app` package and excludes no production modules.
- Coverage `fail_under` is the largest multiple of 5 not exceeding the pre-change measured percentage; if measured coverage is below 70%, use its integer floor.
- mypy checks only the stable rule and route boundaries listed in the design and must not use `ignore_errors = true`.
- Local and CI checks use `scripts/check.sh` as the single entry point.

---

### Task 1: Lock the Quality-Gate Contract

**Files:**
- Create: `tests/test_quality_gate.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: Python 3.12 `tomllib` and repository text files.
- Produces: executable configuration for `ruff`, `mypy`, `pytest-cov`, and Coverage.py.

- [ ] **Step 1: Write failing configuration contract tests**

Create tests that load `pyproject.toml` and assert:

```python
def test_test_dependencies_include_quality_tools():
    dependencies = PROJECT["project"]["optional-dependencies"]["test"]
    assert any(item.startswith("pytest-cov>=6.0") for item in dependencies)
    assert any(item.startswith("ruff>=0.11") for item in dependencies)
    assert any(item.startswith("mypy>=1.15") for item in dependencies)


def test_ruff_policy_is_high_value_and_non_formatting():
    assert PROJECT["tool"]["ruff"]["target-version"] == "py312"
    assert PROJECT["tool"]["ruff"]["lint"]["select"] == ["E4", "E7", "E9", "F", "I", "B"]
    assert "format" not in PROJECT["tool"]["ruff"]


def test_mypy_policy_targets_stable_boundaries():
    mypy = PROJECT["tool"]["mypy"]
    assert mypy["python_version"] == "3.12"
    assert mypy["check_untyped_defs"] is True
    assert mypy["no_implicit_optional"] is True
    assert mypy["warn_unused_ignores"] is True
    assert mypy["warn_redundant_casts"] is True
    assert mypy["warn_return_any"] is True
    assert mypy["ignore_missing_imports"] is True
    assert mypy["files"] == EXPECTED_MYPY_FILES
    assert "ignore_errors" not in mypy


def test_coverage_policy_covers_the_package_and_has_a_gate():
    assert PROJECT["tool"]["coverage"]["run"]["source"] == ["governance_app"]
    assert PROJECT["tool"]["coverage"]["report"]["show_missing"] is True
```

- [ ] **Step 2: Run the contract tests and verify red**

Run:

```bash
.venv/bin/python -m pytest tests/test_quality_gate.py -q
```

Expected: failures because the dependencies and tool configuration are absent.

- [ ] **Step 3: Add test-only dependencies and initial tool configuration**

Add the three dependencies and these sections to `pyproject.toml`:

```toml
[tool.ruff]
target-version = "py312"

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F", "I", "B"]

[tool.mypy]
python_version = "3.12"
files = [
  "src/governance_app/rule_types.py",
  "src/governance_app/rule_fields.py",
  "src/governance_app/rule_helpers.py",
  "src/governance_app/audit_rules.py",
  "src/governance_app/rules",
  "src/governance_app/routes",
]
check_untyped_defs = true
no_implicit_optional = true
warn_unused_ignores = true
warn_redundant_casts = true
warn_return_any = true
ignore_missing_imports = true

[tool.coverage.run]
source = ["governance_app"]

[tool.coverage.report]
show_missing = true
exclude_also = ["if __name__ == .__main__.:"]
```

Do not add `fail_under` or pytest coverage `addopts` until the untouched baseline is measured in Task 2.

- [ ] **Step 4: Install the updated test extra**

Run:

```bash
.venv/bin/python -m pip install -e ".[test]"
```

Expected: Ruff, mypy, pytest-cov, and Coverage.py are installed in `.venv`.

- [ ] **Step 5: Commit the red contract and tool declarations**

```bash
git add pyproject.toml tests/test_quality_gate.py
git commit -m "Declare Python quality tools"
```

---

### Task 2: Measure and Lock Coverage

**Files:**
- Modify: `pyproject.toml`
- Modify: `tests/test_quality_gate.py`

**Interfaces:**
- Consumes: unchanged application code and the complete existing pytest suite.
- Produces: fixed Coverage.py `fail_under` and pytest coverage arguments.

- [ ] **Step 1: Measure the pre-remediation baseline**

Run before any Ruff or mypy source fixes:

```bash
.venv/bin/python -m pytest --cov=governance_app --cov-report=term
```

Record the reported integer total `P`. Compute the gate as:

```python
gate = (P // 5) * 5 if P >= 70 else int(P)
```

- [ ] **Step 2: Lock pytest and Coverage.py configuration**

First add a failing contract test asserting the exact computed gate and the pytest coverage arguments. Run the focused test and confirm it fails because neither setting exists. Then set the configuration below.

Set:

```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
addopts = "--cov=governance_app --cov-report=term-missing"

[tool.coverage.report]
show_missing = true
exclude_also = ["if __name__ == .__main__.:"]
```

Add a numeric `fail_under` line between `show_missing` and `exclude_also`; its integer value is exactly the `gate` calculated in Step 1.

- [ ] **Step 3: Strengthen the contract test with the exact gate**

Replace the loose positive assertion with the computed integer:

```python
assert PROJECT["tool"]["coverage"]["report"]["fail_under"] == EXPECTED_BASELINE_GATE
```

Define `EXPECTED_BASELINE_GATE` as the integer calculated and recorded in Step 1.

- [ ] **Step 4: Run the contract and full coverage suite**

```bash
.venv/bin/python -m pytest tests/test_quality_gate.py -q
.venv/bin/python -m pytest -q
```

Expected: contract tests pass and total coverage meets the fixed gate.

- [ ] **Step 5: Commit the coverage baseline**

```bash
git add pyproject.toml tests/test_quality_gate.py
git commit -m "Lock test coverage baseline"
```

---

### Task 3: Enforce Ruff Without Formatting Churn

**Files:**
- Modify: only Python files reported by the selected Ruff rules.

**Interfaces:**
- Consumes: `[tool.ruff]` policy from Task 1.
- Produces: a clean `ruff check src tests` result with behavior preserved.

- [ ] **Step 1: Run Ruff and capture the failing rule list**

```bash
.venv/bin/python -m ruff check src tests
```

Expected: nonzero if historical import order, unused imports, or bug-risk patterns exist.

- [ ] **Step 2: Apply safe mechanical fixes**

```bash
.venv/bin/python -m ruff check --fix src tests
```

Inspect `git diff`; do not run `ruff format`.

- [ ] **Step 3: Resolve any remaining findings surgically**

For each remaining diagnostic, preserve expressions and behavior. Do not add file-wide ignores. If a targeted ignore is unavoidable, use an error-code-specific inline comment and add a contract assertion that no global `# noqa` was introduced.

- [ ] **Step 4: Verify Ruff and all tests**

```bash
.venv/bin/python -m ruff check src tests
.venv/bin/python -m pytest -q
```

Expected: both commands pass and coverage remains above the fixed gate.

- [ ] **Step 5: Commit Ruff adoption**

```bash
git add src tests
git commit -m "Adopt Ruff quality checks"
```

---

### Task 4: Enforce Staged mypy Boundaries

**Files:**
- Modify: targeted files under `src/governance_app/rules/` and `src/governance_app/routes/` only when mypy reports an error.
- Modify: `src/governance_app/audit_rules.py`, `rule_types.py`, `rule_fields.py`, or `rule_helpers.py` only when reported.

**Interfaces:**
- Consumes: stable rule dataclasses, facade API, domain rule factories, and route handler protocols.
- Produces: a clean `.venv/bin/python -m mypy` result under the staged policy.

- [ ] **Step 1: Run mypy and verify the baseline is red or clean**

```bash
.venv/bin/python -m mypy
```

Record every diagnostic. A clean initial result is acceptable evidence that the selected boundary already conforms; do not manufacture changes.

- [ ] **Step 2: Fix reported type defects minimally**

Prefer precise annotations and local narrowing. Preserve public facade imports and route return shapes. Do not add `ignore_errors`, unscoped `type: ignore`, `Any` casts solely to silence a real mismatch, or runtime validation unrelated to a diagnostic.

- [ ] **Step 3: Verify mypy, Ruff, and tests together**

```bash
.venv/bin/python -m mypy
.venv/bin/python -m ruff check src tests
.venv/bin/python -m pytest -q
```

Expected: all commands pass.

- [ ] **Step 4: Commit staged typing fixes**

If source changes were required:

```bash
git add src
git commit -m "Type check rule and route boundaries"
```

If no source changes were required, record the clean command output and do not create an empty commit.

---

### Task 5: Make the Quality Gate and Documentation Authoritative

**Files:**
- Modify: `scripts/check.sh`
- Modify: `tests/test_quality_gate.py`
- Modify: `CONTRIBUTING.md`
- Modify: `README.md`
- Verify: `.github/workflows/quality.yml`

**Interfaces:**
- Consumes: tool policy in `pyproject.toml`.
- Produces: one local/CI command, `scripts/check.sh`.

- [ ] **Step 1: Add failing script/workflow contract tests**

Assert the script contains, in order:

```python
assert CHECK_SCRIPT.index("-m ruff check src tests") < CHECK_SCRIPT.index("-m mypy")
assert CHECK_SCRIPT.index("-m mypy") < CHECK_SCRIPT.index("-m pytest -q")
assert 'run: scripts/check.sh' in QUALITY_WORKFLOW
```

Also assert the script does not contain `ruff format` or `--no-cov`.

- [ ] **Step 2: Run the focused test and verify red**

```bash
.venv/bin/python -m pytest tests/test_quality_gate.py -q
```

Expected: failure because `scripts/check.sh` does not yet invoke Ruff or mypy.

- [ ] **Step 3: Update the single quality entry point**

Insert before pytest:

```bash
.venv/bin/python -m ruff check src tests
.venv/bin/python -m mypy
.venv/bin/python -m pytest -q
```

Keep compileall, JavaScript discovery, and Shell syntax checks unchanged.

- [ ] **Step 4: Document individual and aggregate commands**

Update `CONTRIBUTING.md` with the full gate and standalone Ruff/mypy/pytest commands. Update the README delivery checklist to state that `scripts/check.sh` enforces static checks, type checks, coverage, tests, and syntax checks.

- [ ] **Step 5: Run the focused contract and complete gate**

```bash
.venv/bin/python -m pytest tests/test_quality_gate.py -q
scripts/check.sh
```

Expected: all configured checks pass.

- [ ] **Step 6: Commit the authoritative quality gate**

```bash
git add scripts/check.sh tests/test_quality_gate.py CONTRIBUTING.md README.md
git commit -m "Enforce shared engineering quality gate"
```

---

### Task 6: Verification, Review, and Integration

**Files:**
- Verify all changed files.

**Interfaces:**
- Consumes: completed third-stage branch.
- Produces: verified commits on `main` and synchronized `origin/main`.

- [ ] **Step 1: Run the complete gate from a clean command invocation**

```bash
scripts/check.sh
```

- [ ] **Step 2: Verify anti-bypass invariants**

```bash
rg -n "ignore_errors|ruff format|--no-cov|# noqa" pyproject.toml scripts src tests
git diff main...HEAD --check
git status --short
```

Expected: no global bypasses, no whitespace errors, and no uncommitted files.

- [ ] **Step 3: Request independent review**

Review the full branch against the design for dependency scope, threshold derivation, coverage exclusions, Ruff bypasses, mypy scope, CI/local parity, and behavior preservation. Fix every Critical or Important finding and rerun the complete gate.

- [ ] **Step 4: Fast-forward merge into `main`**

Update `main`, merge the feature branch with `--ff-only`, and run `scripts/check.sh` on the merged result.

- [ ] **Step 5: Clean up and push**

Remove the owned worktree, delete the merged feature branch, push `main` to `origin`, and verify:

```bash
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
```

Expected: clean synchronized `main` with identical local and remote SHAs.
