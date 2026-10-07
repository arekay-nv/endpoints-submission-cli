"""Check infrastructure — Severity, CheckResult, result helpers, and Report."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, computed_field

from ..messages import SHARED, catalog, render

__all__ = ["CheckResult", "Report", "Severity", "err", "field_err", "ok", "warn"]


class Severity(str, Enum):
    """Severity level for a check result."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class CheckResult(BaseModel):
    """Result of a single automated check.

    The wording comes from the message catalog (``data/messages.yaml``); build
    results with :func:`ok`, :func:`warn` and :func:`err` rather than by hand.

    Attributes:
        rule: Short identifier matching a §9.1 check name.
        key: Which of the rule's catalog messages this is.
        title: The rule's human-readable title, from the catalog.
        message: Human-readable description of the finding.
        fix: What the submitter should do about it, where the catalog says.
        severity: How critical the finding is.
        path: File or directory the finding applies to, if any.
        spec_ref: The spec section the rule enforces, e.g. ``§5.4``.
    """

    model_config = ConfigDict(frozen=True)

    rule: str
    message: str
    severity: Severity = Severity.ERROR
    path: Path | None = None
    spec_ref: str = ""
    key: str = ""
    title: str = ""
    fix: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        """True when the result is not an error."""
        return self.severity != Severity.ERROR


def _result(
    severity: Severity, rule: str, key: str, path: Path | None, params: dict[str, object]
) -> CheckResult:
    rendered = render(rule, key, params)
    return CheckResult(
        rule=rule,
        key=key,
        title=rendered.title,
        message=rendered.text,
        fix=rendered.fix,
        severity=severity,
        path=path,
        spec_ref=rendered.spec,
    )


def ok(rule: str, key: str, path: Path | None = None, /, **params: object) -> CheckResult:
    """An INFO result — the check passed. *key* names the catalog message."""
    return _result(Severity.INFO, rule, key, path, params)


def warn(rule: str, key: str, path: Path | None = None, /, **params: object) -> CheckResult:
    """A WARNING result — notable but not a hard failure."""
    return _result(Severity.WARNING, rule, key, path, params)


def err(rule: str, key: str, path: Path | None = None, /, **params: object) -> CheckResult:
    """An ERROR result — the check failed."""
    return _result(Severity.ERROR, rule, key, path, params)


def field_err(
    rule: str, path: Path, field: str, source: str, key: str, params: Mapping[str, object]
) -> CheckResult:
    """An ERROR for one field of a file that failed validation.

    *source* and *key* name the catalog message saying what is wrong: a rule's own
    message raised as :class:`~submission_checker.messages.Invalid`, or a
    :data:`~submission_checker.messages.PYDANTIC` one. The shared ``field-invalid``
    message places it in the file and field; the result keeps its fix and spec.
    """
    problem = render(source, key, params)
    where = {"file": path.name, "problem": problem.text}
    located = (
        render(SHARED, "field-invalid", {**where, "field": field})
        if field
        else render(SHARED, "file-invalid", where)
    )
    entry = catalog().get(rule)
    return CheckResult(
        rule=rule,
        key=key,
        title=entry.title if entry is not None else rule,
        message=located.text,
        fix=problem.fix,
        severity=Severity.ERROR,
        path=path,
        spec_ref=problem.spec or (entry.spec if entry is not None else ""),
    )


class Report(BaseModel):
    """Aggregated results from all checks against a submission.

    Attributes:
        submission_path: Root directory that was checked.
        results: Individual check results in order of execution.
    """

    submission_path: Path
    results: list[CheckResult] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def errors(self) -> list[CheckResult]:
        """All results with ERROR severity."""
        return [r for r in self.results if r.severity == Severity.ERROR]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def warnings(self) -> list[CheckResult]:
        """All results with WARNING severity."""
        return [r for r in self.results if r.severity == Severity.WARNING]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        """True when there are no errors."""
        return len(self.errors) == 0
