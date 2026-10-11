"""Measured link metrics, path aggregation, normalization, and QoS cost."""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass
from typing import Dict, Mapping, Optional, Sequence, Tuple

Edge = Tuple[int, int]


class MissingMetricsError(ValueError):
    pass


@dataclass(frozen=True)
class LinkMetrics:
    latency_ms: Optional[float]
    jitter_ms: Optional[float]
    packet_loss_pct: Optional[float]
    utilization_pct: Optional[float]
    throughput_bps: float = 0.0
    available_bandwidth_bps: float = 0.0
    capacity_bps: float = 100_000_000.0
    timestamp: float = 0.0
    sample_source: str = "openflow+lldp"

    def __post_init__(self) -> None:
        for field_name in ("latency_ms", "jitter_ms", "packet_loss_pct", "utilization_pct"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError("{} cannot be negative".format(field_name))
        if self.packet_loss_pct is not None and self.packet_loss_pct > 100:
            raise ValueError("packet_loss_pct cannot exceed 100")
        if self.utilization_pct is not None and self.utilization_pct > 100:
            raise ValueError("utilization_pct cannot exceed 100")

    @property
    def complete(self) -> bool:
        return all(value is not None for value in (
            self.latency_ms, self.jitter_ms, self.packet_loss_pct, self.utilization_pct
        ))


@dataclass(frozen=True)
class PathMetrics:
    latency_ms: float
    jitter_ms: float
    packet_loss_pct: float
    utilization_pct: float
    throughput_bps: float
    available_bandwidth_bps: float


@dataclass(frozen=True)
class PathEvaluation:
    path: Tuple[int, ...]
    metrics: PathMetrics
    normalized: Dict[str, float]
    weights: Dict[str, float]
    cost: float

    def to_dict(self) -> Dict[str, object]:
        return {
            "path": list(self.path),
            "metrics": asdict(self.metrics),
            "normalized": dict(self.normalized),
            "weights": dict(self.weights),
            "cost": self.cost,
        }


class MetricNormalizer:
    """Normalize unlike units against documented engineering reference limits.

    Fixed references keep costs comparable across routes, experiments, and
    time. Values are clipped to [0, 1], so severe values saturate instead of
    dominating solely because of their unit scale.
    """

    REFERENCE_LIMITS = {
        "latency": 100.0,      # milliseconds, path total
        "jitter": 50.0,        # milliseconds, path root-sum-square
        "loss": 5.0,           # percent, end-to-end
        "utilization": 100.0,  # percent, path bottleneck
    }

    def normalize(self, metrics: PathMetrics) -> Dict[str, float]:
        raw = {
            "latency": metrics.latency_ms,
            "jitter": metrics.jitter_ms,
            "loss": metrics.packet_loss_pct,
            "utilization": metrics.utilization_pct,
        }
        return {
            name: min(1.0, max(0.0, value / self.REFERENCE_LIMITS[name]))
            for name, value in raw.items()
        }


class QoSCostCalculator:
    def __init__(self, normalizer: Optional[MetricNormalizer] = None) -> None:
        self.normalizer = normalizer or MetricNormalizer()

    @staticmethod
    def path_edges(path: Sequence[int]) -> Tuple[Edge, ...]:
        return tuple(zip(path, path[1:]))

    def aggregate_path(
        self, path: Sequence[int], link_metrics: Mapping[Edge, LinkMetrics]
    ) -> PathMetrics:
        edges = self.path_edges(path)
        if not edges:
            return PathMetrics(0.0, 0.0, 0.0, 0.0, 0.0, float("inf"))
        missing = [edge for edge in edges if edge not in link_metrics or not link_metrics[edge].complete]
        if missing:
            raise MissingMetricsError("Incomplete measured metrics for links: {}".format(missing))
        samples = [link_metrics[edge] for edge in edges]
        delivery_probability = math.prod(1.0 - sample.packet_loss_pct / 100.0 for sample in samples)  # type: ignore[operator]
        positive_throughputs = [sample.throughput_bps for sample in samples if sample.throughput_bps > 0]
        return PathMetrics(
            latency_ms=sum(sample.latency_ms for sample in samples),  # type: ignore[arg-type]
            jitter_ms=math.sqrt(sum(sample.jitter_ms ** 2 for sample in samples)),  # type: ignore[operator]
            packet_loss_pct=100.0 * (1.0 - delivery_probability),
            utilization_pct=max(sample.utilization_pct for sample in samples),  # type: ignore[arg-type]
            throughput_bps=min(positive_throughputs) if positive_throughputs else 0.0,
            available_bandwidth_bps=min(sample.available_bandwidth_bps for sample in samples),
        )

    def evaluate(
        self,
        path: Sequence[int],
        link_metrics: Mapping[Edge, LinkMetrics],
        weights: Mapping[str, float],
    ) -> PathEvaluation:
        expected = set(self.normalizer.REFERENCE_LIMITS)
        if set(weights) != expected:
            raise ValueError("weights must contain exactly {}".format(sorted(expected)))
        if not math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9):
            raise ValueError("QoS weights must sum to 1.0")
        metrics = self.aggregate_path(path, link_metrics)
        normalized = self.normalizer.normalize(metrics)
        cost = sum(float(weights[name]) * normalized[name] for name in expected)
        return PathEvaluation(tuple(path), metrics, normalized, dict(weights), cost)


def metric_age_seconds(metric: LinkMetrics, now: Optional[float] = None) -> float:
    return (now or time.time()) - metric.timestamp

