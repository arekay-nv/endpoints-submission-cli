# SPDX-FileCopyrightText: Copyright (c) 2024 MLCommons
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for submissions.formatters module."""

from __future__ import annotations

import pytest

from endpoints_submission_cli.submissions.formatters import (
    print_submission_detail,
    print_submissions_table,
)
from tests.endpoints_submission_cli.conftest import (
    RUN_OUT,
    SUBMISSION_ID,
    SUBMISSION_OUT,
)


@pytest.mark.unit
class TestPrintSubmissionsTable:
    def test_empty_list(self) -> None:
        print_submissions_table([])

    def test_renders_submission(self) -> None:
        print_submissions_table([SUBMISSION_OUT])


@pytest.mark.unit
class TestPrintSubmissionDetail:
    def test_renders_without_error(self) -> None:
        print_submission_detail(SUBMISSION_OUT)

    def test_renders_with_runs(self) -> None:
        sub = {**SUBMISSION_OUT, "runs": [RUN_OUT]}
        print_submission_detail(sub)

    def test_renders_minimal(self) -> None:
        print_submission_detail({"id": SUBMISSION_ID})

    def test_zero_reviewers_renders_as_zero_not_dash(self, capsys) -> None:
        """A count of 0 must stay visible — `value or "—"` would swallow it."""
        print_submission_detail({**SUBMISSION_OUT, "reviewers_assigned": 0})
        out = capsys.readouterr().out
        row = next(line for line in out.splitlines() if "Reviewers Assigned" in line)
        assert "0" in row
        assert "—" not in row

    def test_penalties_render_when_present(self, capsys) -> None:
        print_submission_detail(
            {**SUBMISSION_OUT, "penalties_imposed": 2, "business_days_since_response": 7}
        )
        out = capsys.readouterr().out
        penalties = next(line for line in out.splitlines() if "Penalties Imposed" in line)
        waiting = next(line for line in out.splitlines() if "Business Days Waiting" in line)
        assert "2" in penalties
        assert "7" in waiting

    def test_zero_penalties_render_as_zero(self, capsys) -> None:
        print_submission_detail({**SUBMISSION_OUT, "penalties_imposed": 0})
        out = capsys.readouterr().out
        row = next(line for line in out.splitlines() if "Penalties Imposed" in line)
        assert "0" in row
        assert "—" not in row

    def test_missing_penalties_render_as_dash(self, capsys) -> None:
        """An API that predates the field must not look like zero penalties."""
        sub = {k: v for k, v in SUBMISSION_OUT.items() if k != "penalties_imposed"}
        print_submission_detail(sub)
        out = capsys.readouterr().out
        row = next(line for line in out.splitlines() if "Penalties Imposed" in line)
        assert "—" in row

    def test_publication_cycle_is_labelled_as_published_cycle(self, capsys) -> None:
        print_submission_detail({**SUBMISSION_OUT, "publication_cycle": "2026-11-C0"})
        out = capsys.readouterr().out
        row = next(line for line in out.splitlines() if "Published In Cycle" in line)
        assert "2026-11-C0" in row
        assert "Publication Cycle" not in out

    def test_unpublished_cycle_renders_as_dash(self, capsys) -> None:
        print_submission_detail({**SUBMISSION_OUT, "publication_cycle": None})
        out = capsys.readouterr().out
        row = next(line for line in out.splitlines() if "Published In Cycle" in line)
        assert "—" in row
