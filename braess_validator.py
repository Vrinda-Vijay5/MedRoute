"""Pre-deployment validation for Braess-type network degradation.

The validator uses measured current link state plus a deterministic load
projection. It asks whether moving one flow to an attractive candidate route
would harm protected flows or aggregate network QoS. It does not use random
thresholds, AI, game theory, or fabricated measurements.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, Mapping, Optional, Sequence, Tuple

from qos_metrics import Edge, LinkMetrics, MissingMetricsError, QoSCostCalculator


@dataclass(frozen=True)
class ActiveFlow:
    flow_id: str
    path: Tuple[int, ...]
    traffic_type: str
    criticality: int
    rate_bps: float
    weights: Dict[str, float]


@dataclass(frozen=True)
class BraessResult:
    safe: bool
    status: str
    reason: str
    current_flow_cost: Optional[float]
    projected_flow_cost: float
    aggregate_cost_before: float
    aggregate_cost_after: float
    aggregate_change_pct: float
    worst_protected_change_pct: float
    max_projected_utilization_pct: float


class BraessValidator:
    """Reject routes that create measurable/projected systemic degradation."""

    AGGREGATE_TOLERANCE_PCT = 8.0
    MAX_PROJECTED_UTILIZATION_PCT = 95.0
    PROTECTED_TOLERANCE_PCT = {5: 5.0, 4: 10.0, 3: 15.0, 2: 20.0, 1: 25.0, 0: 25.0}
    NETWORK_WEIGHTS = {"latency": 0.25, "jitter": 0.25, "loss": 0.25, "utilization": 0.25}

    def __init__(self, calculator: Optional[QoSCostCalculator] = None) -> None:
        self.calculator = calculator or QoSCostCalculator()

    def validate(
        self,
        candidate_path: Sequence[int],
        current_path: Optional[Sequence[int]],
        flow_rate_bps: float,
        flow_weights: Mapping[str, float],
        link_metrics: Mapping[Edge, LinkMetrics],
        active_flows: Sequence[ActiveFlow] = (),
    ) -> BraessResult:
        projected = self._project_state(link_metrics, current_path, candidate_path, flow_rate_bps)
        candidate_after = self.calculator.evaluate(candidate_path, projected, flow_weights).cost
        current_cost = None
        if current_path:
            current_cost = self.calculator.evaluate(current_path, link_metrics, flow_weights).cost

        before = self._aggregate_network_cost(link_metrics)
        after = self._aggregate_network_cost(projected)
        aggregate_change = self._percent_change(before, after)
        max_utilization = max(
            (metric.utilization_pct or 0.0) for metric in projected.values()
        ) if projected else 0.0

        worst_protected = 0.0
        protected_reason = ""
        for flow in active_flows:
            if flow.criticality < 4:
                continue
            try:
                flow_before = self.calculator.evaluate(flow.path, link_metrics, flow.weights).cost
                flow_after = self.calculator.evaluate(flow.path, projected, flow.weights).cost
            except MissingMetricsError:
                continue
            change = self._percent_change(flow_before, flow_after)
            worst_protected = max(worst_protected, change)
            allowed = self.PROTECTED_TOLERANCE_PCT.get(flow.criticality, 10.0)
            if change > allowed:
                protected_reason = (
                    "protected flow {} (criticality {}) would worsen by {:.2f}% > {:.2f}%"
                ).format(flow.flow_id, flow.criticality, change, allowed)
                break

        if max_utilization > self.MAX_PROJECTED_UTILIZATION_PCT:
            return self._result(False, "BRAESS_RISK", "projected link utilization {:.2f}% exceeds {:.2f}%".format(
                max_utilization, self.MAX_PROJECTED_UTILIZATION_PCT
            ), current_cost, candidate_after, before, after, aggregate_change, worst_protected, max_utilization)
        if protected_reason:
            return self._result(False, "BRAESS_RISK", protected_reason, current_cost, candidate_after,
                                before, after, aggregate_change, worst_protected, max_utilization)
        attractive = current_cost is not None and candidate_after < current_cost
        if attractive and aggregate_change > self.AGGREGATE_TOLERANCE_PCT:
            reason = "candidate benefits this flow but aggregate QoS cost worsens by {:.2f}% > {:.2f}%".format(
                aggregate_change, self.AGGREGATE_TOLERANCE_PCT
            )
            return self._result(False, "BRAESS_RISK", reason, current_cost, candidate_after,
                                before, after, aggregate_change, worst_protected, max_utilization)
        return self._result(True, "SAFE", "projected state stays within utilization, protected-flow, and aggregate limits",
                            current_cost, candidate_after, before, after, aggregate_change,
                            worst_protected, max_utilization)

    def _project_state(
        self,
        measured: Mapping[Edge, LinkMetrics],
        current_path: Optional[Sequence[int]],
        candidate_path: Sequence[int],
        flow_rate_bps: float,
    ) -> Dict[Edge, LinkMetrics]:
        old_edges = set(self.calculator.path_edges(current_path or ()))
        new_edges = set(self.calculator.path_edges(candidate_path))
        projected = {}
        for edge, sample in measured.items():
            capacity = max(sample.capacity_bps, 1.0)
            utilization = sample.utilization_pct or 0.0
            delta_pct = 100.0 * flow_rate_bps / capacity
            target = utilization
            if edge in old_edges and edge not in new_edges:
                target -= delta_pct
            if edge in new_edges and edge not in old_edges:
                target += delta_pct
            target = min(100.0, max(0.0, target))
            projected[edge] = self._project_metric(sample, target)
        return projected

    @staticmethod
    def _project_metric(sample: LinkMetrics, target_utilization: float) -> LinkMetrics:
        current = min(sample.utilization_pct or 0.0, 99.0)
        target = min(target_utilization, 99.0)
        # Queueing delay grows approximately as 1/(1-rho). Scaling the measured
        # value preserves the testbed's real base delay while projecting load.
        queue_ratio = (1.0 - current / 100.0) / max(0.01, 1.0 - target / 100.0)
        latency = (sample.latency_ms or 0.0) * queue_ratio
        jitter = (sample.jitter_ms or 0.0) * max(1.0, queue_ratio)
        congestion_loss = max(0.0, target - 80.0) * 0.25
        loss = min(100.0, max(sample.packet_loss_pct or 0.0, congestion_loss))
        throughput = target / 100.0 * sample.capacity_bps
        return replace(
            sample,
            latency_ms=latency,
            jitter_ms=jitter,
            packet_loss_pct=loss,
            utilization_pct=target_utilization,
            throughput_bps=throughput,
            available_bandwidth_bps=max(0.0, sample.capacity_bps - throughput),
            sample_source=sample.sample_source + "+projection",
        )

    def _aggregate_network_cost(self, metrics: Mapping[Edge, LinkMetrics]) -> float:
        complete = [sample for sample in metrics.values() if sample.complete]
        if not complete:
            raise MissingMetricsError("No complete link metrics for Braess validation")
        costs = []
        for index, sample in enumerate(complete):
            synthetic_edge = {(index, index + 1): sample}
            costs.append(self.calculator.evaluate((index, index + 1), synthetic_edge, self.NETWORK_WEIGHTS).cost)
        return sum(costs) / len(costs)

    @staticmethod
    def _percent_change(before: float, after: float) -> float:
        if before <= 1e-12:
            return 0.0 if after <= 1e-12 else 100.0
        return 100.0 * (after - before) / before

    @staticmethod
    def _result(safe, status, reason, current, projected, before, after, change, protected, utilization):
        return BraessResult(safe, status, reason, current, projected, before, after, change, protected, utilization)

