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
        weights = {"latency": 0.30, "jitter": 0.25, "loss": 0.25,
                   "utilization": 0.10, "throughput": 0.10}
        evaluation = self.calculator.evaluate(
            (1, 2, 4), self.links, weights, requested_rate_bps=10_000_000
        )
        self.assertAlmostEqual(evaluation.normalized["latency"], 0.30)
        self.assertAlmostEqual(evaluation.normalized["jitter"], 0.10)
        self.assertAlmostEqual(evaluation.normalized["loss"], 2.98 / 5)
        # Residual path capacity is 90 Mbit/s, so the 10 Mbit/s requested
        # demand contributes a normalized ratio of 1/9.
        expected = (0.30 * 0.30 + 0.25 * 0.10 + 0.25 * (2.98 / 5)
                    + 0.10 * 0.60 + 0.10 * (1 / 9))
        self.assertAlmostEqual(evaluation.cost, expected)

    def test_demand_adequacy_affects_route_ranking_independently(self):
        links = {
            (1, 2): LinkMetrics(5, 1, 0.1, 20, 20_000_000, 80_000_000,
                                100_000_000, 1.0, "fixture"),
            (1, 3): LinkMetrics(5, 1, 0.1, 20, 20_000_000, 20_000_000,
                                100_000_000, 1.0, "fixture"),
        }
        weights = {"latency": 0.0, "jitter": 0.0, "loss": 0.0,
                   "utilization": 0.0, "throughput": 1.0}
        adequate = self.calculator.evaluate(
            (1, 2), links, weights, requested_rate_bps=10_000_000
        )
        constrained = self.calculator.evaluate(
            (1, 3), links, weights, requested_rate_bps=10_000_000
        )
        self.assertLess(adequate.cost, constrained.cost)

    def test_each_required_cost_component_can_change_ranking(self):
        low = LinkMetrics(5, 1, 0.1, 10, 5_000_000, 90_000_000,
                          100_000_000, 1.0, "fixture")
        variants = {
            "latency": LinkMetrics(80, 1, 0.1, 10, 5_000_000, 90_000_000,
                                   100_000_000, 1.0, "fixture"),
            "jitter": LinkMetrics(5, 40, 0.1, 10, 5_000_000, 90_000_000,
                                  100_000_000, 1.0, "fixture"),
            "loss": LinkMetrics(5, 1, 4, 10, 5_000_000, 90_000_000,
                                100_000_000, 1.0, "fixture"),
            "utilization": LinkMetrics(5, 1, 0.1, 90, 5_000_000, 90_000_000,
                                       100_000_000, 1.0, "fixture"),
        }
        names = ("latency", "jitter", "loss", "utilization", "throughput")
        for name, high in variants.items():
            with self.subTest(metric=name):
                weights = {key: float(key == name) for key in names}
                low_cost = self.calculator.evaluate((1, 2), {(1, 2): low}, weights).cost
                high_cost = self.calculator.evaluate((1, 2), {(1, 2): high}, weights).cost
                self.assertLess(low_cost, high_cost)

    def test_incomplete_telemetry_is_rejected(self):
        links = {(1, 2): LinkMetrics(None, None, 0, 0)}
        with self.assertRaises(MissingMetricsError):
            self.calculator.aggregate_path((1, 2), links)


if __name__ == "__main__":
    unittest.main()
