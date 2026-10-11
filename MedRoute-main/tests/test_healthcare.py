import csv
import tempfile
import unittest
from pathlib import Path

from healthcare_data import (
    CsvHealthcareDataSource,
    SyntheticHealthcareDataSource,
    load_healthcare_workload_profiles,
)
from healthcare_pipeline import HealthcarePipeline
from traffic_classifier import (
    HealthcareTrafficClassifier,
    build_flow_id,
    ipv4_openflow_match_fields,
)


class HealthcareTests(unittest.TestCase):
    def test_udp_5001_is_ecg_criticality_five(self):
        profile = HealthcareTrafficClassifier().classify_udp_port(5001)
        self.assertEqual(profile["traffic_type"], "ECG")
        self.assertEqual(profile["criticality"], 5)

    def test_all_configured_healthcare_ports_classify_correctly(self):
        classifier = HealthcareTrafficClassifier()
        expected = {
            5001: ("ECG", 5),
            5002: ("ICU", 5),
            5003: ("TELEMEDICINE", 4),
            5004: ("EHR", 4),
            5005: ("IMAGING", 3),
            5006: ("CCTV", 1),
        }
        for destination_port, (traffic_type, criticality) in expected.items():
            with self.subTest(destination_port=destination_port):
                profile = classifier.classify_udp_port(destination_port)
                self.assertEqual(profile["traffic_type"], traffic_type)
                self.assertEqual(profile["criticality"], criticality)

    def test_unknown_traffic_remains_unknown(self):
        classifier = HealthcareTrafficClassifier()
        for destination_port in (None, 0, 6001):
            with self.subTest(destination_port=destination_port):
                profile = classifier.classify_udp_port(destination_port)
                self.assertEqual(profile["traffic_type"], "UNKNOWN")
                self.assertEqual(profile["criticality"], 0)

    def test_flow_key_and_openflow_match_preserve_protocol_and_ports(self):
        flow_id = build_flow_id("10.0.0.1", "10.0.0.2", 49152, 5001)
        self.assertEqual(flow_id, "10.0.0.1:49152->10.0.0.2:5001")
        udp_fields = ipv4_openflow_match_fields(
            "10.0.0.1", "10.0.0.2", 17, 49152, 5001
        )
        self.assertEqual(udp_fields["ip_proto"], 17)
        self.assertEqual(udp_fields["udp_src"], 49152)
        self.assertEqual(udp_fields["udp_dst"], 5001)

        tcp_fields = ipv4_openflow_match_fields(
            "10.0.0.1", "10.0.0.2", 6, 49153, 443
        )
        self.assertEqual(tcp_fields["ip_proto"], 6)
        self.assertEqual(tcp_fields["tcp_src"], 49153)
        self.assertEqual(tcp_fields["tcp_dst"], 443)

        icmp_fields = ipv4_openflow_match_fields(
            "10.0.0.1", "10.0.0.2", 1
        )
        self.assertEqual(icmp_fields["ip_proto"], 1)
        self.assertNotIn("udp_dst", icmp_fields)
        self.assertEqual(
            build_flow_id("10.0.0.1", "10.0.0.2"),
            "10.0.0.1:0->10.0.0.2:0",
        )

    def test_all_profiles_are_explainable_and_normalized(self):
        classifier = HealthcareTrafficClassifier()
        self.assertEqual(set(classifier.TRAFFIC_TYPES),
                         {"ECG", "ICU", "TELEMEDICINE", "EHR", "IMAGING", "CCTV"})
        for name in classifier.TRAFFIC_TYPES:
            profile = classifier.classify(name.lower())
            self.assertIn(profile["criticality"], range(1, 6))
            self.assertAlmostEqual(sum(profile["weights"].values()), 1.0)
            self.assertTrue(profile["reason"])
            self.assertEqual(classifier.classify_udp_port(profile["udp_port"])["traffic_type"], name)

    def test_synthetic_source_is_repeatable_when_seeded(self):
        first = [record.traffic_type for record in SyntheticHealthcareDataSource(7).records(8)]
        second = [record.traffic_type for record in SyntheticHealthcareDataSource(7).records(8)]
        self.assertEqual(first, second)

    def test_workload_dataset_is_complete_and_matches_classifier_policy(self):
        profiles = load_healthcare_workload_profiles()
        classifier = HealthcareTrafficClassifier()
        self.assertEqual(set(profiles), set(classifier.TRAFFIC_TYPES))
        for name, workload in profiles.items():
            with self.subTest(traffic_type=name):
                policy = classifier.classify(name)
                self.assertEqual(workload["criticality"], policy["criticality"])
                self.assertEqual(workload["destination_port"], policy["udp_port"])
                self.assertEqual(workload["transport_protocol"], "UDP")
                self.assertGreater(workload["offered_rate_bps"], 0)
                self.assertEqual(
                    SyntheticHealthcareDataSource.DEFAULT_RATES[name],
                    workload["offered_rate_bps"],
                )
        for record in SyntheticHealthcareDataSource(seed=2).records(30):
            workload = profiles[record.traffic_type]
            self.assertEqual(record.payload_bytes, workload["packet_size_bytes"])
            self.assertEqual(record.expected_rate_bps, workload["offered_rate_bps"])
            self.assertEqual(record.destination_port, workload["destination_port"])
            self.assertEqual(record.criticality, workload["criticality"])
            self.assertEqual(record.duration_seconds, workload["duration_seconds"])

    def test_csv_source_and_pipeline_share_interface(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "traffic.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=("record_id", "traffic_type", "timestamp"))
                writer.writeheader()
                writer.writerow({"record_id": "r1", "traffic_type": "ECG", "timestamp": "1.5"})
            result = HealthcarePipeline(CsvHealthcareDataSource(path)).process(None)
        self.assertEqual(result[0]["traffic_type"], "ECG")
        self.assertEqual(result[0]["criticality"], 5)
        self.assertEqual(result[0]["destination_port"], 5001)
        self.assertEqual(result[0]["expected_rate_bps"], 500_000)


if __name__ == "__main__":
    unittest.main()
