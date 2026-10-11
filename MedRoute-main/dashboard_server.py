"""Read-only local dashboard API and static-file server."""

from __future__ import annotations

import argparse
import json
import math
import mimetypes
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


class DashboardRepository:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database).resolve()

    def snapshot(self):
        generated = time.time()
        if not self.database.exists():
            return {
                "generated_at": generated, "database": str(self.database),
                "state": "disconnected", "reason": "evidence database not found",
                "status": [], "topology": {"links": [], "hosts": []},
                "metrics": [], "flows": [], "decisions": [], "evaluations": [],
                "patient_sources": [], "patient_readings": [], "rule_events": [],
                "post_action_validations": [], "experiments": [],
            }
        uri = "file:{}?mode=ro".format(self.database.as_posix())
        connection = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            connection.row_factory = sqlite3.Row
            status = self._rows(connection, "SELECT * FROM system_status ORDER BY component")
            links = self._rows(connection, "SELECT * FROM topology_links WHERE active=1 ORDER BY 1,2")
            hosts = self._rows(connection, "SELECT * FROM host_attachments WHERE active=1 ORDER BY ip_address")
            metrics = self._rows(connection, """
                SELECT n.* FROM network_metrics n JOIN (
                    SELECT source_switch, destination_switch, MAX(id) AS id
                    FROM network_metrics GROUP BY source_switch, destination_switch
                ) latest ON n.id=latest.id ORDER BY n.source_switch, n.destination_switch
            """)
            flows = self._rows(connection, "SELECT * FROM flows ORDER BY active DESC, last_seen DESC LIMIT 100")
            decisions = self._rows(connection, """
                SELECT d.* FROM routing_decisions d JOIN (
                    SELECT flow_id, MAX(id) AS id FROM routing_decisions GROUP BY flow_id
                ) latest ON d.id=latest.id ORDER BY d.timestamp DESC LIMIT 100
            """)
            evaluations = self._rows(connection, "SELECT * FROM path_evaluations ORDER BY id DESC LIMIT 100")
            sources = self._rows(connection, "SELECT * FROM patient_sources ORDER BY last_seen DESC")
            readings = self._rows(connection, """
                SELECT r.* FROM patient_readings r JOIN (
                    SELECT source_id, metric, MAX(id) AS id FROM patient_readings
                    GROUP BY source_id, metric
                ) latest ON r.id=latest.id ORDER BY r.source_id, r.metric
            """)
            rule_events = self._rows(connection, "SELECT * FROM flow_rule_events ORDER BY id DESC LIMIT 100")
            validations = self._rows(connection, "SELECT * FROM post_action_validations ORDER BY id DESC LIMIT 50")
            experiments = self._rows(connection, "SELECT * FROM experiments ORDER BY id DESC LIMIT 100")
        finally:
            connection.close()
        for item in status:
            item["age_seconds"] = max(0.0, generated - item["timestamp"])
            item["details"] = self._decode(item.pop("details_json"), {})
            item["fresh"] = item["age_seconds"] <= 10.0
        for item in metrics:
            item["age_seconds"] = max(0.0, generated - item["timestamp"])
            item["fresh"] = item["age_seconds"] <= 10.0
            item["labels"] = {
                "latency_ms": "measured", "jitter_ms": "derived",
                "packet_loss_pct": "derived directional estimate",
                "utilization_pct": "derived", "throughput_bps": "measured carried rate",
                "available_bandwidth_bps": "derived residual estimate",
                "capacity_bps": item.get("capacity_source") or "unknown",
            }
        for item in flows:
            item["fresh"] = bool(item.get("active")) and generated - item["last_seen"] <= 45.0
        for item in decisions:
            item["selected_path"] = self._decode(item.pop("selected_path_json"), [])
            item["previous_path"] = self._decode(item.pop("previous_path_json"), None)
        for item in evaluations:
            item["path"] = self._decode(item.pop("path_json"), [])
            item["metrics"] = self._decode(item.pop("metrics_json"), {})
            item["normalized"] = self._decode(item.pop("normalized_json"), {})
            item["weights"] = self._decode(item.pop("weights_json"), {})
            item["cost_data_kind"] = "derived from measured inputs"
            item["safety_data_kind"] = "projected counterfactual"
        for item in sources:
            item["age_seconds"] = max(0.0, generated - item["last_seen"])
            item["fresh"] = item["status"] == "connected" and item["age_seconds"] <= item["freshness_seconds"]
            item["metadata"] = self._decode(item.pop("metadata_json"), {})
            if not item["fresh"]:
                item["display_status"] = "stale" if item["status"] == "connected" else "disconnected"
            else:
                item["display_status"] = "connected"
        for item in readings:
            item["fresh"] = generated <= item["expires_at"]
            item["data_kind"] = item["operating_mode"]
        for item in rule_events:
            item["match"] = self._decode(item.pop("match_json"), {})
            item["path"] = self._decode(item.pop("path_json"), [])
        for item in validations:
            item["projected"] = self._decode(item.pop("projected_json"), {})
            item["measured"] = self._decode(item.pop("measured_json"), {})
        state = "connected" if any(
            item["component"] == "controller" and item["fresh"] for item in status
        ) else "stale-or-disconnected"
        return {
            "generated_at": generated, "database": str(self.database), "state": state,
            "status": status, "topology": {"links": links, "hosts": hosts},
            "metrics": metrics, "flows": flows, "decisions": decisions,
            "evaluations": evaluations, "patient_sources": sources,
            "patient_readings": readings, "rule_events": rule_events,
            "post_action_validations": validations, "experiments": experiments,
        }

    @staticmethod
    def _rows(connection, sql):
        try:
            return [dict(row) for row in connection.execute(sql)]
        except sqlite3.OperationalError as error:
            if "no such table" in str(error):
                return []
            raise

    @staticmethod
    def _decode(value, default):
        if value is None:
            return default
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default


def handler_factory(repository: DashboardRepository, static_dir: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/state":
                self._send_json(200, repository.snapshot())
                return
            if path == "/health":
                self._send_json(200, {"status": "ready", "read_only": True})
                return
            relative = "index.html" if path == "/" else path.lstrip("/")
            target = (static_dir / relative).resolve()
            if static_dir not in target.parents and target != static_dir:
                self._send_json(403, {"error": "forbidden"})
                return
            if not target.is_file():
                self._send_json(404, {"error": "not found"})
                return
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            return

        def _send_json(self, status, body):
            data = json.dumps(_json_safe(body), allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def _json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="results/medroute.db")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    repository = DashboardRepository(args.database)
    static_dir = Path(__file__).resolve().parent / "dashboard"
    server = ThreadingHTTPServer((args.host, args.port), handler_factory(repository, static_dir))
    print("MedRoute read-only dashboard: http://{}:{}".format(args.host, args.port))
    server.serve_forever()


if __name__ == "__main__":
    main()
