"""Stable diagnostics shared by validation and command output."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    code: str
    severity: Severity
    location: str
    message: str
    hint: str | None = None

    def as_dict(self) -> dict[str, str]:
        result = {
            "code": self.code,
            "severity": self.severity.value,
            "location": self.location,
            "message": self.message,
        }
        if self.hint:
            result["hint"] = self.hint
        return result


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[Issue, ...]

    @property
    def valid(self) -> bool:
        return not any(issue.severity is Severity.ERROR for issue in self.issues)
