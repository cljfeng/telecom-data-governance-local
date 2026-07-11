import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
GITIGNORE = (PROJECT_ROOT / ".gitignore").read_text().splitlines()
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
    assert "ignore_errors" not in mypy


def test_coverage_policy_covers_the_complete_package():
    coverage = PROJECT["tool"]["coverage"]

    assert coverage["run"]["source"] == ["governance_app"]
    assert coverage["report"]["show_missing"] is True
    assert coverage["report"]["exclude_also"] == ['if __name__ == .__main__.:']


def test_coverage_policy_locks_the_measured_baseline():
    coverage_report = PROJECT["tool"]["coverage"]["report"]
    pytest_options = PROJECT["tool"]["pytest"]["ini_options"]

    assert coverage_report["fail_under"] == EXPECTED_BASELINE_GATE
    assert "addopts" not in pytest_options


def test_coverage_data_file_is_ignored():
    assert ".coverage" in GITIGNORE
