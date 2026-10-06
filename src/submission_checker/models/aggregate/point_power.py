"""Per-point power normalisation — §4.5.3 and its three §9.1 rows.

Provisioned power is a property of the system (``system_power.json``); what varies
per point is how much of it the point *engages*. Three §9.1 rows follow:

- **Per-point node declaration** — a point's ``nodes_used`` names node sets that exist,
  stays within each set's provisioned ``N_s``, and covers the accelerators the point's
  parallelism engages.
- **Maximal engagement** — the point replicates data-parallel until no further
  replica fits, ``DP = floor(A_provisioned / A_replica)``, or declares why not.
- **Per-point denominator** — ``system_tps_per_kw = system_tps / point_power_kw``.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from pathlib import Path

from pydantic import BaseModel, ConfigDict, PrivateAttr, model_validator

from ..file.point_config import PointConfig
from ..file.system_power import PowerComputation
from ..results import CheckResult, err, ok, warn

__all__ = ["Parallelism", "PointPower"]

#: Relative tolerance for a stored ``system_tps_per_kw``. §4.5.2 reports it to one
#: decimal place, which this comfortably absorbs at any realistic magnitude.
_TPS_PER_KW_TOLERANCE = 0.01


@dataclass(frozen=True)
class Parallelism:
    """A point's parallelism configuration, from its own ``system_desc.json`` (§8.2).

    §8.1 places ``system_desc.json`` in each point directory as the "framework /
    parallelism / precision for this point", and §4.5.3's worked example (C.3) changes
    the replica size from point to point, so it is read per point, not per curve.
    """

    tensor_parallel: int = 1
    pipeline_parallel: int = 1
    expert_parallel: int = 1
    data_parallel: int = 1
    disaggregated: bool = False

    @property
    def replica(self) -> int:
        """§4.5.3's ``A_replica = TP × PP × EP``."""
        return self.tensor_parallel * self.pipeline_parallel * self.expert_parallel

    @property
    def accelerators_used(self) -> int:
        """§4.5.3's ``accelerators_used = DP × A_replica``."""
        return self.data_parallel * self.replica


