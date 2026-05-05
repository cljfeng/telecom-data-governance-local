from dataclasses import dataclass
from typing import Literal

LedgerType = Literal["site", "tower_rent", "electricity", "generator"]
IssueStatus = Literal[
    "pending_export",
    "pending_correction",
    "returned",
    "still_invalid",
    "needs_review",
    "closed",
    "not_required",
]
Severity = Literal["high", "medium", "low"]

LEDGER_TYPES: tuple[LedgerType, ...] = ("site", "tower_rent", "electricity", "generator")


@dataclass(frozen=True)
class ValidationErrorDetail:
    row_number: int
    field_name: str
    message: str
