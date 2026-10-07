# SPDX-FileCopyrightText: Copyright (c) 2024 MLCommons
# SPDX-License-Identifier: Apache-2.0
"""``check-submission`` command — run the submission checker on a directory.

Designed to be dropped into a GitHub Actions (or any CI) pipeline: it prints a
human-readable table, optionally emits GitHub workflow annotations and a job
summary, and exits non-zero when the submission fails §9.1 compliance.

Results are listed errors first, then warnings, then info. Inside GitHub
Actions the errors print in full while warnings and info are folded into
log groups (collapsed by default), and the job summary does the same with a
<details> block, so a failing check opens on what failed.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from submission_checker.checker import SubmissionChecker
from submission_checker.models import CheckResult, Report, Severity

__all__ = ["check_submission"]

# Row styles: errors and warnings must be told apart at a glance.
_SEVERITY_STYLE: dict[Severity, str] = {
    Severity.ERROR: "bold red",
    Severity.WARNING: "yellow",
    Severity.INFO: "dim",
}

# Display order: what failed comes first.
_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.ERROR: 0,
    Severity.WARNING: 1,
    Severity.INFO: 2,
}

# Job-summary markers. Markdown cannot colour text, so the colour rides on these.
_SUMMARY_MARKER: dict[Severity, str] = {
    Severity.ERROR: "🔴",
    Severity.WARNING: "🟡",
    Severity.INFO: "",
}

# GitHub Actions logs render ANSI colour but are not a TTY, and report an
# 80-column terminal that truncates every message cell.
_GITHUB_LOG_WIDTH = 200

# Map our severities onto GitHub Actions annotation levels.
_GITHUB_LEVEL: dict[Severity, str] = {
    Severity.ERROR: "error",
    Severity.WARNING: "warning",
    Severity.INFO: "notice",
}


def _gha_escape(value: str, *, prop: bool) -> str:
    """Escape a string for a GitHub Actions workflow command.

    See: https://docs.github.com/actions/using-workflows/workflow-commands-for-github-actions
    """
    out = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if prop:
        out = out.replace(":", "%3A").replace(",", "%2C")
    return out


def _rel_to_cwd(path: Path) -> str:
    """Path relative to cwd (the repo root under ``actions/checkout``) if possible."""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _check_title(result: CheckResult) -> str:
    """The rule's title, with its spec section when it has one."""
    title = result.title or result.rule
    return f"{title} ({result.spec_ref})" if result.spec_ref else title


def _emit_github_annotations(report: Report) -> None:
    """Emit ``::error``/``::warning`` workflow commands so findings show up inline.

    Titled with the rule's title and spec section; the body is the message and,
    where the catalog has one, the fix. A finding repeated on the same file is
    annotated once.

    Written to stderr: GitHub Actions parses workflow commands from the merged
    step log, so stdout stays clean for ``--json`` machine consumption.
    """
    seen: set[tuple[object, ...]] = set()
    for result in report.results:
        if result.severity == Severity.INFO:
            continue
        identity = (result.severity, result.rule, result.message, result.path)
        if identity in seen:
            continue
        seen.add(identity)
        props = [f"title={_gha_escape(_check_title(result), prop=True)}"]
        if result.path is not None:
            props.insert(0, f"file={_gha_escape(_rel_to_cwd(result.path), prop=True)}")
        body = result.message + (f"\n\nFix: {result.fix}" if result.fix else "")
        message = _gha_escape(body, prop=False)
        click.echo(f"::{_GITHUB_LEVEL[result.severity]} {','.join(props)}::{message}", err=True)


def _by_severity(results: list[CheckResult]) -> list[CheckResult]:
    """Errors, then warnings, then info; stable, so checker order holds within each."""
    return sorted(results, key=lambda r: _SEVERITY_ORDER[r.severity])


@dataclass
class _Finding:
    """Identical results (same rule, wording and fix) shown as one row."""

    result: CheckResult
    paths: list[Path | None]

    @property
    def count(self) -> int:
        return len(self.paths)

    def message(self) -> str:
        return self.result.message + (f" (×{self.count})" if self.count > 1 else "")


def _findings(results: list[CheckResult]) -> list[_Finding]:
    """Group identical results, errors first, keeping first-seen order."""
    groups: dict[tuple[object, ...], _Finding] = {}
    for r in _by_severity(results):
        identity = (r.severity, r.rule, r.message, r.fix)
        if identity in groups:
            groups[identity].paths.append(r.path)
        else:
            groups[identity] = _Finding(r, [r.path])
    return list(groups.values())


def _where(finding: _Finding, show: Callable[[Path], str]) -> str:
    first = finding.paths[0]
    loc = show(first) if first is not None else ""
    return loc + (f" (+{finding.count - 1} more)" if finding.count > 1 else "")


def _md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _summary_table(results: list[CheckResult]) -> list[str]:
    lines = ["| Severity | Check | Message | Path |", "| --- | --- | --- | --- |"]
    for finding in _findings(results):
        r = finding.result
        check = f"{_md(_check_title(r))}<br><sub>`{r.rule}`</sub>"
        msg = _md(finding.message()) + (f"<br>**Fix:** {_md(r.fix)}" if r.fix else "")
        severity = f"{_SUMMARY_MARKER[r.severity]} {r.severity.value}"
        lines.append(f"| {severity} | {check} | {msg} | {_md(_where(finding, _rel_to_cwd))} |")
    return lines


