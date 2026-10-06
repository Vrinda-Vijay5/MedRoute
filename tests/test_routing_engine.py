import unittest
from types import SimpleNamespace

import networkx as nx

from network_monitor import NetworkMonitor
from qos_metrics import LinkMetrics
from routing_engine import RoutingEngine
from traffic_classifier import HealthcareTrafficClassifier


def measured(latency, jitter, loss, utilization):
    return LinkMetrics(latency, jitter, loss, utilization, 5_000_000,
                       95_000_000, 100_000_000, 100.0, "test-fixture")


class RoutingEngineTests(unittest.TestCase):
    def setUp(self):
        self.graph = nx.DiGraph(((1, 2), (2, 4), (1, 3), (3, 4)))
        self.metrics = {
            (1, 2): measured(20, 8, 2, 80),
            (2, 4): measured(20, 8, 2, 80),
            (1, 3): measured(5, 1, 0.1, 20),
            (3, 4): measured(5, 1, 0.1, 20),
        }
        self.profile = HealthcareTrafficClassifier().classify("ECG")

    def test_baseline_preserves_fewest_hop_routing(self):
        decision = RoutingEngine().select_path(
            self.graph, 1, 4, self.metrics, self.profile, mode="baseline"
        )
        self.assertEqual(len(decision.selected_path), 3)
        self.assertEqual(decision.reason, "fewest-hop NetworkX baseline")

    def test_qos_selects_lower_measured_cost(self):
        decision = RoutingEngine().select_path(
            self.graph, 1, 4, self.metrics, self.profile, mode="qos"
        )
        self.assertEqual(decision.selected_path, (1, 3, 4))
        self.assertEqual(len(decision.evaluations), 2)

    def test_missing_metrics_fall_back_without_invention(self):
        engine = RoutingEngine()
        baseline = engine.select_path(
            self.graph, 1, 4, {}, self.profile, mode="baseline"
        )
        decision = engine.select_path(
            self.graph, 1, 4, {}, self.profile, mode="medroute"
        )
        self.assertEqual(decision.selected_path, baseline.selected_path)
        self.assertIn("telemetry incomplete", decision.reason)
        self.assertFalse(decision.evaluations)

    def test_hysteresis_prevents_small_improvement_route_flap(self):
        close_metrics = dict(self.metrics)
        close_metrics[(1, 2)] = measured(5.2, 1, 0.1, 20)
        close_metrics[(2, 4)] = measured(5.2, 1, 0.1, 20)
        engine = RoutingEngine(minimum_improvement=0.10, cooldown_seconds=0)
        decision = engine.select_path(
            self.graph, 1, 4, close_metrics, self.profile, mode="qos",
            current_path=(1, 2, 4), last_change_at=0, now=100,
        )
        self.assertEqual(decision.selected_path, (1, 2, 4))
        self.assertIn("hysteresis", decision.reason)

    def test_partial_startup_telemetry_cannot_escape_braess_validation(self):
        partial_metrics = {
            edge: LinkMetrics(None, None, None, None, timestamp=1.0,
                              sample_source="partial-test-fixture")
            for edge in self.graph.edges()
        }
        decision = RoutingEngine().select_path(
            self.graph, 1, 1, partial_metrics, self.profile, mode="medroute"
        )
        self.assertEqual(decision.selected_path, (1,))
        self.assertTrue(decision.reason.startswith("TELEMETRY WARMUP/FALLBACK"))
        self.assertIn("telemetry incomplete", decision.reason)
        self.assertFalse(decision.evaluations)
        self.assertFalse(decision.braess_results)

    def test_complete_telemetry_resumes_qos_and_braess_after_warmup(self):
        decision = RoutingEngine().select_path(
            self.graph, 1, 1, self.metrics, self.profile, mode="medroute"
        )
        self.assertEqual(decision.selected_path, (1,))
        self.assertEqual(len(decision.evaluations), 1)
        self.assertEqual(len(decision.braess_results), 1)
        self.assertTrue(decision.braess_results[0][1].safe)
        self.assertNotIn("TELEMETRY WARMUP/FALLBACK", decision.reason)
        self.assertIn("Braess validation", decision.reason)

    def test_late_missing_metric_from_old_route_is_converted_to_fallback(self):
        engine = RoutingEngine()
        baseline = engine.select_path(
            self.graph, 1, 4, self.metrics, self.profile, mode="baseline"
        )
        decision = engine.select_path(
            self.graph, 1, 4, self.metrics, self.profile, mode="medroute",
            current_path=(1, 5, 4),
        )
        self.assertEqual(decision.selected_path, baseline.selected_path)
        self.assertTrue(decision.reason.startswith("TELEMETRY WARMUP/FALLBACK"))
        self.assertIn("telemetry incomplete during Braess validation", decision.reason)
        self.assertEqual(len(decision.evaluations), 2)
        self.assertFalse(decision.braess_results)

    def test_measured_monitor_pipeline_transitions_from_warmup_to_medroute(self):
        graph = nx.DiGraph(((1, 2),))
        monitor = NetworkMonitor()
        monitor.register_link(1, 2, 10, 20)
        engine = RoutingEngine(cooldown_seconds=0)

        warmup = engine.select_path(
            graph, 1, 2, monitor.snapshot(include_stale=True), self.profile,
            mode="medroute",
        )
        self.assertTrue(warmup.reason.startswith("TELEMETRY WARMUP/FALLBACK"))

        monitor.record_echo_rtt(1, 0.2, timestamp=1.0)
        monitor.record_echo_rtt(2, 0.2, timestamp=1.0)
        monitor.record_lldp_delay(1, 2, 0.001, timestamp=1.0)
        monitor.record_lldp_delay(1, 2, 0.001, timestamp=1.1)
        first_source = SimpleNamespace(port_no=10, tx_bytes=0, rx_bytes=0,
                                       tx_packets=0, rx_packets=0)
        first_destination = SimpleNamespace(port_no=20, tx_bytes=0, rx_bytes=0,
                                            tx_packets=0, rx_packets=0)
        monitor.process_port_stats(1, [first_source], timestamp=1.0)
        monitor.process_port_stats(2, [first_destination], timestamp=1.0)
        monitor.process_port_stats(1, [first_source], timestamp=2.0)
        monitor.process_port_stats(2, [first_destination], timestamp=2.0)

        measured_decision = engine.select_path(
            graph, 1, 2, monitor.snapshot(include_stale=True), self.profile,
            mode="medroute", current_path=warmup.selected_path,
        )
        self.assertNotIn("TELEMETRY WARMUP/FALLBACK", measured_decision.reason)
        self.assertEqual(len(measured_decision.evaluations), 1)
        self.assertEqual(len(measured_decision.braess_results), 1)
        self.assertTrue(measured_decision.braess_results[0][1].safe)


if __name__ == "__main__":
    unittest.main()