class PointPower(BaseModel):
    """Validates one point's engagement and normalised throughput against §4.5.3."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    _check_results: list[CheckResult] = PrivateAttr(default_factory=list)
    _nodes_by_set: dict[int, int] | None = PrivateAttr(default=None)

    config: PointConfig
    power: PowerComputation
    #: ``None`` where the point's description declares no parallelism at all.
    parallelism: Parallelism | None
    system_tps: float
    stored_tps_per_kw: float | None
    yaml_path: Path
    summary_path: Path

    @property
    def _a_provisioned(self) -> int | None:
        """``A_provisioned``: Σ N_s × accelerators per node, if every set states it."""
        if any(s.accelerators_per_node is None for s in self.power.sets):
            return None
        return sum(s.nodes_provisioned * (s.accelerators_per_node or 0) for s in self.power.sets)

    @model_validator(mode="after")
    def _check_nodes_used(self) -> PointPower:
        """§9.1 "Per-point node declaration"."""
        declared = self.config.nodes_used
        if declared is None:
            self._nodes_by_set = {}
            return self

        problems: list[str] = []
        by_set: dict[int, int] = {}
        seen: set[int] = set()
        for entry in declared:
            ensemble = entry.system_node_ensemble_id
            if ensemble in seen:
                problems.append(f"system_node_ensemble_id {ensemble} is declared twice")
                continue
            seen.add(ensemble)
            matches = [s for s in self.power.sets if s.system_node_ensemble_id == ensemble]
            if len(matches) != 1:
                problems.append(
                    f"system_node_ensemble_id {ensemble} matches {len(matches)} node sets in"
                    " system_power.json; it must resolve to exactly one"
                )
                continue
            node_set = matches[0]
            if entry.nodes < 1 or entry.nodes > node_set.nodes_provisioned:
                problems.append(
                    f"system_node_ensemble_id {ensemble}: nodes {entry.nodes} must be a"
                    f" positive whole number no greater than N_s = {node_set.nodes_provisioned}"
                )
                continue
            by_set[node_set.node_set_id] = entry.nodes

        if not problems:
            problems.extend(self._capacity_problems(by_set))
        if problems:
            self._check_results.append(
                err("nodes-used", "; ".join(problems), self.yaml_path, "#4.5.3")
            )
            return self

        self._nodes_by_set = by_set
        summary = ", ".join(
            f"set {s.node_set_id}: {by_set.get(s.node_set_id, s.nodes_provisioned)}"
            f"/{s.nodes_provisioned}"
            for s in self.power.sets
        )
        self._check_results.append(
            ok("nodes-used", f"nodes_used {summary}", self.yaml_path, "#4.5.3")
        )
        return self

    def _capacity_problems(self, by_set: dict[int, int]) -> list[str]:
        """The declared nodes must hold every accelerator the parallelism engages.

        §4.5.3 sets ``Y_s = ceil(accelerators_used_s / accelerators_per_node_s)``, but a
        point's parallelism is declared for the whole system, so for a heterogeneous
        system the split across sets is not knowable. What is checkable for any number
        of sets is that the declared nodes *hold* the engaged accelerators — fewer
        understates the deployment, which §4.5.3 calls a misrepresentation. Declaring
        more than needed only overstates the denominator, so it is a warning.
        """
        para = self.parallelism
        if para is None or para.disaggregated:
            return []
        if any(s.accelerators_per_node is None for s in self.power.sets):
            return []
        engaged = {
            s.node_set_id: by_set.get(s.node_set_id, s.nodes_provisioned) for s in self.power.sets
        }
        capacity = sum(
            engaged[s.node_set_id] * (s.accelerators_per_node or 0) for s in self.power.sets
        )
        used = para.accelerators_used
        if capacity < used:
            return [
                f"the declared nodes hold {capacity} accelerators, but the point's parallelism"
                f" (DP {para.data_parallel} × TP×PP×EP {para.replica}) engages {used}"
            ]
        spare = [
            s
            for s in self.power.sets
            if engaged[s.node_set_id] > 0 and capacity - (s.accelerators_per_node or 0) >= used
        ]
        if spare:
            if len(self.power.sets) == 1:
                needed = ceil(used / (spare[0].accelerators_per_node or 1))
                detail = f"ceil({used} / {spare[0].accelerators_per_node}) = {needed} nodes"
            else:
                detail = f"{used} accelerators fit in fewer nodes"
            self._check_results.append(
                warn(
                    "nodes-used",
                    f"nodes_used declares more nodes than the point engages ({detail});"
                    " §4.5.3 rounds up from the accelerators engaged, and the extra nodes"
                    " only raise this point's denominator",
                    self.yaml_path,
                    "#4.5.3",
                )
            )
        return []

    @model_validator(mode="after")
    def _check_maximal_engagement(self) -> PointPower:
        """§9.1 "Maximal engagement": ``DP = floor(A_provisioned / A_replica)``."""
        para = self.parallelism
        label = f"Point {self.config.concurrency}"
        if para is None:
            self._check_results.append(
                warn(
                    "maximal-engagement",
                    f"{label}: system_desc.json declares no tensor/pipeline/expert/data"
                    " parallelism, so §4.5.3's engagement cannot be checked",
                    self.yaml_path,
                    "#4.5.3",
                )
            )
            return self
        if para.disaggregated:
            # TP × PP × EP is one replica's footprint only where prefill and decode share
            # it; for a disaggregated deployment the rules do not say what A_replica is.
            self._check_results.append(
                warn(
                    "maximal-engagement",
                    f"{label}: disaggregated serving — §4.5.3's A_replica = TP × PP × EP"
                    " does not describe a prefill/decode split, so engagement is left to"
                    " peer review",
                    self.yaml_path,
                    "#4.5.3",
                )
            )
            return self
        a_prov = self._a_provisioned
        if a_prov is None:
            self._check_results.append(
                warn(
                    "maximal-engagement",
                    f"{label}: accelerators per node are not known for every node set, so"
                    " A_provisioned cannot be derived",
                    self.yaml_path,
                    "#4.5.3",
                )
            )
            return self

        a_replica = para.replica
        dp = para.data_parallel
        if a_replica < 1 or dp < 1:
            self._check_results.append(
                err(
                    "maximal-engagement",
                    f"{label}: TP, PP, EP and DP must each be at least 1",
                    self.yaml_path,
                    "#4.5.3",
                )
            )
            return self
        dp_max = a_prov // a_replica
        formula = f"floor({a_prov} / {a_replica}) = {dp_max}"
        shortfall = self.config.dp_shortfall

        if dp > dp_max:
            self._check_results.append(
                err(
                    "maximal-engagement",
                    f"{label}: DP {dp} × A_replica {a_replica} = {dp * a_replica}"
                    f" accelerators, more than the {a_prov} provisioned ({formula})",
                    self.yaml_path,
                    "#4.5.3",
                )
            )
        elif dp == dp_max:
            message = f"{label}: DP {dp} = {formula}, engaging {dp * a_replica} of {a_prov}"
            if shortfall is not None:
                self._check_results.append(
                    warn(
                        "maximal-engagement",
                        f"{message}; dp_shortfall is declared but there is no shortfall",
                        self.yaml_path,
                        "#4.5.3",
                    )
                )
            else:
                self._check_results.append(
                    ok("maximal-engagement", message, self.yaml_path, "#4.5.3")
                )
        elif shortfall is None:
            self._check_results.append(
                err(
                    "maximal-engagement",
                    f"{label}: DP {dp} is below {formula}, and the point declares no"
                    " dp_shortfall (§8.3)",
                    self.yaml_path,
                    "#4.5.3",
                )
            )
        elif shortfall.dp_actual != dp or shortfall.dp_formula != dp_max:
            self._check_results.append(
                err(
                    "maximal-engagement",
                    f"{label}: dp_shortfall declares DP {shortfall.dp_actual} of"
                    f" {shortfall.dp_formula}, but the point runs DP {dp} of {formula}",
                    self.yaml_path,
                    "#4.5.3",
                )
            )
        else:
            self._check_results.append(
                warn(
                    "maximal-engagement",
                    f"{label}: DP {dp} below {formula}, declared as a shortfall:"
                    f" {shortfall.reason!r} — subject to Methodology objection at peer review",
                    self.yaml_path,
                    "#4.5.3",
                )
            )
        return self

    @model_validator(mode="after")
    def _check_tps_per_kw(self) -> PointPower:
        """§9.1 "Per-point denominator": ``system_tps_per_kw = system_tps / point_power_kw``."""
        if self._nodes_by_set is None:
            return self  # an invalid nodes_used gives no denominator to check against
        kw = self.power.point_power_kw(self._nodes_by_set)
        if kw is None or kw <= 0:
            return self
        derived = self.system_tps / kw
        basis = "full provisioned power" if not self._nodes_by_set else "nodes_used"
        stored = self.stored_tps_per_kw
        if stored is not None:
            rel_err = abs(stored - derived) / max(abs(derived), 1e-9)
            if rel_err > _TPS_PER_KW_TOLERANCE:
                self._check_results.append(
                    err(
                        "metric-consistency-tps-per-kw",
                        f"stored system_tps_per_kw {stored:.4f} ≠ derived"
                        f" system_tps / point_power_kw {kw:.2f} ({basis}) = {derived:.4f}"
                        f" (rel err {rel_err:.1%})",
                        self.summary_path,
                        "#4.5.3",
                    )
                )
                return self
        self._check_results.append(
            ok(
                "metric-consistency-tps-per-kw",
                f"system_tps_per_kw={derived:.4f} ({self.system_tps:.3f} /"
                f" point_power_kw {kw:.2f}, {basis})",
                self.summary_path,
                "#4.5.3",
            )
        )
        return self
