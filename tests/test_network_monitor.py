import unittest
from types import SimpleNamespace

from network_monitor import NetworkMonitor, find_lldp_send_timestamp


def stat(port, tx_bytes, rx_bytes, tx_packets, rx_packets):
    return SimpleNamespace(port_no=port, tx_bytes=tx_bytes, rx_bytes=rx_bytes,
                           tx_packets=tx_packets, rx_packets=rx_packets)


class NetworkMonitorTests(unittest.TestCase):
    def test_ryu_lldp_timestamp_uses_existing_topology_port_key(self):
        ryu_port = SimpleNamespace(dpid=1, port_no=10)
        ryu_port_data = SimpleNamespace(timestamp=123.5)
        timestamp = find_lldp_send_timestamp(
            [(ryu_port, ryu_port_data)], source=1, source_port=10
        )
        self.assertEqual(timestamp, 123.5)
        self.assertIsNone(find_lldp_send_timestamp(
            [(ryu_port, ryu_port_data)], source=1, source_port=11
        ))

    def test_openflow_counter_deltas_create_real_link_metrics(self):
        monitor = NetworkMonitor()
        monitor.register_link(1, 2, 10, 20, 100_000_000)
        monitor.record_lldp_delay(1, 2, 0.005, timestamp=1.0)
        monitor.record_lldp_delay(1, 2, 0.005, timestamp=1.1)
        monitor.process_port_stats(1, [stat(10, 0, 0, 0, 0)], timestamp=1.0)
        monitor.process_port_stats(2, [stat(20, 0, 0, 0, 0)], timestamp=1.0)
        monitor.process_port_stats(1, [stat(10, 1_000_000, 0, 1000, 0)], timestamp=2.0)
        monitor.process_port_stats(2, [stat(20, 0, 900_000, 0, 900)], timestamp=2.0)
        metric = monitor.snapshot(include_stale=True)[(1, 2)]
        self.assertTrue(metric.complete)
        self.assertAlmostEqual(metric.latency_ms, 5.0)
        self.assertAlmostEqual(metric.throughput_bps, 7_200_000)
        self.assertAlmostEqual(metric.utilization_pct, 8.0)
        self.assertAlmostEqual(metric.packet_loss_pct, 10.0)

    def test_telemetry_progresses_and_topology_keys_match_metric_keys(self):
        monitor = NetworkMonitor(stale_after_seconds=10.0)
        # Numeric strings model values crossing a serialization/API boundary;
        # production NetworkX and Ryu keys are normalized to integer DPIDs.
        monitor.register_link("1", "2", "10", "20", 100_000_000)
        readiness = monitor.readiness([(1, 2)], now=1.0)
        self.assertEqual((readiness.complete, readiness.total), (0, 1))
        self.assertIn("source_port_rate_missing_second_sample", readiness.missing[(1, 2)])

        monitor.record_echo_rtt(1, 0.2, timestamp=1.0)
        monitor.record_echo_rtt(2, 0.2, timestamp=1.0)
        monitor.record_lldp_delay(1, 2, 0.001, timestamp=1.0)
        monitor.record_lldp_delay(1, 2, 0.001, timestamp=1.1)
        monitor.process_port_stats(1, [stat(10, 0, 0, 0, 0)], timestamp=1.0)
        monitor.process_port_stats(2, [stat(20, 0, 0, 0, 0)], timestamp=1.0)
        self.assertEqual(monitor.readiness([(1, 2)], now=1.0).complete, 0)

        # A second real counter observation creates measured zero rates. Zero
        # traffic is data, not missing telemetry.
        monitor.process_port_stats(1, [stat(10, 0, 0, 0, 0)], timestamp=2.0)
        monitor.process_port_stats(2, [stat(20, 0, 0, 0, 0)], timestamp=2.0)
        readiness = monitor.readiness([(1, 2)], now=2.0)
        self.assertTrue(readiness.ready)
        self.assertEqual((readiness.complete, readiness.total), (1, 1))
        metric = monitor.snapshot(now=2.0)[(1, 2)]
        self.assertTrue(metric.complete)
        self.assertEqual(metric.throughput_bps, 0.0)
        self.assertEqual(metric.utilization_pct, 0.0)
        self.assertEqual(metric.packet_loss_pct, 0.0)
        self.assertEqual(metric.jitter_ms, 0.0)

    def test_stale_lldp_cannot_be_hidden_by_fresh_port_statistics(self):
        monitor = NetworkMonitor(stale_after_seconds=5.0)
        monitor.register_link(1, 2, 10, 20)
        monitor.record_echo_rtt(1, 0.1, timestamp=1.0)
        monitor.record_echo_rtt(2, 0.1, timestamp=1.0)
        monitor.record_lldp_delay(1, 2, 0.001, timestamp=1.0)
        monitor.record_lldp_delay(1, 2, 0.001, timestamp=1.1)
        monitor.process_port_stats(1, [stat(10, 0, 0, 0, 0)], timestamp=9.0)
        monitor.process_port_stats(2, [stat(20, 0, 0, 0, 0)], timestamp=9.0)
        monitor.process_port_stats(1, [stat(10, 0, 0, 0, 0)], timestamp=10.0)
        monitor.process_port_stats(2, [stat(20, 0, 0, 0, 0)], timestamp=10.0)
        readiness = monitor.readiness([(1, 2)], now=10.0)
        self.assertFalse(readiness.ready)
        self.assertIn("corrected_lldp_latency_stale", readiness.missing[(1, 2)])
        self.assertNotIn((1, 2), monitor.snapshot(now=10.0))

    def test_configured_testbed_capacity_is_not_overwritten_by_port_description(self):
        monitor = NetworkMonitor()
        monitor.register_link(1, 2, 10, 20, capacity_bps=100_000_000)
        monitor.update_port_capacity(1, 10, 10_000_000_000)
        monitor.record_lldp_delay(1, 2, 0.001, timestamp=1.0)
        monitor.record_lldp_delay(1, 2, 0.001, timestamp=1.1)
        monitor.process_port_stats(1, [stat(10, 0, 0, 0, 0)], timestamp=1.0)
        monitor.process_port_stats(2, [stat(20, 0, 0, 0, 0)], timestamp=1.0)
        monitor.process_port_stats(
            1, [stat(10, 2_500_000, 0, 1000, 0)], timestamp=2.0
        )
        monitor.process_port_stats(
            2, [stat(20, 0, 2_500_000, 0, 1000)], timestamp=2.0
        )
        metric = monitor.snapshot(include_stale=True)[(1, 2)]
        self.assertEqual(metric.capacity_bps, 100_000_000)
        self.assertEqual(metric.utilization_pct, 20.0)


if __name__ == "__main__":
    unittest.main()
