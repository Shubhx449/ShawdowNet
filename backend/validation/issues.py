"""
ValidationIssue / ValidationReport.

Unlike MetricsEngine._validate() (Phase 5), which raises immediately on an
impossible-by-construction math error (delivered > generated, for example --
that can only happen from a genuine bug and should fail loudly), this layer
is for auditing a COMPLETED run for anomalies worth a human's attention
without necessarily halting anything. Per the spec: "If invalid results
occur: show a visible warning" -- a warning, not a crash.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List


class Severity(str, Enum):
    ERROR = "ERROR"      # something that should be structurally impossible happened
    WARNING = "WARNING"  # worth a human's attention, not necessarily a bug


@dataclass
class ValidationIssue:
    severity: Severity
    code: str            # short stable identifier, e.g. "FAILED_NODE_FORWARDED"
    message: str          # human-readable description
    evidence: dict = field(default_factory=dict)  # supporting data (event IDs, packet IDs, etc.)

    def to_dict(self) -> dict:
        return {
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
            "evidence": self.evidence,
        }


@dataclass
class ValidationReport:
    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """True if there are no ERROR-severity issues. WARNINGs don't fail a run."""
        return not any(i.severity == Severity.ERROR for i in self.issues)

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    def add(self, issue: ValidationIssue) -> None:
        self.issues.append(issue)

    def extend(self, issues: List[ValidationIssue]) -> None:
        self.issues.extend(issues)

    def summary(self) -> str:
        if not self.issues:
            return "No issues found."
        return f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)."

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "summary": self.summary(),
            "issues": [i.to_dict() for i in self.issues],
        }
