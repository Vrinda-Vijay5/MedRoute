import tempfile
import unittest
from pathlib import Path

from dashboard_server import DashboardRepository
from storage import ResultsStore


class DashboardTests(unittest.TestCase):
    def test_missing_database_is_explicitly_disconnected(self):
        with tempfile.TemporaryDirectory() as directory:
            state = DashboardRepository(Path(directory) / "missing.db").snapshot()
        self.assertEqual(state["state"], "disconnected")
        self.assertIn("not found", state["reason"])

    def test_snapshot_decodes_routes_and_labels_metric_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "medroute.db"
            store = ResultsStore(path)
            store.record_status("controller", "ready", "medroute", "measured", {})
            store.record_decision("f1", (1, 3, 4), None, "medroute", "test", True)
            store.close()
            state = DashboardRepository(path).snapshot()
        self.assertEqual(state["decisions"][0]["selected_path"], [1, 3, 4])
        self.assertEqual(state["status"][0]["data_kind"], "measured")


if __name__ == "__main__":
    unittest.main()
