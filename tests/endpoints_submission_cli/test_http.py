# SPDX-FileCopyrightText: Copyright (c) 2024 MLCommons
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for _http transport module (token resolution and HTTP helpers)."""

from __future__ import annotations

import pytest

from endpoints_submission_cli._http import _base_url, get_token
from endpoints_submission_cli.exceptions import AuthError


@pytest.mark.unit
class TestGetToken:
    def test_explicit_token_returned(self) -> None:
        assert get_token("mlc_abc") == "mlc_abc"

    def test_env_var_used_when_no_explicit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PRISM_USER_API_TOKEN", "mlc_env")
        assert get_token(None) == "mlc_env"

    def test_explicit_takes_precedence_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PRISM_USER_API_TOKEN", "mlc_env")
        assert get_token("mlc_explicit") == "mlc_explicit"

    def test_no_token_raises_auth_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PRISM_USER_API_TOKEN", raising=False)
        with pytest.raises(AuthError):
            get_token(None)


@pytest.mark.unit
class TestBaseUrl:
    def test_default_is_the_custom_domain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Not the generated *.run.app URL, which changes if the service is recreated."""
        monkeypatch.delenv("MLPERF_API_BASE_URL", raising=False)
        assert _base_url() == "https://endpointsapi.mlcommons.org"

    def test_env_var_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MLPERF_API_BASE_URL", "http://localhost:8080")
        assert _base_url() == "http://localhost:8080"
