"""Baseline, QoS, and full MedRoute path selection."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import networkx as nx

from braess_validator import ActiveFlow, BraessResult, BraessValidator
from qos_metrics import Edge, LinkMetrics, MissingMetricsError, PathEvaluation, QoSCostCalculator


@dataclass(frozen=True)
class RoutingDecision:
    selected_path: Tuple[int, ...]
    previous_path: Optional[Tuple[int, ...]]
    mode: str
    reason: str
    evaluations: Tuple[PathEvaluation, ...]
    braess_results: Tuple[Tuple[Tuple[int, ...], BraessResult], ...]
    changed: bool


class RoutingEngine:
    MODES = {"baseline", "qos", "medroute"}

    def __init__(
        self,
        max_candidates: int = 6,
        minimum_improvement: float = 0.05,
        cooldown_seconds: float = 10.0,
    ) -> None:
        self.max_candidates = max_candidates
        self.minimum_improvement = minimum_improvement
        self.cooldown_seconds = cooldown_seconds
        self.calculator = QoSCostCalculator()
        self.braess = BraessValidator(self.calculator)

    def candidate_paths(
        self, graph: nx.DiGraph, source: int, destination: int
    ) -> List[Tuple[int, ...]]:
        try:
            paths = nx.shortest_simple_paths(graph, source, destination)
            return [tuple(path) for _, path in zip(range(self.max_candidates), paths)]
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    def select_path(
        self,
        graph: nx.DiGraph,
        source: int,
        destination: int,
        link_metrics: Mapping[Edge, LinkMetrics],
        profile: Mapping[str, object],
        mode: str = "medroute",
        current_path: Optional[Sequence[int]] = None,
        active_flows: Sequence[ActiveFlow] = (),
        flow_rate_bps: float = 1_000_000.0,
        last_change_at: float = 0.0,
        now: Optional[float] = None,
    ) -> RoutingDecision:
        if mode not in self.MODES:
            raise ValueError("mode must be one of {}".format(sorted(self.MODES)))
        candidates = self.candidate_paths(graph, source, destination)
        if not candidates:
            raise ValueError("No candidate path from {} to {}".format(source, destination))
        previous = tuple(current_path) if current_path else None
        if mode == "baseline":
            selected = candidates[0]
            return RoutingDecision(selected, previous, mode, "fewest-hop NetworkX baseline", (), (), selected != previous)

        if mode == "medroute":
            topology_edges = tuple(graph.edges())
            incomplete_edges = tuple(
                edge for edge in topology_edges
                if edge not in link_metrics or not link_metrics[edge].complete
            )
            if not topology_edges or incomplete_edges:
                complete_count = len(topology_edges) - len(incomplete_edges)
                return self._telemetry_fallback(
                    candidates,
                    previous,
                    mode,
                    "telemetry incomplete ({}/{} topology links complete); "
                    "QoS/Braess validation deferred".format(complete_count, len(topology_edges)),
                )

        weights = profile["weights"]
        evaluations: List[PathEvaluation] = []
        for path in candidates:
            try:
                evaluations.append(self.calculator.evaluate(path, link_metrics, weights))  # type: ignore[arg-type]
            except MissingMetricsError:
                continue
        if not evaluations:
            return self._telemetry_fallback(
                candidates, previous, mode,
                "telemetry incomplete for every candidate path; QoS evaluation deferred",
            )

        braess_results: List[Tuple[Tuple[int, ...], BraessResult]] = []
        if mode == "medroute":
            for evaluation in evaluations:
                try:
                    result = self.braess.validate(
                        evaluation.path, previous, flow_rate_bps, weights,
                        link_metrics, active_flows  # type: ignore[arg-type]
                    )
                except MissingMetricsError as error:
                    return self._telemetry_fallback(
                        candidates,
                        previous,
                        mode,
                        "telemetry incomplete during Braess validation ({}); "
                        "QoS/Braess validation deferred".format(error),
                        evaluations=tuple(evaluations),
                    )
                braess_results.append((evaluation.path, result))
            safe_paths = {path for path, result in braess_results if result.safe}
            eligible = [evaluation for evaluation in evaluations if evaluation.path in safe_paths]
            if not eligible:
                selected = previous if previous in candidates else candidates[0]
                return RoutingDecision(selected, previous, mode,
                                       "all measured candidates are BRAESS_RISK; retained safe state",
                                       tuple(evaluations), tuple(braess_results), selected != previous)
        else:
            eligible = evaluations

        best = min(eligible, key=lambda item: item.cost)
        selected = best.path
        current_evaluation = next((item for item in evaluations if item.path == previous), None)
        clock = time.time() if now is None else now
        if previous and current_evaluation and selected != previous:
            improvement = (current_evaluation.cost - best.cost) / max(current_evaluation.cost, 1e-12)
            if improvement < self.minimum_improvement:
                selected = previous
                reason = "kept current path: {:.2%} improvement is below {:.2%} hysteresis".format(
                    improvement, self.minimum_improvement
                )
            elif clock - last_change_at < self.cooldown_seconds:
                selected = previous
                reason = "kept current path during {:.0f}s reroute cooldown".format(self.cooldown_seconds)
            else:
                reason = "selected lowest-cost safe path with {:.2%} improvement".format(improvement)
        else:
            reason = "selected lowest measured QoS cost{}".format(" after Braess validation" if mode == "medroute" else "")
        return RoutingDecision(selected, previous, mode, reason, tuple(evaluations),
                               tuple(braess_results), selected != previous)

    @staticmethod
    def _telemetry_fallback(
        candidates: Sequence[Tuple[int, ...]],
        previous: Optional[Tuple[int, ...]],
        mode: str,
        detail: str,
        evaluations: Tuple[PathEvaluation, ...] = (),
    ) -> RoutingDecision:
        if previous in candidates:
            selected = previous
            action = "retained current path"
        else:
            selected = candidates[0]
            action = "selected deterministic shortest path"
        reason = "TELEMETRY WARMUP/FALLBACK: {}; {}".format(detail, action)
        return RoutingDecision(
            selected, previous, mode, reason, evaluations, (), selected != previous
        )

    @staticmethod
    def format_evaluation(evaluation: PathEvaluation) -> str:
        m = evaluation.metrics
        n = evaluation.normalized
        return (
            "PATH {} | latency={:.3f} ms jitter={:.3f} ms loss={:.3f}% "
            "utilization={:.3f}% | normalized={} | cost={:.6f}"
        ).format("->".join(map(str, evaluation.path)), m.latency_ms, m.jitter_ms,
                 m.packet_loss_pct, m.utilization_pct,
                 {name: round(value, 6) for name, value in n.items()}, evaluation.cost)
