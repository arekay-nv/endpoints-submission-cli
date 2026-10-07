"""Utilities for loading and parsing submission artifact files."""

from __future__ import annotations

import json
from pathlib import Path

__all__ = [
    "load_accuracy_result",
    "load_accuracy_scores",
    "load_point_config",
    "load_result_summary",
    "load_system_description",
    "load_system_power",
]

from typing import Any

import yaml
from pydantic import ValidationError
from pydantic_core import ErrorDetails

from .. import messages
from ..messages import PYDANTIC, SHARED, Invalid, MessageCatalogError
from .file import AccuracyResult, PointConfig, PointSummary, SystemDescription, SystemPower
from .results import CheckResult, err, field_err

#: A load failure: the ``_shared`` catalog message and the values it needs.
_LoadError = tuple[str, dict[str, object]]


def _load_json(path: Path) -> tuple[dict[str, Any] | None, _LoadError | None]:
    try:
        return json.loads(path.read_text()), None
    except FileNotFoundError:
        return None, ("file-not-found", {"file_path": str(path)})
    except json.JSONDecodeError as exc:
        return None, ("parse-error", {"file": path.name, "format": "JSON", "error": str(exc)})
    except OSError as exc:
        return None, ("io-error", {"file": path.name, "error": str(exc)})


def _load_yaml(path: Path) -> tuple[dict[str, Any] | None, _LoadError | None]:
    try:
        data = yaml.safe_load(path.read_text())
        if not isinstance(data, dict):
            return None, ("not-a-mapping", {"file": path.name, "format": "YAML"})
        return data, None
    except FileNotFoundError:
        return None, ("file-not-found", {"file_path": str(path)})
    except yaml.YAMLError as exc:
        return None, ("parse-error", {"file": path.name, "format": "YAML", "error": str(exc)})
    except OSError as exc:
        return None, ("io-error", {"file": path.name, "error": str(exc)})


def _load_failure(rule: str, path: Path, failure: _LoadError) -> list[CheckResult]:
    key, params = failure
    return [err(rule, key, path, **params)]


def _brief(value: object, limit: int = 60) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _problem(error: ErrorDetails) -> tuple[str, str, dict[str, object]]:
    """The catalog message for one Pydantic error: (rule, key, values)."""
    ctx = dict(error.get("ctx") or {})
    cause = ctx.get("error")
    if isinstance(cause, Invalid):
        return cause.rule, cause.key, cause.params
    if error["type"] in messages.catalog()[PYDANTIC].messages:
        return PYDANTIC, error["type"], {**ctx, "input": _brief(error["input"])}
    if messages.strict:
        raise MessageCatalogError(
            f"no {PYDANTIC} message for Pydantic error {error['type']!r}: {error['msg']}"
        )
    return SHARED, "unworded-error", {"message": error["msg"]}


def _validation_errors(exc: ValidationError, rule: str, path: Path) -> list[CheckResult]:
    return [
        field_err(rule, path, ".".join(str(part) for part in e["loc"]), *_problem(e))
        for e in exc.errors(include_url=False)
    ]


def load_system_description(
    path: Path,
) -> tuple[SystemDescription | None, list[CheckResult]]:
    """Load and validate a point's ``system_desc.json`` (§8.2).

    Returns:
        A ``(model, check_results)`` pair.  On success the model is not None and
        check_results is empty.  On failure the model is None and check_results
        contains one entry per validation error.
    """
    data, load_err = _load_json(path)
    if load_err:
        return None, _load_failure("system-description-valid", path, load_err)
    try:
        return SystemDescription.model_validate(data), []
    except ValidationError as exc:
        return None, _validation_errors(exc, "system-description-valid", path)


def load_point_config(
    path: Path, context: dict[str, Any] | None = None
) -> tuple[PointConfig | None, list[CheckResult]]:
    """Load and validate a ``point_<N>.yaml`` measurement-point config.

    Returns:
        A ``(model, check_results)`` pair.  On success the model is not None and
        check_results contains the validator-produced CheckResult entries.
        On failure the model is None and check_results contains one entry per
        validation error.
    """
    data, load_err = _load_yaml(path)
    if load_err:
        return None, _load_failure("point-config-valid", path, load_err)
    try:
        instance = PointConfig.model_validate(data, context=context or {})
        return instance, list(instance._check_results)
    except ValidationError as exc:
        return None, _validation_errors(exc, "point-config-valid", path)


def load_result_summary(path: Path) -> tuple[PointSummary | None, list[CheckResult]]:
    """Load and validate ``results_summary.json``.

    Returns:
        A ``(model, check_results)`` pair.  On success the model is not None and
        check_results is empty.  On failure the model is None and check_results
        contains one entry per validation error.
    """
    data, load_err = _load_json(path)
    if load_err:
        return None, _load_failure("result-file-valid", path, load_err)
    try:
        return PointSummary.model_validate(data), []
    except ValidationError as exc:
        return None, _validation_errors(exc, "result-file-valid", path)


def load_accuracy_result(
    path: Path,
) -> tuple[AccuracyResult | None, list[CheckResult]]:
    """Load and validate ``accuracy_result.json``.

    Returns:
        A ``(model, check_results)`` pair.  On success the model is not None and
        check_results contains the validator-produced CheckResult entries.
        On failure the model is None and check_results contains one entry per
        validation error.
    """
    data, load_err = _load_json(path)
    if load_err:
        return None, _load_failure("accuracy-valid", path, load_err)
    try:
        instance = AccuracyResult.model_validate(data, context={"json_path": path})
        return instance, list(instance._check_results)
    except ValidationError as exc:
        return None, _validation_errors(exc, "accuracy-valid", path)


def load_accuracy_scores(
    path: Path,
) -> tuple[AccuracyResult | None, list[CheckResult], bool]:
    """Load accuracy from a ``results.json``'s ``accuracy_scores`` field.

    The benchmark writes per-dataset accuracy directly into ``results.json`` under
    ``accuracy_scores`` (a dataset mapping or native list). This reads that
    field instead of a separate ``accuracy/results.json`` file.

    Returns ``(model, check_results, present)``. ``present`` is True when the file
    contains a non-empty ``accuracy_scores`` value (regardless of validity); a
    missing/invalid ``results.json`` is reported by the result-summary loaders, so
    accuracy is simply treated as absent here. On a validation failure the model is
    None and ``check_results`` holds one entry per error.
    """
    data, load_err = _load_json(path)
    if load_err or not isinstance(data, dict):
        return None, [], False
    scores = data.get("accuracy_scores")
    if scores is None or scores == {} or scores == []:
        return None, [], False
    try:
        instance = AccuracyResult.model_validate(
            {"accuracy_scores": scores}, context={"json_path": path}
        )
        return instance, list(instance._check_results), True
    except ValidationError as exc:
        return None, _validation_errors(exc, "accuracy-valid", path), True


def load_system_power(path: Path) -> tuple[SystemPower | None, list[CheckResult]]:
    """Load and validate a system's ``system_power.json`` (§4.5.2).

    Returns:
        A ``(model, check_results)`` pair. On failure the model is None and
        check_results contains one entry per validation error.
    """
    data, load_err = _load_json(path)
    if load_err:
        return None, _load_failure("power-descriptor", path, load_err)
    try:
        return SystemPower.model_validate(data), []
    except ValidationError as exc:
        return None, _validation_errors(exc, "power-descriptor", path)
