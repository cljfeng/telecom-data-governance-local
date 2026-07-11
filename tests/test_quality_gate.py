import re
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


def test_test_dependencies_include_quality_tools():
    dependencies = PROJECT["project"]["optional-dependencies"]["test"]

    assert any(item.startswith("pytest-cov>=6.0") for item in dependencies)
    assert any(item.startswith("ruff>=0.11") for item in dependencies)
    assert any(item.startswith("mypy>=1.15") for item in dependencies)


def test_ruff_policy_is_high_value_and_non_formatting():
    ruff = PROJECT["tool"]["ruff"]

    assert ruff["target-version"] == "py312"
    assert ruff["lint"]["select"] == ["E4", "E7", "E9", "F", "I", "B"]
    assert ruff["lint"]["isort"]["combine-as-imports"] is True
    assert "format" not in ruff
    assert not {"ignore", "extend-ignore", "per-file-ignores", "extend-per-file-ignores"}.intersection(ruff["lint"])


def test_mypy_policy_targets_stable_boundaries():
    mypy = PROJECT["tool"]["mypy"]

    assert mypy["python_version"] == "3.12"
    assert mypy["files"] == EXPECTED_MYPY_FILES
    assert mypy["check_untyped_defs"] is True
    assert mypy["no_implicit_optional"] is True
    assert mypy["warn_unused_ignores"] is True
    assert mypy["warn_redundant_casts"] is True
    assert mypy["warn_return_any"] is True
    assert mypy["ignore_missing_imports"] is True
    assert mypy["follow_imports"] == "silent"
    assert not {"ignore_errors", "exclude", "overrides", "disable_error_code"}.intersection(mypy)


def test_coverage_policy_covers_the_complete_package():
    coverage = PROJECT["tool"]["coverage"]

    assert coverage["run"]["source"] == ["governance_app"]
    assert "omit" not in coverage["run"]
    assert coverage["report"]["show_missing"] is True
    assert coverage["report"]["exclude_also"] == ['if __name__ == .__main__.:']


def test_coverage_policy_locks_the_measured_baseline():
    coverage_report = PROJECT["tool"]["coverage"]["report"]
    pytest_options = PROJECT["tool"]["pytest"]["ini_options"]

    assert coverage_report["fail_under"] == EXPECTED_BASELINE_GATE
    assert "addopts" not in pytest_options


def test_coverage_data_file_is_ignored():
    assert ".coverage" in GITIGNORE


def test_check_script_runs_the_complete_gate_in_order():
    active_lines = [
        line.strip()
        for line in CHECK_SCRIPT.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    gate_commands = [
        ".venv/bin/python -m ruff check src tests",
        ".venv/bin/python -m mypy",
        ".venv/bin/python -m pytest -q --cov=governance_app --cov-report=term-missing",
        ".venv/bin/python -m compileall -q src",
    ]
    gate_start = active_lines.index(gate_commands[0])

    assert active_lines[gate_start : gate_start + len(gate_commands)] == gate_commands
    assert not any(line.startswith(("exit ", "return ")) for line in active_lines[: gate_start + len(gate_commands)])
    assert "ruff format" not in CHECK_SCRIPT
    assert "--no-cov" not in CHECK_SCRIPT


def test_ci_uses_the_same_quality_gate():
    assert re.search(r'^\s+run: \|\s*\n\s+python -m venv \.venv\s*\n\s+\.venv/bin/python -m pip install -e "\.\[test\]"\s*$', QUALITY_WORKFLOW, re.MULTILINE)
    assert re.search(r"^\s+run: scripts/check\.sh\s*$", QUALITY_WORKFLOW, re.MULTILINE)
    assert "continue-on-error: true" not in QUALITY_WORKFLOW
