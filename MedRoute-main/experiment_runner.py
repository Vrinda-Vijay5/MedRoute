"""Run real Mininet measurements and persist raw/structured evidence.

This script never supplies fallback numbers. Missing tools or malformed output
fail the experiment so an incomplete run cannot be mistaken for evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Dict, Mapping, Sequence

from storage import ResultsStore
from traffic_classifier import HealthcareTrafficClassifier
from healthcare_data import SyntheticHealthcareDataSource


PING_LOSS = re.compile(r"([0-9.]+)% packet loss")
PING_RTT = re.compile(r"=\s*([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+)\s*ms")
UDP_RESULT_FIELDS = (
    "bits_per_second", "jitter_ms", "lost_packets", "packets", "lost_percent"
)


def parse_ping(output: str):
    loss = PING_LOSS.search(output)
    rtt = PING_RTT.search(output)
    if not loss or not rtt:
        raise RuntimeError("Could not parse ping output: {}".format(output))
    return {"packet_loss_pct": float(loss.group(1)), "latency_ms": float(rtt.group(2)),
            "jitter_ms": float(rtt.group(4))}


def parse_iperf_udp_report(report: Mapping[str, object]) -> Dict[str, object]:
    """Extract the receiver UDP summary across supported iperf3 schemas."""
    if not isinstance(report, Mapping):
        raise ValueError("iperf3 report root is not an object")
    end = report.get("end")
    if not isinstance(end, Mapping):
        raise ValueError("iperf3 report has no end object")

    candidates = []
    candidates.append(("end.sum_received", end.get("sum_received")))
    candidates.append(("end.sum", end.get("sum")))
    streams = end.get("streams")
    if isinstance(streams, Sequence) and not isinstance(streams, (str, bytes)):
        for index, stream in enumerate(streams):
            if isinstance(stream, Mapping):
                candidates.append(("end.streams[{}].udp".format(index), stream.get("udp")))

    invalid_sources = []
    for source, candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        if not all(field in candidate for field in UDP_RESULT_FIELDS):
            continue
        try:
            return {
                "throughput_bps": float(candidate["bits_per_second"]),
                "jitter_ms": float(candidate["jitter_ms"]),
                "packet_loss_pct": float(candidate["lost_percent"]),
                "lost_packets": int(candidate["lost_packets"]),
                "packets": int(candidate["packets"]),
                "summary_source": source,
            }
        except (TypeError, ValueError) as error:
            invalid_sources.append("{} ({})".format(source, error))
            continue

    raise ValueError(
        "iperf3 report has no complete UDP summary; checked end.sum_received, "
        "end.sum, and end.streams[*].udp{}".format(
            "; invalid: {}".format(", ".join(invalid_sources)) if invalid_sources else ""
        )
    )


def run_iperf(client, server, destination: str, port: int, rate: str, duration: int):
    server.cmd("pkill -f 'iperf3 -s -1 -p {}' >/dev/null 2>&1 || true".format(port))
    server.cmd("iperf3 -s -1 -p {} >/tmp/medroute-iperf-{}.log 2>&1 &".format(port, port))
    ready = server.cmd(
        "for attempt in $(seq 1 50); do ss -lunH 'sport = :{}' | grep -q . "
        "&& {{ echo READY; break; }}; sleep 0.1; done".format(port)
    )
    if "READY" not in ready:
        raise RuntimeError("iperf3 UDP server did not become ready on port {}".format(port))
    raw = client.cmd("iperf3 -c {} -u -b {} -t {} -p {} -J".format(destination, rate, duration, port))
    try:
        report = json.loads(raw)
        parsed = parse_iperf_udp_report(report)
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise RuntimeError("iperf3 did not return a valid UDP report: {}".format(raw)) from error
    return report, parsed


def _wait_for_controller(database: Path, since: float,
                         timeout_seconds: float = 40.0) -> None:
    deadline = time.time() + timeout_seconds
    last = "database not created"
    while time.time() < deadline:
        if database.exists():
            try:
                with sqlite3.connect(str(database), timeout=2.0) as connection:
                    row = connection.execute(
                        "SELECT status, details_json, timestamp FROM system_status "
                        "WHERE component='controller'"
                    ).fetchone()
                if row:
                    details = json.loads(row[1])
                    last = "status={} telemetry={}/{}".format(
                        row[0], details.get("telemetry_complete"),
                        details.get("telemetry_total"),
                    )
                    if (row[0] == "ready" and row[2] >= since
                            and details.get("telemetry_total", 0) > 0
                            and details.get("telemetry_complete") == details.get("telemetry_total")):
                        return
            except (sqlite3.Error, json.JSONDecodeError) as error:
                last = str(error)
        time.sleep(0.25)
    raise RuntimeError("controller telemetry not ready within {:.0f}s: {}".format(
        timeout_seconds, last
    ))


def _flow_evidence(database: Path, source: str, destination: str,
                   traffic_type: str, since: float):
    with sqlite3.connect(str(database), timeout=5.0) as connection:
        connection.row_factory = sqlite3.Row
        flow = connection.execute(
            """SELECT f.* FROM flows f
               JOIN routing_decisions d ON d.flow_id=f.flow_id
               WHERE f.source=? AND f.destination=? AND f.traffic_type=?
                 AND d.timestamp>=?
               ORDER BY d.id DESC LIMIT 1""",
            (source, destination, traffic_type, since),
        ).fetchone()
        if flow is None:
            raise RuntimeError("no controller flow evidence for {} {} -> {}".format(
                traffic_type, source, destination
            ))
        decisions = [dict(row) for row in connection.execute(
            "SELECT * FROM routing_decisions WHERE flow_id=? AND timestamp>=? ORDER BY id",
            (flow["flow_id"], since),
        )]
        evaluations = [dict(row) for row in connection.execute(
            "SELECT * FROM path_evaluations WHERE flow_id=? AND timestamp>=? ORDER BY id",
            (flow["flow_id"], since),
        )]
    for decision in decisions:
        decision["selected_path"] = json.loads(decision.pop("selected_path_json"))
        previous = decision.pop("previous_path_json")
        decision["previous_path"] = json.loads(previous) if previous else None
    if not decisions:
        raise RuntimeError("controller flow has no recorded route decision")
    return dict(flow), decisions, evaluations


def _source_revision(root: Path) -> str:
    digest = hashlib.sha256()
    paths = (
        list(root.glob("*.py"))
        + list((root / "scripts").glob("*.sh"))
        + list((root / "scripts").glob("*.py"))
        + list((root / "config").glob("*.json"))
        + list((root / "data").glob("*.csv"))
        + list((root / "dashboard").glob("*"))
        + [root / "requirements.txt"]
    )
    for path in sorted(item for item in paths if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return "sha256:{}".format(digest.hexdigest())


def _command_version(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        output = (result.stdout or result.stderr).strip().splitlines()
        return output[0] if output else "exit {}".format(result.returncode)
    except (OSError, subprocess.SubprocessError) as error:
        return "unavailable: {}".format(error)


def _capture_switch_flows(net):
    return {switch.name: switch.cmd(
        "ovs-ofctl -O OpenFlow13 dump-flows {} 2>&1".format(switch.name)
    ) for switch in net.switches}


def run_experiment(scenario: str, routing_mode: str, traffic_type: str, duration: int,
                   database_path: str | Path = "results/medroute.db",
                   run_id: str = "manual", trial_index: int = 1) -> Path:
    # Keep Mininet optional when importing the JSON parser for unit tests.
    from hospital_topology import apply_jitter, create_network

    profile = HealthcareTrafficClassifier().classify(traffic_type)
    port = int(profile["udp_port"])
    offered_rate_bps = int(SyntheticHealthcareDataSource.DEFAULT_RATES[traffic_type])
    database = Path(database_path)
    net = create_network(scenario)
    root = Path(__file__).resolve().parent
    started = time.time()
    revision = _source_revision(root)
    versions = {
        "python": platform.python_version(),
        "iperf3": _command_version(["iperf3", "--version"]),
        "ovs": _command_version(["ovs-vsctl", "--version"]),
        "mininet": _command_version(["mn", "--version"]),
        "ryu": _command_version(["ryu-manager", "--version"]),
    }
    raw_record = {"run_id": run_id, "trial_index": trial_index,
                  "scenario": scenario, "routing_mode": routing_mode,
                  "traffic_type": traffic_type, "offered_rate_bps": offered_rate_bps,
                  "duration_seconds": duration, "started_at": started,
                  "source_revision": revision, "versions": versions}
    try:
        net.start()
        if scenario == "jitter":
            apply_jitter(net)
        reachability_loss_pct = float(net.pingAll())
        if reachability_loss_pct != 0.0:
            raise RuntimeError("pingAll readiness failed with {}% loss".format(reachability_loss_pct))
        _wait_for_controller(database, started)
        if scenario == "congestion":
            net["bg2"].cmd("iperf3 -s -1 -p 6001 >/tmp/medroute-background.log 2>&1 &")
            net["bg1"].cmd("iperf3 -c 10.0.0.4 -u -b 85M -t {} -p 6001 >/tmp/medroute-bg-client.log 2>&1 &".format(duration + 3))
            time.sleep(2)
        ping_started = time.time()
        ping_output = net["h1"].cmd("ping -c 10 -i 0.2 10.0.0.2")
        ping = parse_ping(ping_output)
        ping_flow, ping_decisions, _ = _flow_evidence(
            database, "10.0.0.1", "10.0.0.2", "UNKNOWN", ping_started
        )
        iperf_report, iperf = run_iperf(
            net["h1"], net["h2"], "10.0.0.2", port,
            str(offered_rate_bps), duration,
        )
        time.sleep(1)
        flow, decisions, evaluations = _flow_evidence(
            database, "10.0.0.1", "10.0.0.2", traffic_type, started
        )
        selected_path = decisions[-1]["selected_path"]
        ping_path = ping_decisions[-1]["selected_path"]
        latency_correlated = ping_path == selected_path
        raw_record.update({"ping_output": ping_output, "ping": ping, "iperf": iperf_report,
                           "latency_probe_flow": ping_flow,
                           "latency_probe_path": ping_path,
                           "latency_probe_matches_healthcare_path": latency_correlated,
                           "healthcare_flow": flow, "routing_decisions": decisions,
                           "path_evaluations": evaluations,
                           "selected_path": selected_path,
                           "switch_flow_tables": _capture_switch_flows(net),
                           "finished_at": time.time()})
        results = Path("results")
        raw_dir = results / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / "{}-{}-t{}-{}-{}-{}.json".format(
            int(raw_record["started_at"]), run_id, trial_index,
            scenario, routing_mode, traffic_type.lower()
        )
        raw_path.write_text(json.dumps(raw_record, indent=2), encoding="utf-8")
        store = ResultsStore(database)
        store.record_experiment({
            "scenario": scenario, "routing_mode": routing_mode, "traffic_type": traffic_type,
            "latency_ms": ping["latency_ms"] if latency_correlated else None,
            "jitter_ms": iperf["jitter_ms"],
            "packet_loss_pct": iperf["packet_loss_pct"],
            "throughput_bps": iperf["throughput_bps"],
            "selected_path_json": json.dumps(selected_path),
            "notes": "latency controller-path match={}; raw evidence: {}".format(
                latency_correlated, raw_path
            ),
            "run_id": run_id, "raw_path": str(raw_path), "revision": revision,
            "versions_json": json.dumps(versions, sort_keys=True),
        })
        store.close()
        return raw_path
    finally:
        net.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("normal", "congestion", "loss", "delay", "jitter"),
                        required=True)
    parser.add_argument("--routing-mode", choices=("baseline", "qos", "medroute"), required=True)
    parser.add_argument("--traffic-type", choices=tuple(HealthcareTrafficClassifier.TRAFFIC_TYPES),
                        default="ECG")
    parser.add_argument("--duration", type=int, default=10)
    parser.add_argument("--database", default="results/medroute.db")
    parser.add_argument("--run-id", default="manual")
    parser.add_argument("--trial-index", type=int, default=1)
    args = parser.parse_args()
    output = run_experiment(
        args.scenario, args.routing_mode, args.traffic_type, args.duration,
        args.database, args.run_id, args.trial_index,
    )
    print("Actual experiment evidence written to {}".format(output))