def _write_step_summary(report: Report, path: Path, summary_file: Path) -> None:
    """Append a Markdown summary to ``$GITHUB_STEP_SUMMARY`` for the job page.

    Errors are shown in full; warnings sit in a collapsed <details> block.
    """
    errors, warnings = report.errors, report.warnings
    status = "✅ **PASSED**" if not errors else "❌ **FAILED**"
    lines = [
        "## Submission Checker",
        "",
        f"{status} — `{path}`",
        "",
        f"- 🔴 Errors: **{len(errors)}**",
        f"- 🟡 Warnings: **{len(warnings)}**",
        f"- Total checks: {len(report.results)}",
    ]
    if errors:
        lines += ["", "### 🔴 Errors", "", *_summary_table(errors)]
    if warnings:
        # The blank lines around the table are required for GitHub to render
        # Markdown inside <details>.
        lines += [
            "",
            "<details>",
            f"<summary>🟡 {len(warnings)} warning(s) — click to expand</summary>",
            "",
            *_summary_table(warnings),
            "",
            "</details>",
        ]
    lines.append("")
    with open(summary_file, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _render_table(results: list[CheckResult], path: Path, title: str) -> Table:
    table = Table(title=title, show_lines=True)
    table.add_column("Check", no_wrap=True)
    table.add_column("Severity", no_wrap=True)
    table.add_column("Message")
    table.add_column("Path", style="dim")

    def show(p: Path) -> str:
        return str(p.relative_to(path)) if p.is_relative_to(path) else str(p)

    for finding in _findings(results):
        result = finding.result
        style = _SEVERITY_STYLE[result.severity]
        check = Text(_check_title(result))
        check.append(f"\n{result.rule}", style="dim")
        message = Text(finding.message())
        if result.fix:
            message.append(f"\nFix: {result.fix}", style="italic")
        # Errors and warnings colour the whole row; info only dims its severity.
        row_style = style if result.severity != Severity.INFO else None
        table.add_row(
            check,
            f"[{style}]{result.severity.value}[/{style}]",
            message,
            _where(finding, show),
            style=row_style,
        )
    return table


def _print_report(console: Console, report: Report, path: Path, quiet: bool) -> None:
    """One table, errors first: the view for a terminal."""
    results = [r for r in report.results if not (quiet and r.severity == Severity.INFO)]
    console.print(_render_table(results, path, f"Submission Check — {path}"))


def _print_grouped_report(console: Console, report: Report, path: Path, quiet: bool) -> None:
    """The GitHub Actions log view: errors in full, the rest in collapsed groups.

    Log groups cannot nest, so the calling workflow must not wrap this command
    in a ``::group::`` of its own, or these would close it early.
    """
    title = f"Submission Check — {path}"
    if report.errors:
        console.print(_render_table(report.errors, path, f"{title}: errors"))
    infos = [r for r in report.results if r.severity == Severity.INFO]
    folded = [(report.warnings, "warning(s)")]
    if not quiet:
        folded.append((infos, "info result(s)"))
    for results, label in folded:
        if not results:
            continue
        console.file.flush()
        click.echo(f"::group::{title}: {len(results)} {label}")
        console.print(_render_table(results, path, f"{title}: {label}"))
        console.file.flush()
        click.echo("::endgroup::")


@click.command(name="check-submission")
@click.argument("path", type=click.Path(exists=False, path_type=Path))
@click.option("--strict", is_flag=True, default=False, help="Treat warnings as errors.")
@click.option("-q", "--quiet", is_flag=True, default=False, help="Hide INFO-level results.")
@click.option(
    "-j",
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Print the full report as JSON to stdout (suppresses the table).",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="Write the full report as JSON to FILE.",
)
@click.option(
    "--annotate/--no-annotate",
    default=None,
    help="Emit GitHub Actions annotations. Default: on when $GITHUB_ACTIONS is set.",
)
def check_submission(
    path: Path,
    strict: bool,
    quiet: bool,
    as_json: bool,
    output: Path | None,
    annotate: bool | None,
) -> None:
    r"""Run the submission checker on the submission directory at PATH.

    PATH is the submitting organisation's root directory. Intended for CI: it
    prints a results table to stdout, optionally emits GitHub Actions
    annotations/job summary, and sets the exit code from the outcome.

    Exit codes:

    \b
      0  Passed — no errors (and no warnings when --strict).
      1  Failed — one or more errors (or warnings when --strict).
      2  Usage error (bad arguments).
    """
    report = SubmissionChecker(path).run()

    in_github = os.environ.get("GITHUB_ACTIONS") == "true"
    do_annotate = in_github if annotate is None else annotate
    # stdout: the report is this command's primary output. Rich disables colour
    # when stdout is not a TTY; GitHub Actions logs are not one but do render it.
    console = Console(force_terminal=True, width=_GITHUB_LOG_WIDTH) if in_github else Console()

    if output is not None:
        output.write_text(report.model_dump_json(indent=2))

    if do_annotate:
        _emit_github_annotations(report)
        summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_file:
            _write_step_summary(report, path, Path(summary_file))

    if as_json:
        click.echo(report.model_dump_json(indent=2))
    elif in_github:
        _print_grouped_report(console, report, path, quiet)
    else:
        _print_report(console, report, path, quiet)

    error_count = len(report.errors)
    warn_count = len(report.warnings)
    failed = error_count > 0 or (strict and warn_count > 0)

    if not as_json:
        verdict = "[bold red]FAILED[/]" if failed else "[bold green]PASSED[/]"
        console.print(f"{verdict} — {error_count} error(s), {warn_count} warning(s)")

    raise SystemExit(1 if failed else 0)
