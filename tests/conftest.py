# SPDX-FileCopyrightText: Copyright (c) 2024 MLCommons
# SPDX-License-Identifier: Apache-2.0
"""Suite-wide fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from submission_checker import messages


@pytest.fixture(autouse=True)
def _strict_messages() -> Iterator[None]:
    """A check result whose message cannot render fails the test that produced it.

    Outside tests the catalog degrades to a generic message instead, so a wording
    mistake never stops a submission from being checked.
    """
    previous = messages.strict
    messages.strict = True
    try:
        yield
    finally:
        messages.strict = previous
