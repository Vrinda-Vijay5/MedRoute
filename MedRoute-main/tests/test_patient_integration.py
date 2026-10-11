import json
import tempfile
import unittest
from pathlib import Path

from patient_integration import (
    DemonstrationCriticalityPolicy, PatientValidationError, validate_update,
)
from storage import ResultsStore


def payload(timestamp=100.0):
    return {
        "source_id": "wearable-test-01",
        "source_type": "test-generator",
        "interface": "http-json",
        "timestamp": timestamp,
        "freshness_seconds": 10,
        "readings": [{"metric": "priority_signal", "value": 5, "unit": "level"}],
        "flow_binding": {
            "source_ip": "10.0.0.1", "destination_ip": "10.0.0.2",
            "ip_protocol": 17, "destination_port": 5001,
        },
    }


class PatientIntegrationTests(unittest.TestCase):
    def test_validation_policy_storage_and_expiry(self):
        update = validate_update(payload(), "test", now=100.0)
        policy = DemonstrationCriticalityPolicy()
        criticality, reason = policy.evaluate(update.readings)
        self.assertEqual(criticality, 5)
        with tempfile.TemporaryDirectory() as directory:
            store = ResultsStore(Path(directory) / "test.db")
            store.record_patient_update(
                update.source_id, update.source_type, update.interface,
                update.operating_mode, update.freshness_seconds, update.readings,
                update.flow_binding, criticality, reason, policy.name,
                update.timestamp, 100.0, update.metadata,
            )
            active = store.resolve_live_criticality(
                "10.0.0.1", "10.0.0.2", 17, 42000, 5001, now=105.0
            )
            stale = store.resolve_live_criticality(
                "10.0.0.1", "10.0.0.2", 17, 42000, 5001, now=111.0
            )
            store.close()
        self.assertEqual(active["criticality"], 5)
        self.assertEqual(active["operating_mode"], "test")
        self.assertIsNone(stale)

    def test_rejects_identifying_metadata_and_bad_units(self):
        identifying = payload()
        identifying["metadata"] = {"patient_name": "not accepted"}
        with self.assertRaises(PatientValidationError):
            validate_update(identifying, "test", now=100.0)
        bad_unit = payload()
        bad_unit["readings"][0]["unit"] = "bpm"
        with self.assertRaises(PatientValidationError):
            validate_update(bad_unit, "test", now=100.0)


if __name__ == "__main__":
    unittest.main()
