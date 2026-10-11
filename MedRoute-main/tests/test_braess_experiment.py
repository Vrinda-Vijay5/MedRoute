import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from braess_experiment import (
    _background_iperf_command,
    _capture_flow_tables,
    _latest_selected_path,
    _prepare_trial_topology,
    _read_completed_udp_report,
    _wait_for_telemetry_ready,
)
from storage import ResultsStore
from braess_config import (
    ECG_RATE_BPS,
    ECG_UDP_PORT,
    IMAGING_FLOW_PAIRS,
    IMAGING_RATE_BPS,
    IMAGING_UDP_PORT,
    INTERSWITCH_CAPACITY_BPS,
    INTERSWITCH_CAPACITY_MBPS,
    iperf_rate,
)
from traffic_classifier import HealthcareTrafficClassifier


class FakeHost:
    def __init__(self, files):
        self.files = files

    def cmd(self, command):
        path = command.split("cat ", 1)[1].split(" ", 1)[0]
        return self.files.get(path, "")


class FakeProbeNode:
    def __init__(self, name, ip_address=None):
        self.name = name
        self.ip_address = ip_address
        self.commands = []

    def IP(self):
        return self.ip_address

    def cmd(self, command):
        self.commands.append(command)
        return ""


class FakeProbeNetwork(dict):
    @property
    def switches(self):
        return [node for name, node in self.items() if name.startswith("s")]


class FakeLifecycleNetwork:
    def __init__(self, events):
        self.events = events

    def pingAll(self):
        self.events.append(("pingAll",))
        return 0.0


class BraessExperimentLifecycleTests(unittest.TestCase):
    def test_dedicated_capacity_is_shared_by_links_controller_and_evidence(self):
        root = Path(__file__).resolve().parents[1]
        topology_source = (root / "braess_topology.py").read_text(encoding="utf-8")
        experiment_source = (root / "braess_experiment.py").read_text(encoding="utf-8")
        runner_source = (root / "scripts/run_braess_experiment.sh").read_text(
            encoding="utf-8"
        )

        self.assertGreater(INTERSWITCH_CAPACITY_MBPS, 0)
        self.assertEqual(
            INTERSWITCH_CAPACITY_BPS,
            INTERSWITCH_CAPACITY_MBPS * 1_000_000,
        )
        self.assertEqual(
            topology_source.count("bw=INTERSWITCH_CAPACITY_MBPS"), 5
        )
        self.assertIn("INTERSWITCH_CAPACITY_BPS", experiment_source)
        self.assertIn("INTERSWITCH_CAPACITY_MBPS", experiment_source)
        self.assertIn("INTERSWITCH_CAPACITY_BPS", runner_source)
        self.assertIn('MEDROUTE_LINK_CAPACITY_BPS="$capacity_bps"', runner_source)
        classifier = HealthcareTrafficClassifier()
        self.assertEqual(classifier.classify_udp_port(ECG_UDP_PORT)["traffic_type"], "ECG")
        self.assertEqual(classifier.classify_udp_port(IMAGING_UDP_PORT)["traffic_type"], "IMAGING")
        self.assertEqual(iperf_rate(ECG_RATE_BPS), "2M")
        self.assertEqual(iperf_rate(IMAGING_RATE_BPS), "20M")
        self.assertEqual(len(IMAGING_FLOW_PAIRS) * IMAGING_RATE_BPS, 100_000_000)
        command = _background_iperf_command("10.0.0.5", 15, "h2")
        self.assertIn("-c 10.0.0.5 -u -b 20M -t 15 -p 5005", command)
        self.assertIn("medroute-braess-h2-client.log", command)

    def test_candidate_is_enabled_only_after_successful_pingall(self):
        events = []
        net = FakeLifecycleNetwork(events)

        def set_candidate_link(_net, enabled):
            events.append(("candidate", enabled))

        def telemetry_ready(_log, expected_links, after_offset=0):
            events.append(("ready", expected_links))

        with tempfile.TemporaryDirectory() as directory:
            controller_log = Path(directory) / "controller.log"
            controller_log.write_text("controller output\n", encoding="utf-8")
            with patch(
                "braess_experiment._wait_for_telemetry_ready",
                side_effect=telemetry_ready,
            ):
                _prepare_trial_topology(
                    net, True, controller_log, set_candidate_link
                )

        self.assertEqual(events, [
            ("candidate", False),
            ("ready", 8),
            ("pingAll",),
            ("candidate", True),
            ("ready", 10),
        ])

    def test_empty_protected_output_reports_status_and_stderr(self):
        host = FakeHost({
            "/tmp/output.json": "",
            "/tmp/error.log": "iperf3: error - unable to connect to server",
            "/tmp/status": "1\n",
        })
        with self.assertRaisesRegex(
            RuntimeError, "exit status 1.*unable to connect to server"
        ):
            _read_completed_udp_report(
                host, "/tmp/output.json", "/tmp/error.log", "/tmp/status"
            )

    def test_waits_for_exact_controller_telemetry_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "controller.log"
            log_path.write_text(
                "TELEMETRY STATUS READY complete=8/8\n"
                "TELEMETRY STATUS READY complete=10/10\n",
                encoding="utf-8",
            )
            _wait_for_telemetry_ready(
                log_path, expected_links=10, timeout_seconds=0.1
            )

    def test_reads_current_trial_selected_ecg_path(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "results.db"
            store = ResultsStore(database)
            observed = time.time()
            flow_id = "10.0.0.1:42060->10.0.0.4:5001"
            store.upsert_flow(
                flow_id, "ECG", 5, "10.0.0.1", "10.0.0.4", 2_000_000,
                timestamp=observed,
            )
            store.record_decision(
                flow_id, (1, 2, 4), None, "medroute", "measured", True
            )
            store.close()
            self.assertEqual(
                _latest_selected_path(
                    database, "10.0.0.1", "10.0.0.4", "ECG", observed - 1
                ),
                (1, 2, 4),
            )

    def test_flow_table_capture_is_read_only(self):
        net = FakeProbeNetwork({
            "s1": FakeProbeNode("s1"),
            "s2": FakeProbeNode("s2"),
            "s4": FakeProbeNode("s4"),
        })
        captured = _capture_flow_tables(net)
        self.assertEqual(set(captured), {"s1", "s2", "s4"})
        for switch in net.switches:
            self.assertEqual(len(switch.commands), 1)
            self.assertIn(" dump-flows ", switch.commands[0])
            self.assertNotIn(" add-flow ", switch.commands[0])
            self.assertNotIn(" del-flows ", switch.commands[0])


if __name__ == "__main__":
    unittest.main()
