import unittest

from braess_validator import ActiveFlow, BraessValidator
from qos_metrics import LinkMetrics


WEIGHTS = {"latency": 0.35, "jitter": 0.30, "loss": 0.25, "utilization": 0.10}


def measured(latency, jitter, loss, utilization):
    capacity = 100_000_000
    throughput = capacity * utilization / 100
    return LinkMetrics(latency, jitter, loss, utilization, throughput,
                       capacity - throughput, capacity, 1.0, "test-fixture")


class BraessValidatorTests(unittest.TestCase):
    def setUp(self):
        self.links = {
            (1, 2): measured(8, 1, 0.1, 40),
            (2, 4): measured(8, 1, 0.1, 40),
            (1, 3): measured(5, 1, 0.1, 70),
            (3, 4): measured(5, 1, 0.1, 70),
        }
        self.validator = BraessValidator()

    def test_rejects_candidate_that_harms_a_protected_flow(self):
        protected = ActiveFlow("icu-1", (1, 3, 4), "ICU", 5, 10_000_000, WEIGHTS)
        result = self.validator.validate((1, 3, 4), (1, 2, 4), 20_000_000,
                                         WEIGHTS, self.links, [protected])
        self.assertFalse(result.safe)
        self.assertEqual(result.status, "BRAESS_RISK")
        self.assertIn("protected flow", result.reason)

    def test_accepts_low_load_candidate_with_no_protected_degradation(self):
        result = self.validator.validate((1, 2, 4), None, 1_000_000, WEIGHTS, self.links)
        self.assertTrue(result.safe)
        self.assertEqual(result.status, "SAFE")

    def test_dedicated_topology_rejects_central_route_and_accepts_other_branch(self):
        links = {
            (1, 2): measured(0.5, 0.1, 0.0, 2.0),
            (2, 4): measured(12.0, 0.1, 0.0, 0.0),
            (1, 3): measured(12.0, 0.1, 0.0, 0.0),
            (3, 4): measured(0.5, 0.1, 0.0, 0.0),
            (2, 3): measured(0.5, 0.1, 0.0, 0.0),
        }
        protected = ActiveFlow(
            "ecg-protected", (1, 2, 4), "ECG", 5, 500_000, WEIGHTS
        )
        imaging_weights = {
            "latency": 0.15, "jitter": 0.05, "loss": 0.20, "utilization": 0.60,
        }

        central = self.validator.validate(
            (1, 2, 3, 4), None, 20_000_000, imaging_weights, links, [protected]
        )
        other_branch = self.validator.validate(
            (1, 3, 4), None, 20_000_000, imaging_weights, links, [protected]
        )

        self.assertFalse(central.safe)
        self.assertEqual(central.status, "BRAESS_RISK")
        self.assertIn("protected flow", central.reason)
        self.assertTrue(other_branch.safe)


if __name__ == "__main__":
    unittest.main()
