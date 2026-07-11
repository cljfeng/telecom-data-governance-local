import ast
from pathlib import Path

from governance_app import audit_rules
from governance_app.audit_rules import (
    AuditLedgerRow,
    AuditRule,
    BatchAuditRule,
    BatchRuleFinding,
    RuleFinding,
    RuleThresholds,
    all_batch_rules,
    all_rules,
)


RULES_DIR = Path(audit_rules.__file__).parent / "rules"


def test_facade_contains_no_domain_implementation_functions():
    tree = ast.parse(Path(audit_rules.__file__).read_text())
    names = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

    assert not any(
        name.startswith(("_tower_", "_generator_", "_electricity_", "_site_", "_fee_"))
        for name in names
    )


def test_domain_modules_do_not_import_compatibility_facade():
    for module_path in RULES_DIR.glob("*.py"):
        tree = ast.parse(module_path.read_text())
        imported_modules = {
            node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
        }
        imported_modules.update(
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        )
        assert "governance_app.audit_rules" not in imported_modules, module_path.name


def test_factories_do_not_import_domain_modules():
    factories_path = RULES_DIR / "factories.py"
    tree = ast.parse(factories_path.read_text())
    imported_modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }

    assert not imported_modules.intersection({
        "governance_app.rules.site",
        "governance_app.rules.tower_rent",
        "governance_app.rules.electricity",
        "governance_app.rules.generator",
        "governance_app.rules.cross_ledger",
    })


def test_facade_preserves_public_types_and_returns_fresh_lists():
    assert all((AuditLedgerRow, AuditRule, BatchAuditRule, BatchRuleFinding, RuleFinding, RuleThresholds))
    assert all_rules() is not all_rules()
    assert all_batch_rules() is not all_batch_rules()
