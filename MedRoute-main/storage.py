"""Small SQLite evidence store for flows, telemetry, evaluations, and decisions."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

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
        CREATE TABLE IF NOT EXISTS system_status (
            component TEXT PRIMARY KEY, timestamp REAL NOT NULL, status TEXT NOT NULL,
            operating_mode TEXT NOT NULL, data_kind TEXT NOT NULL, details_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS topology_links (
            source_switch INTEGER NOT NULL, destination_switch INTEGER NOT NULL,
            source_port INTEGER NOT NULL, destination_port INTEGER NOT NULL,
            active INTEGER NOT NULL, updated_at REAL NOT NULL,
            PRIMARY KEY (source_switch, destination_switch)
        );
        CREATE TABLE IF NOT EXISTS host_attachments (
            ip_address TEXT PRIMARY KEY, mac_address TEXT NOT NULL, switch_id INTEGER NOT NULL,
            port_no INTEGER NOT NULL, active INTEGER NOT NULL, updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS flow_rule_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
            flow_id TEXT NOT NULL, action TEXT NOT NULL, switch_id INTEGER NOT NULL,
            cookie INTEGER NOT NULL, route_version INTEGER NOT NULL,
            direction TEXT NOT NULL, match_json TEXT NOT NULL, output_port INTEGER,
            path_json TEXT NOT NULL, reason TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS patient_sources (
            source_id TEXT PRIMARY KEY, source_type TEXT NOT NULL, interface TEXT NOT NULL,
            operating_mode TEXT NOT NULL, status TEXT NOT NULL, last_seen REAL NOT NULL,
            freshness_seconds REAL NOT NULL, metadata_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS patient_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
            received_at REAL NOT NULL, source_id TEXT NOT NULL, metric TEXT NOT NULL,
            value REAL NOT NULL, unit TEXT NOT NULL, operating_mode TEXT NOT NULL,
            validation_status TEXT NOT NULL, expires_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS flow_bindings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL,
            source_ip TEXT NOT NULL, destination_ip TEXT NOT NULL,
            ip_protocol INTEGER NOT NULL, source_port INTEGER, destination_port INTEGER,
            policy_name TEXT NOT NULL, active INTEGER NOT NULL, updated_at REAL NOT NULL,
            UNIQUE(source_id, source_ip, destination_ip, ip_protocol, source_port, destination_port)
        );
        CREATE TABLE IF NOT EXISTS criticality_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
            source_id TEXT NOT NULL, criticality INTEGER NOT NULL, reason TEXT NOT NULL,
            policy_name TEXT NOT NULL, operating_mode TEXT NOT NULL,
            expires_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS post_action_validations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
            flow_id TEXT NOT NULL, decision_id INTEGER, projected_json TEXT NOT NULL,
            measured_json TEXT NOT NULL, verdict TEXT NOT NULL
        );
        """
        with self._lock, self._connection:
            self._connection.executescript(schema)
            self._ensure_column("network_metrics", "capacity_bps", "REAL")
            self._ensure_column("network_metrics", "capacity_source", "TEXT")
            self._ensure_column("flows", "ip_protocol", "INTEGER")
            self._ensure_column("flows", "source_port", "INTEGER")
            self._ensure_column("flows", "destination_port", "INTEGER")
            self._ensure_column("flows", "source_id", "TEXT")
            self._ensure_column("flows", "observed_rate_bps", "REAL")
            self._ensure_column("flows", "observed_rate_at", "REAL")
            self._ensure_column("flows", "active", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column("experiments", "run_id", "TEXT")
            self._ensure_column("experiments", "raw_path", "TEXT")
            self._ensure_column("experiments", "revision", "TEXT")
            self._ensure_column("experiments", "versions_json", "TEXT")

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        columns = {
            row[1] for row in self._connection.execute(
                "PRAGMA table_info({})".format(table)
            )
        }
        if column not in columns:
            self._connection.execute(
                "ALTER TABLE {} ADD COLUMN {} {}".format(table, column, declaration)
            )

    def upsert_flow(self, flow_id: str, traffic_type: str, criticality: int, source: str,
                    destination: str, expected_rate_bps: float, timestamp: Optional[float] = None,
                    ip_protocol: Optional[int] = None, source_port: Optional[int] = None,
                    destination_port: Optional[int] = None,
                    source_id: Optional[str] = None) -> None:
        observed = timestamp or time.time()
        sql = """INSERT INTO flows
                 (flow_id, first_seen, last_seen, traffic_type, criticality, source,
                  destination, expected_rate_bps, ip_protocol, source_port,
                  destination_port, source_id, active)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                 ON CONFLICT(flow_id) DO UPDATE SET
                   last_seen=excluded.last_seen, traffic_type=excluded.traffic_type,
                   criticality=excluded.criticality, expected_rate_bps=excluded.expected_rate_bps,
                   source_id=excluded.source_id, active=1"""
        self._execute(sql, (flow_id, observed, observed, traffic_type, criticality,
                            source, destination, expected_rate_bps, ip_protocol,
                            source_port, destination_port, source_id))

    def touch_flow(self, flow_id: str, observed_rate_bps: float,
                   timestamp: Optional[float] = None) -> None:
        observed = timestamp or time.time()
        self._execute(
            """UPDATE flows SET last_seen=?, observed_rate_bps=?,
               observed_rate_at=?, active=1 WHERE flow_id=?""",
            (observed, observed_rate_bps, observed, flow_id),
        )

    def mark_flow_inactive(self, flow_id: str,
                           timestamp: Optional[float] = None) -> None:
        self._execute(
            "UPDATE flows SET active=0, last_seen=? WHERE flow_id=?",
            (timestamp or time.time(), flow_id),
        )

    def record_link_metric(self, edge, metric: LinkMetrics) -> None:
        self._execute(
            """INSERT INTO network_metrics
               (timestamp, source_switch, destination_switch, latency_ms, jitter_ms,
                packet_loss_pct, utilization_pct, throughput_bps, available_bandwidth_bps,
                sample_source, capacity_bps, capacity_source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (metric.timestamp or time.time(), edge[0], edge[1], metric.latency_ms, metric.jitter_ms,
             metric.packet_loss_pct, metric.utilization_pct, metric.throughput_bps,
             metric.available_bandwidth_bps, metric.sample_source, metric.capacity_bps,
             metric.capacity_source),
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
                   "selected_path_json", "notes", "run_id", "raw_path", "revision",
                   "versions_json")
        row = [values.get(name) for name in columns]
        row[0] = row[0] or time.time()
        self._execute("INSERT INTO experiments ({}) VALUES ({})".format(
            ",".join(columns), ",".join("?" for _ in columns)), row)

    def record_status(self, component: str, status: str, operating_mode: str,
                      data_kind: str, details: Mapping[str, object],
                      timestamp: Optional[float] = None) -> None:
        self._execute(
            """INSERT INTO system_status VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(component) DO UPDATE SET timestamp=excluded.timestamp,
               status=excluded.status, operating_mode=excluded.operating_mode,
               data_kind=excluded.data_kind, details_json=excluded.details_json""",
            (component, timestamp or time.time(), status, operating_mode, data_kind,
             json.dumps(dict(details), sort_keys=True)),
        )

    def replace_topology(self, links: Sequence[Sequence[int]],
                         timestamp: Optional[float] = None) -> None:
        observed = timestamp or time.time()
        rows = [tuple(map(int, link)) for link in links]
        with self._lock, self._connection:
            self._connection.execute("UPDATE topology_links SET active=0, updated_at=?", (observed,))
            self._connection.executemany(
                """INSERT INTO topology_links
                   (source_switch, destination_switch, source_port, destination_port, active, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?)
                   ON CONFLICT(source_switch, destination_switch) DO UPDATE SET
                   source_port=excluded.source_port, destination_port=excluded.destination_port,
                   active=1, updated_at=excluded.updated_at""",
                [row + (observed,) for row in rows],
            )

    def upsert_host(self, ip_address: str, mac_address: str, switch_id: int,
                    port_no: int, timestamp: Optional[float] = None) -> None:
        self._execute(
            """INSERT INTO host_attachments VALUES (?, ?, ?, ?, 1, ?)
               ON CONFLICT(ip_address) DO UPDATE SET mac_address=excluded.mac_address,
               switch_id=excluded.switch_id, port_no=excluded.port_no,
               active=1, updated_at=excluded.updated_at""",
            (ip_address, mac_address, int(switch_id), int(port_no), timestamp or time.time()),
        )

    def record_rule_event(self, flow_id: str, action: str, switch_id: int,
                          cookie: int, route_version: int, direction: str,
                          match: Mapping[str, object], output_port: Optional[int],
                          path: Sequence[int], reason: str) -> None:
        # SQLite INTEGER is signed 64-bit; store the namespace cookie's same
        # bit pattern in signed form when bit 63 is set.
        signed_cookie = cookie if cookie < (1 << 63) else cookie - (1 << 64)
        self._execute(
            """INSERT INTO flow_rule_events
               (timestamp, flow_id, action, switch_id, cookie, route_version,
                direction, match_json, output_port, path_json, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (time.time(), flow_id, action, int(switch_id), signed_cookie,
             int(route_version), direction, json.dumps(dict(match), sort_keys=True),
             output_port, json.dumps(list(path)), reason),
        )

    def resolve_live_criticality(self, source_ip: str, destination_ip: str,
                                 ip_protocol: int, source_port: Optional[int],
                                 destination_port: Optional[int],
                                 now: Optional[float] = None) -> Optional[Mapping[str, Any]]:
        clock = now or time.time()
        sql = """
            SELECT s.source_id, s.criticality, s.reason, s.policy_name,
                   s.operating_mode, s.timestamp, s.expires_at
            FROM flow_bindings b
            JOIN criticality_signals s ON s.source_id=b.source_id
            WHERE b.active=1 AND b.source_ip=? AND b.destination_ip=?
              AND b.ip_protocol=?
              AND (b.source_port IS NULL OR b.source_port=?)
              AND (b.destination_port IS NULL OR b.destination_port=?)
              AND s.expires_at>=?
            ORDER BY s.timestamp DESC LIMIT 1
        """
        with self._lock:
            row = self._connection.execute(
                sql, (source_ip, destination_ip, int(ip_protocol), source_port,
                      destination_port, clock),
            ).fetchone()
        if row is None:
            return None
        keys = ("source_id", "criticality", "reason", "policy_name",
                "operating_mode", "timestamp", "expires_at")
        return dict(zip(keys, row))

    def record_patient_update(
        self, source_id: str, source_type: str, interface: str,
        operating_mode: str, freshness_seconds: float,
        readings: Sequence[Mapping[str, object]],
        flow_binding: Mapping[str, object], criticality: int, reason: str,
        policy_name: str, timestamp: float, received_at: Optional[float] = None,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        received = received_at or time.time()
        expires = timestamp + freshness_seconds
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO patient_sources VALUES (?, ?, ?, ?, 'connected', ?, ?, ?)
                   ON CONFLICT(source_id) DO UPDATE SET source_type=excluded.source_type,
                   interface=excluded.interface, operating_mode=excluded.operating_mode,
                   status='connected', last_seen=excluded.last_seen,
                   freshness_seconds=excluded.freshness_seconds,
                   metadata_json=excluded.metadata_json""",
                (source_id, source_type, interface, operating_mode, timestamp,
                 freshness_seconds, json.dumps(dict(metadata or {}), sort_keys=True)),
            )
            self._connection.executemany(
                """INSERT INTO patient_readings
                   (timestamp, received_at, source_id, metric, value, unit,
                    operating_mode, validation_status, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'valid', ?)""",
                [
                    (timestamp, received, source_id, item["metric"], item["value"],
                     item["unit"], operating_mode, expires)
                    for item in readings
                ],
            )
            self._connection.execute(
                "UPDATE flow_bindings SET active=0, updated_at=? WHERE source_id=?",
                (received, source_id),
            )
            self._connection.execute(
                """INSERT INTO flow_bindings
                   (source_id, source_ip, destination_ip, ip_protocol, source_port,
                    destination_port, policy_name, active, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (source_id, flow_binding["source_ip"], flow_binding["destination_ip"],
                 flow_binding["ip_protocol"], flow_binding.get("source_port"),
                 flow_binding.get("destination_port"), policy_name, received),
            )
            self._connection.execute(
                """INSERT INTO criticality_signals
                   (timestamp, source_id, criticality, reason, policy_name,
                    operating_mode, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (timestamp, source_id, criticality, reason, policy_name,
                 operating_mode, expires),
            )

    def mark_patient_source_disconnected(self, source_id: str,
                                         timestamp: Optional[float] = None) -> None:
        observed = timestamp or time.time()
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE patient_sources SET status='disconnected', last_seen=? WHERE source_id=?",
                (observed, source_id),
            )
            self._connection.execute(
                "UPDATE flow_bindings SET active=0, updated_at=? WHERE source_id=?",
                (observed, source_id),
            )

    def record_post_action_validation(
        self, flow_id: str, projected: Mapping[str, object],
        measured: Mapping[str, object], verdict: str,
        decision_id: Optional[int] = None,
    ) -> None:
        self._execute(
            """INSERT INTO post_action_validations
               (timestamp, flow_id, decision_id, projected_json, measured_json, verdict)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (time.time(), flow_id, decision_id,
             json.dumps(dict(projected), sort_keys=True),
             json.dumps(dict(measured), sort_keys=True), verdict),
        )

    def _execute(self, sql, parameters) -> None:
        with self._lock, self._connection:
            self._connection.execute(sql, parameters)

    def close(self) -> None:
        with self._lock:
            self._connection.close()
