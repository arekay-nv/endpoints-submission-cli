"""Where a point sits in the concurrency space — §9.1 "Concurrency in range".

These rules used to live on :class:`~submission_checker.models.file.PointConfig`,
but v1.0 derives ``C_min`` from the submission's points (§5.4), so the region
boundaries are a property of the whole curve rather than of any single file. That
makes this an aggregate check, which is what this package is for.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, PrivateAttr, model_validator

from ..file.point_config import OFFLINE_DEDICATED, PointConfig
from ..regions import SUBMITTERS_CHOICE, Regions, classify_concurrency, covered_region
from ..results import CheckResult, err, ok, warn

__all__ = ["RegionPlacement"]


class RegionPlacement(BaseModel):
    """Validates one point's concurrency against its curve's region boundaries."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    _check_results: list[CheckResult] = PrivateAttr(default_factory=list)

    config: PointConfig
    regions: Regions
    yaml_path: Path

    @model_validator(mode="after")
    def _check_concurrency_range(self) -> RegionPlacement:
        """§9.1: the concurrency must fall inside a valid region, margin included.

        A **dedicated** Offline run is exempt: §5.7.1 fixes its concurrency at the
        *cardinality of the performance dataset* — "a property of the benchmark dataset
        rather than a value the submitter selects" — which routinely exceeds C_max and
        its 10 % margin. §5.7.2 constrains it instead, by requiring it to be ≥ C_max.

        An **elected** point is not exempt. §5.7.2's Option 2 is explicit that "the
        §5.7.1 exemptions … apply to a dedicated Offline run, not to an elected point":
        it "remains a fixed-concurrency pareto point" whose concurrency the submitter
        chose, so the §5.7.1 rationale does not reach it.
        """
        if self.config.offline == OFFLINE_DEDICATED:
            self._check_results.append(
                ok(
                    "concurrency-in-range",
                    "pass",
                    self.yaml_path,
                    concurrency=self.config.concurrency,
                )
            )
            return self
        concurrency = self.config.concurrency
        region = classify_concurrency(concurrency, self.regions)
        if region is None:
            self._check_results.append(
                err(
                    "concurrency-in-range",
                    "fail",
                    self.yaml_path,
                    concurrency=concurrency,
                    end=self.regions.margin.end,
                )
            )
        else:
            self._check_results.append(
                ok(
                    "concurrency-in-range",
                    "pass-2",
                    self.yaml_path,
                    concurrency=concurrency,
                    region=region,
                )
            )
        return self

    @model_validator(mode="after")
    def _check_region_placement(self) -> RegionPlacement:
        """§8.3: a declared region should match the one computed for its concurrency."""
        declared = self.config.region
        if declared is None or declared == SUBMITTERS_CHOICE:
            return self
        computed = classify_concurrency(self.config.concurrency, self.regions)
        if computed is None:
            return self  # already reported by concurrency-in-range
        if computed != declared:
            self._check_results.append(
                warn(
                    "region-placement",
                    "warn",
                    self.yaml_path,
                    declared=declared,
                    computed=computed,
                    concurrency=self.config.concurrency,
                )
            )
        else:
            self._check_results.append(
                ok(
                    "region-placement",
                    "pass",
                    self.yaml_path,
                    declared=declared,
                    concurrency=self.config.concurrency,
                )
            )
        return self

    @property
    def covered_region(self) -> str | None:
        """The region this point counts towards for §9.1 coverage, if any.

        A dedicated Offline run counts towards none: §5.7.2 says it "does not satisfy
        any region-coverage requirement of §5.3". An elected point is an ordinary
        fixed-concurrency point and keeps its region.
        """
        if self.config.offline == OFFLINE_DEDICATED:
            return None
        return covered_region(self.config.concurrency, self.regions)
