import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
GITIGNORE = (PROJECT_ROOT / ".gitignore").read_text().splitlines()
CHECK_SCRIPT = (PROJECT_ROOT / "scripts" / "check.sh").read_text()
QUALITY_WORKFLOW = (PROJECT_ROOT / ".github" / "workflows" / "quality.yml").read_text()
EXPECTED_MYPY_FILES = [
    "src/governance_app/rule_types.py",
    "src/governance_app/rule_fields.py",
    "src/governance_app/rule_helpers.py",
    "src/governance_app/audit_rules.py",
    "src/governance_app/rules",
    "src/governance_app/routes",
]
EXPECTED_BASELINE_GATE = 90
EXPECTED_CHECK_SCRIPT = '''#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

.venv/bin/python -m ruff check src tests
.venv/bin/python -m mypy
.venv/bin/python -m pytest -q --cov=governance_app --cov-report=term-missing
.venv/bin/python -m compileall -q src
for module in src/governance_app/static/*.js; do
  node --check "$module"
done
bash -n scripts/start.sh
bash -n scripts/build_app.sh
'''
EXPECTED_QUALITY_WORKFLOW = '''name: Quality

on:
  pull_request:
  push:
    branches:
      - main

jobs:
  checks:
    name: Tests and syntax checks
    runs-on: ubuntu-latest

    steps:
      - name: Check out repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Set up Node.js
        uses: actions/setup-node@v4
        with:
          node-version: "22"

      - name: Install test dependencies
        run: |
          python -m venv .venv
          .venv/bin/python -m pip install -e ".[test]"

      - name: Run quality gate
        run: scripts/check.sh
'''


def test_test_dependencies_include_quality_tools():
    dependencies = PROJECT["project"]["optional-dependencies"]["test"]
    runtime_dependencies = PROJECT["project"]["dependencies"]

    assert any(item.startswith("pytest-cov>=6.0") for item in dependencies)
    assert any(item.startswith("ruff>=0.11") for item in dependencies)
    assert any(item.startswith("mypy>=1.15") for item in dependencies)
    assert not any(item.startswith(("pytest-cov", "ruff", "mypy")) for item in runtime_dependencies)


def test_ruff_policy_is_high_value_and_non_formatting():
    ruff = PROJECT["tool"]["ruff"]

    assert set(ruff) == {"target-version", "lint"}
    assert ruff["target-version"] == "py312"
    assert set(ruff["lint"]) == {"select", "isort"}
    assert ruff["lint"]["select"] == ["E4", "E7", "E9", "F", "I", "B"]
    assert set(ruff["lint"]["isort"]) == {"combine-as-imports"}
    assert ruff["lint"]["isort"]["combine-as-imports"] is True


def test_mypy_policy_targets_stable_boundaries():
    mypy = PROJECT["tool"]["mypy"]

    assert set(mypy) == {
        "python_version",
        "files",
        "check_untyped_defs",
        "no_implicit_optional",
        "warn_unused_ignores",
        "warn_redundant_casts",
        "warn_return_any",
        "ignore_missing_imports",
        "follow_imports",
    }
    assert mypy["python_version"] == "3.12"
    assert mypy["files"] == EXPECTED_MYPY_FILES
    assert mypy["check_untyped_defs"] is True
    assert mypy["no_implicit_optional"] is True
    assert mypy["warn_unused_ignores"] is True
    assert mypy["warn_redundant_casts"] is True
    assert mypy["warn_return_any"] is True
    assert mypy["ignore_missing_imports"] is True
    assert mypy["follow_imports"] == "silent"


def test_coverage_policy_covers_the_complete_package():
    coverage = PROJECT["tool"]["coverage"]

    assert set(coverage) == {"run", "report"}
    assert set(coverage["run"]) == {"source"}
    assert coverage["run"]["source"] == ["governance_app"]
    assert set(coverage["report"]) == {"show_missing", "fail_under", "exclude_also"}
    assert coverage["report"]["show_missing"] is True
    assert coverage["report"]["exclude_also"] == ['if __name__ == .__main__.:']


def test_coverage_policy_locks_the_measured_baseline():
    coverage_report = PROJECT["tool"]["coverage"]["report"]
    pytest_options = PROJECT["tool"]["pytest"]["ini_options"]

    assert coverage_report["fail_under"] == EXPECTED_BASELINE_GATE
    assert set(pytest_options) == {"pythonpath", "testpaths"}


def test_coverage_data_file_is_ignored():
    assert ".coverage" in GITIGNORE


def test_check_script_runs_the_complete_gate_in_order():
    assert CHECK_SCRIPT == EXPECTED_CHECK_SCRIPT


def test_ci_uses_the_same_quality_gate():
    assert QUALITY_WORKFLOW == EXPECTED_QUALITY_WORKFLOW
