"""OpenFlow/LLDP measurement state used by the MedRoute controller.

OpenFlow port counters provide throughput, utilization, available bandwidth,
and directional packet-loss estimates. Corrected LLDP observations provide
link latency; variation between consecutive samples provides jitter. No
metric is filled with a random or synthetic value.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Tuple

from qos_metrics import Edge, LinkMetrics


def find_lldp_send_timestamp(
    port_data_items: Iterable[Tuple[object, object]], source: int, source_port: int
) -> Optional[float]:
    """Find Ryu's PortData timestamp without constructing a Ryu Port.

    Ryu 4.34's Port constructor needs an OpenFlow protocol object and a full
    OFPPort object. The topology service already owns the correct Port keys,
    so matching their DPID and port number is the compatible lookup.
    """
    source = int(source)
    source_port = int(source_port)
    for port, data in port_data_items:
        if int(port.dpid) == source and int(port.port_no) == source_port:
            return getattr(data, "timestamp", None)
    return None


@dataclass(frozen=True)
class PortRate:
    timestamp: float
    tx_bps: float
    rx_bps: float
    tx_pps: float
    rx_pps: float


@dataclass(frozen=True)
class CounterSample:
    timestamp: float
    tx_bytes: int
    rx_bytes: int
    tx_packets: int
    rx_packets: int


@dataclass(frozen=True)
class TelemetryReadiness:
    """Readiness of the exact directed edges used by the routing graph."""

    complete: int
    total: int
    missing: Mapping[Edge, Tuple[str, ...]]

    @property
    def ready(self) -> bool:
        return self.total > 0 and self.complete == self.total


class NetworkMonitor:
    DEFAULT_CAPACITY_BPS = 100_000_000.0

    def __init__(self, stale_after_seconds: float = 10.0) -> None:
        self.stale_after_seconds = stale_after_seconds
        self._lock = threading.RLock()
        self._links: Dict[Edge, Tuple[int, int]] = {}
        self._capacities: Dict[Tuple[int, int], float] = {}
        self._configured_capacities = set()
        self._counters: Dict[Tuple[int, int], CounterSample] = {}
        self._rates: Dict[Tuple[int, int], PortRate] = {}
        self._delay_ms: Dict[Edge, float] = {}
        self._delay_timestamps: Dict[Edge, float] = {}
        self._jitter_ms: Dict[Edge, float] = {}
        self._metrics: Dict[Edge, LinkMetrics] = {}
        self._echo_rtt_ms: Dict[int, float] = {}
        self._echo_timestamps: Dict[int, float] = {}

    def register_link(
        self, source: int, destination: int, source_port: int, destination_port: int,
        capacity_bps: Optional[float] = None,
    ) -> None:
        edge = (int(source), int(destination))
        source_port = int(source_port)
        destination_port = int(destination_port)
        with self._lock:
            self._links[edge] = (source_port, destination_port)
            capacity_key = (edge[0], source_port)
            if capacity_bps is not None:
                if capacity_bps <= 0:
                    raise ValueError("link capacity must be positive")
                self._capacities[capacity_key] = float(capacity_bps)
                self._configured_capacities.add(capacity_key)
            else:
                self._capacities.setdefault(capacity_key, self.DEFAULT_CAPACITY_BPS)

    def remove_missing_links(self, active_edges: Iterable[Edge]) -> None:
        active = {(int(source), int(destination)) for source, destination in active_edges}
        with self._lock:
            for edge in set(self._links).difference(active):
                ports = self._links.pop(edge, None)
                if ports is not None:
                    capacity_key = (edge[0], ports[0])
                    self._capacities.pop(capacity_key, None)
                    self._configured_capacities.discard(capacity_key)
                self._metrics.pop(edge, None)
                self._delay_ms.pop(edge, None)
                self._delay_timestamps.pop(edge, None)
                self._jitter_ms.pop(edge, None)

    def update_port_capacity(self, dpid: int, port_no: int, capacity_bps: float) -> None:
        if capacity_bps <= 0:
            return
        with self._lock:
            key = (int(dpid), int(port_no))
            if key not in self._configured_capacities:
                self._capacities[key] = capacity_bps

    def record_echo_rtt(
        self, dpid: int, rtt_ms: float, timestamp: Optional[float] = None
    ) -> None:
        if rtt_ms >= 0:
            with self._lock:
                dpid = int(dpid)
                self._echo_rtt_ms[dpid] = rtt_ms
                self._echo_timestamps[dpid] = time.time() if timestamp is None else timestamp

    def echo_ready(
        self, source: int, destination: int, now: Optional[float] = None
    ) -> bool:
        """Return whether both switches have real echo RTT observations."""
        clock = time.time() if now is None else now
        with self._lock:
            return all(
                dpid in self._echo_rtt_ms
                and clock - self._echo_timestamps.get(dpid, 0.0) <= self.stale_after_seconds
                for dpid in (int(source), int(destination))
            )

    def record_lldp_delay(
        self, source: int, destination: int, observed_seconds: float, timestamp: Optional[float] = None
    ) -> None:
        """Record one-way link delay after removing controller/switch echo time."""
        measured_at = time.time() if timestamp is None else timestamp
        edge = (int(source), int(destination))
        with self._lock:
            correction_ms = (
                self._echo_rtt_ms.get(edge[0], 0.0) + self._echo_rtt_ms.get(edge[1], 0.0)
            ) / 2.0
            corrected_ms = max(0.0, observed_seconds * 1000.0 - correction_ms)
            previous = self._delay_ms.get(edge)
            self._delay_ms[edge] = corrected_ms
            timing_contributors = [measured_at]
            timing_contributors.extend(
                self._echo_timestamps[dpid]
                for dpid in edge if dpid in self._echo_timestamps
            )
            self._delay_timestamps[edge] = min(timing_contributors)
            if previous is not None:
                sample_jitter = abs(corrected_ms - previous)
                old_jitter = self._jitter_ms.get(edge, sample_jitter)
                self._jitter_ms[edge] = 0.8 * old_jitter + 0.2 * sample_jitter
            self._recalculate_links(measured_at)

    def process_port_stats(self, dpid: int, stats: Iterable[object], timestamp: Optional[float] = None) -> None:
        measured_at = time.time() if timestamp is None else timestamp
        dpid = int(dpid)
        with self._lock:
            for stat in stats:
                port_no = int(stat.port_no)
                key = (dpid, port_no)
                sample = CounterSample(
                    measured_at, int(stat.tx_bytes), int(stat.rx_bytes),
                    int(stat.tx_packets), int(stat.rx_packets),
                )
                previous = self._counters.get(key)
                self._counters[key] = sample
                if previous is None:
                    continue
                elapsed = sample.timestamp - previous.timestamp
                if elapsed <= 0:
                    continue
                self._rates[key] = PortRate(
                    timestamp=measured_at,
                    tx_bps=max(0.0, (sample.tx_bytes - previous.tx_bytes) * 8.0 / elapsed),
                    rx_bps=max(0.0, (sample.rx_bytes - previous.rx_bytes) * 8.0 / elapsed),
                    tx_pps=max(0.0, (sample.tx_packets - previous.tx_packets) / elapsed),
                    rx_pps=max(0.0, (sample.rx_packets - previous.rx_packets) / elapsed),
                )
            self._recalculate_links(measured_at)

    def _recalculate_links(self, timestamp: float) -> None:
        for edge, ports in self._links.items():
            source_port, destination_port = ports
            source_rate = self._rates.get((edge[0], source_port))
            destination_rate = self._rates.get((edge[1], destination_port))
            capacity = self._capacities.get((edge[0], source_port), self.DEFAULT_CAPACITY_BPS)
            if source_rate is None or destination_rate is None:
                continue
            throughput = min(source_rate.tx_bps, destination_rate.rx_bps)
            utilization = min(100.0, 100.0 * source_rate.tx_bps / max(capacity, 1.0))
            loss = 0.0
            if source_rate.tx_pps > 0:
                loss = 100.0 * max(0.0, source_rate.tx_pps - destination_rate.rx_pps) / source_rate.tx_pps
            delay_timestamp = self._delay_timestamps.get(edge)
            contributing_timestamps = [source_rate.timestamp, destination_rate.timestamp]
            if delay_timestamp is not None:
                contributing_timestamps.append(delay_timestamp)
            self._metrics[edge] = LinkMetrics(
                latency_ms=self._delay_ms.get(edge),
                jitter_ms=self._jitter_ms.get(edge),
                packet_loss_pct=min(100.0, loss),
                utilization_pct=utilization,
                throughput_bps=throughput,
                available_bandwidth_bps=max(0.0, capacity - source_rate.tx_bps),
                capacity_bps=capacity,
                # The oldest contributor determines freshness. Repeated port
                # statistics cannot make an old LLDP observation look fresh.
                timestamp=min(contributing_timestamps),
                sample_source="openflow-port-stats+corrected-lldp",
            )

    def snapshot(self, include_stale: bool = False, now: Optional[float] = None) -> Dict[Edge, LinkMetrics]:
        clock = time.time() if now is None else now
        with self._lock:
            return {
                edge: metric for edge, metric in self._metrics.items()
                if include_stale or clock - metric.timestamp <= self.stale_after_seconds
            }

    def readiness(
        self, active_edges: Iterable[Edge], now: Optional[float] = None
    ) -> TelemetryReadiness:
        """Explain missing/stale contributors for routing-graph edges."""
        clock = time.time() if now is None else now
        edges = tuple((int(source), int(destination)) for source, destination in active_edges)
        missing: Dict[Edge, Tuple[str, ...]] = {}
        with self._lock:
            for edge in edges:
                reasons = []
                ports = self._links.get(edge)
                if ports is None:
                    reasons.append("link_not_registered")
                else:
                    source_key = (edge[0], ports[0])
                    destination_key = (edge[1], ports[1])
                    for label, key in (("source", source_key), ("destination", destination_key)):
                        if key not in self._counters:
                            reasons.append("{}_port_counter_missing".format(label))
                        rate = self._rates.get(key)
                        if rate is None:
                            reasons.append("{}_port_rate_missing_second_sample".format(label))
                        elif clock - rate.timestamp > self.stale_after_seconds:
                            reasons.append("{}_port_rate_stale".format(label))
                for label, dpid in (("source", edge[0]), ("destination", edge[1])):
                    if dpid not in self._echo_rtt_ms:
                        reasons.append("{}_echo_rtt_missing".format(label))
                    elif clock - self._echo_timestamps.get(dpid, 0.0) > self.stale_after_seconds:
                        reasons.append("{}_echo_rtt_stale".format(label))
                delay_timestamp = self._delay_timestamps.get(edge)
                if delay_timestamp is None:
                    reasons.append("corrected_lldp_latency_missing")
                elif clock - delay_timestamp > self.stale_after_seconds:
                    reasons.append("corrected_lldp_latency_stale")
                metric = self._metrics.get(edge)
                if metric is None:
                    reasons.append("link_metric_not_computed")
                elif not metric.complete:
                    for field_name in (
                        "latency_ms", "jitter_ms", "packet_loss_pct", "utilization_pct"
                    ):
                        if getattr(metric, field_name) is None:
                            reasons.append("{}_missing".format(field_name))
                elif clock - metric.timestamp > self.stale_after_seconds:
                    reasons.append("link_metric_stale")
                if reasons:
                    missing[edge] = tuple(dict.fromkeys(reasons))
        return TelemetryReadiness(len(edges) - len(missing), len(edges), missing)

    def diagnostics(self) -> Mapping[str, int]:
        with self._lock:
            complete = sum(metric.complete for metric in self._metrics.values())
            return {"registered_links": len(self._links), "measured_links": len(self._metrics),
                    "complete_links": complete, "port_rates": len(self._rates),
                    "lldp_links": len(self._delay_ms), "echo_switches": len(self._echo_rtt_ms)}
