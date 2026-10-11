import sqlite3
import tempfile
import unittest
from pathlib import Path

from storage import ResultsStore


class StorageTests(unittest.TestCase):
    def test_schema_and_flow_upsert(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.db"
            store = ResultsStore(path)
            store.upsert_flow("f1", "ECG", 5, "10.0.0.1", "10.0.0.2", 500_000, 1.0)
            store.upsert_flow("f1", "ECG", 5, "10.0.0.1", "10.0.0.2", 500_000, 2.0)
            store.close()
            connection = sqlite3.connect(str(path))
            try:
                count, last_seen = connection.execute(
                    "SELECT COUNT(*), MAX(last_seen) FROM flows"
                ).fetchone()
            finally:
                connection.close()
        self.assertEqual(count, 1)
        self.assertEqual(last_seen, 2.0)


if __name__ == "__main__":
    unittest.main()
