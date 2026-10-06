import math
import unittest

from qos_metrics import LinkMetrics, MetricNormalizer, MissingMetricsError, QoSCostCalculator


def metric(latency, jitter, loss, utilization, throughput=10_000_000):
    return LinkMetrics(latency, jitter, loss, utilization, throughput,
                       100_000_000 - throughput, 100_000_000, 1.0, "test-fixture")


class QoSMetricsTests(unittest.TestCase):
    def setUp(self):
        self.calculator = QoSCostCalculator()
        self.links = {
            (1, 2): metric(10, 3, 1, 40),
            (2, 4): metric(20, 4, 2, 60),
        }

    def test_path_aggregation_uses_correct_units_and_composition(self):
        path = self.calculator.aggregate_path((1, 2, 4), self.links)
        self.assertEqual(path.latency_ms, 30)
        self.assertEqual(path.jitter_ms, 5)
        self.assertAlmostEqual(path.packet_loss_pct, 2.98, places=6)
        self.assertEqual(path.utilization_pct, 60)

    def test_fixed_normalization_and_weighted_cost(self):
        weights = {"latency": 0.35, "jitter": 0.30, "loss": 0.25, "utilization": 0.10}
        evaluation = self.calculator.evaluate((1, 2, 4), self.links, weights)
        self.assertAlmostEqual(evaluation.normalized["latency"], 0.30)
        self.assertAlmostEqual(evaluation.normalized["jitter"], 0.10)
        self.assertAlmostEqual(evaluation.normalized["loss"], 2.98 / 5)
        expected = 0.35 * 0.30 + 0.30 * 0.10 + 0.25 * (2.98 / 5) + 0.10 * 0.60
        self.assertAlmostEqual(evaluation.cost, expected)

    def test_incomplete_telemetry_is_rejected(self):
        links = {(1, 2): LinkMetrics(None, None, 0, 0)}
        with self.assertRaises(MissingMetricsError):
            self.calculator.aggregate_path((1, 2), links)


if __name__ == "__main__":
    unittest.main()

