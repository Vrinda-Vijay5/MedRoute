"""Small SQLite evidence store for flows, telemetry, evaluations, and decisions."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Mapping, Optional, Sequence

from braess_validator import BraessResult
from qos_metrics import LinkMetrics, PathEvaluation


class ResultsStore:
    def __init__(self, path: str | Path = "results/medroute.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(str(self.path), check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS flows (
            flow_id TEXT PRIMARY KEY, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
            traffic_type TEXT NOT NULL, criticality INTEGER NOT NULL,
            source TEXT NOT NULL, destination TEXT NOT NULL, expected_rate_bps REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS network_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
            source_switch INTEGER NOT NULL, destination_switch INTEGER NOT NULL,
            latency_ms REAL, jitter_ms REAL, packet_loss_pct REAL, utilization_pct REAL,
            throughput_bps REAL, available_bandwidth_bps REAL, sample_source TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS path_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL, flow_id TEXT NOT NULL,
            path_json TEXT NOT NULL, metrics_json TEXT NOT NULL, normalized_json TEXT NOT NULL,
            weights_json TEXT NOT NULL, qos_cost REAL NOT NULL, braess_status TEXT,
            braess_reason TEXT, safe INTEGER
        );
        CREATE TABLE IF NOT EXISTS routing_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL, flow_id TEXT NOT NULL,
            selected_path_json TEXT NOT NULL, previous_path_json TEXT, mode TEXT NOT NULL,
            reason TEXT NOT NULL, changed INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
            scenario TEXT NOT NULL, routing_mode TEXT NOT NULL, traffic_type TEXT NOT NULL,
            latency_ms REAL, jitter_ms REAL, packet_loss_pct REAL, throughput_bps REAL,
            path_cost REAL, selected_path_json TEXT, notes TEXT
        );
        """
        with self._lock, self._connection:
            self._connection.executescript(schema)

    def upsert_flow(self, flow_id: str, traffic_type: str, criticality: int, source: str,
                    destination: str, expected_rate_bps: float, timestamp: Optional[float] = None) -> None:
        observed = timestamp or time.time()
        sql = """INSERT INTO flows VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT(flow_id) DO UPDATE SET last_seen=excluded.last_seen"""
        self._execute(sql, (flow_id, observed, observed, traffic_type, criticality,
                            source, destination, expected_rate_bps))

    def record_link_metric(self, edge, metric: LinkMetrics) -> None:
        self._execute(
            """INSERT INTO network_metrics
               (timestamp, source_switch, destination_switch, latency_ms, jitter_ms,
                packet_loss_pct, utilization_pct, throughput_bps, available_bandwidth_bps, sample_source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (metric.timestamp or time.time(), edge[0], edge[1], metric.latency_ms, metric.jitter_ms,
             metric.packet_loss_pct, metric.utilization_pct, metric.throughput_bps,
             metric.available_bandwidth_bps, metric.sample_source),
        )

    def record_evaluation(self, flow_id: str, evaluation: PathEvaluation,
                          braess: Optional[BraessResult] = None) -> None:
        data = evaluation.to_dict()
        self._execute(
            """INSERT INTO path_evaluations
               (timestamp, flow_id, path_json, metrics_json, normalized_json, weights_json,
                qos_cost, braess_status, braess_reason, safe) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (time.time(), flow_id, json.dumps(data["path"]), json.dumps(data["metrics"]),
             json.dumps(data["normalized"]), json.dumps(data["weights"]), evaluation.cost,
             braess.status if braess else None, braess.reason if braess else None,
             int(braess.safe) if braess else None),
        )

    def record_decision(self, flow_id: str, selected_path: Sequence[int], previous_path,
                        mode: str, reason: str, changed: bool) -> None:
        self._execute(
            """INSERT INTO routing_decisions
               (timestamp, flow_id, selected_path_json, previous_path_json, mode, reason, changed)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (time.time(), flow_id, json.dumps(list(selected_path)),
             json.dumps(list(previous_path)) if previous_path else None, mode, reason, int(changed)),
        )

    def record_experiment(self, values: Mapping[str, object]) -> None:
        columns = ("timestamp", "scenario", "routing_mode", "traffic_type", "latency_ms",
                   "jitter_ms", "packet_loss_pct", "throughput_bps", "path_cost",
                   "selected_path_json", "notes")
        row = [values.get(name) for name in columns]
        row[0] = row[0] or time.time()
        self._execute("INSERT INTO experiments ({}) VALUES ({})".format(
            ",".join(columns), ",".join("?" for _ in columns)), row)

    def _execute(self, sql, parameters) -> None:
        with self._lock, self._connection:
            self._connection.execute(sql, parameters)

    def close(self) -> None:
        with self._lock:
            self._connection.close()
