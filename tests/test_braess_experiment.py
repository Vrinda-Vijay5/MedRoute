import tempfile
import time
import unittest
from pathlib import Path

from braess_experiment import (
    _latest_selected_path,
    _read_completed_udp_report,
    _wait_for_telemetry_ready,
)
from storage import ResultsStore


class FakeHost:
    def __init__(self, files):
        self.files = files

    def cmd(self, command):
        path = command.split("cat ", 1)[1].split(" ", 1)[0]
        return self.files.get(path, "")


class BraessExperimentLifecycleTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
