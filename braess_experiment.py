"""Collect one real loaded Braess-topology trial.

Run this script through scripts/run_braess_experiment.sh so the external Ryu
mode and the recorded label agree. It reports observations; it never forces a
BRAESS_RISK outcome.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from pathlib import Path

from experiment_runner import parse_iperf_udp_report, parse_ping
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


TELEMETRY_READY = re.compile(
    r"TELEMETRY STATUS READY complete=(\d+)/(\d+)"
)


def _last_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _background_iperf_command(destination_ip: str, duration: int, source: str) -> str:
    return (
        "iperf3 -c {destination_ip} -u -b {rate} -t {duration} -p {port} "
        ">/tmp/medroute-braess-{source}-client.log 2>&1 & echo $!"
    ).format(
        destination_ip=destination_ip,
        rate=iperf_rate(IMAGING_RATE_BPS),
        duration=duration,
        port=IMAGING_UDP_PORT,
        source=source,
    )


def _start_iperf_server(host, port: int, log_path: str) -> str:
    host.cmd("rm -f {}".format(log_path))
    pid = _last_line(host.cmd(
        "iperf3 -s -1 -p {} >{} 2>&1 & echo $!".format(port, log_path)
    ))
    if not pid.isdigit():
        raise RuntimeError("could not start iperf3 server on port {}: {}".format(port, pid))
    ready = host.cmd(
        "for attempt in $(seq 1 50); do "
        "ss -ltnH 'sport = :{}' | grep -q . && {{ echo READY; break; }}; "
        "sleep 0.1; done".format(port)
    )
    if "READY" not in ready:
        server_error = host.cmd("cat {} 2>/dev/null || true".format(log_path)).strip()
        _stop_child(host, pid)
        raise RuntimeError(
            "iperf3 server on port {} did not become ready: {}".format(
                port, server_error or "no server log output"
            )
        )
    return pid


def _read_completed_udp_report(client, output_path: str, error_path: str, status_path: str):
    status = client.cmd("cat {} 2>/dev/null || true".format(status_path)).strip()
    raw = client.cmd("cat {} 2>/dev/null || true".format(output_path))
    stderr = client.cmd("cat {} 2>/dev/null || true".format(error_path)).strip()
    if status != "0":
        raise RuntimeError(
            "protected ECG iperf3 failed with exit status {}: {}".format(
                status or "missing", stderr or "no stderr output"
            )
        )
    if not raw.strip():
        raise RuntimeError(
            "protected ECG iperf3 returned empty JSON output: {}".format(
                stderr or "no stderr output"
            )
        )
    try:
        report = json.loads(raw)
        parsed = parse_iperf_udp_report(report)
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise RuntimeError(
            "protected ECG iperf3 returned invalid UDP JSON; stderr={}; output={}".format(
                stderr or "none", raw
            )
        ) from error
    return report, parsed


def _stop_child(host, pid: str) -> None:
    if pid and pid.isdigit():
        host.cmd(
            "kill -TERM {0} >/dev/null 2>&1 || true; "
            "wait {0} >/dev/null 2>&1 || true".format(pid)
        )


def _wait_for_telemetry_ready(
    controller_log: str | Path,
    expected_links: int,
    after_offset: int = 0,
    timeout_seconds: float = 40.0,
) -> None:
    """Wait for the controller's measured readiness signal."""
    log_path = Path(controller_log)
    deadline = time.time() + timeout_seconds
    last_status = "controller log has no telemetry status yet"
    while time.time() < deadline:
        if log_path.exists():
            new_text = log_path.read_bytes()[after_offset:].decode(
                "utf-8", errors="replace"
            )
            matches = list(TELEMETRY_READY.finditer(new_text))
            if matches:
                complete, total = (int(value) for value in matches[-1].groups())
                last_status = "latest READY status is {}/{}".format(complete, total)
                if complete == expected_links and total == expected_links:
                    return
        time.sleep(0.25)
    raise RuntimeError(
        "controller telemetry did not reach READY {0}/{0} within {1:.0f}s: {2}".format(
            expected_links, timeout_seconds, last_status
        )
    )


def _latest_selected_path(
    database_path: str | Path,
    source: str,
    destination: str,
    traffic_type: str,
    since: float,
):
    """Read the controller's latest decision for the measured protected flow."""
    query = """
        SELECT decisions.selected_path_json
        FROM routing_decisions AS decisions
        JOIN flows ON flows.flow_id = decisions.flow_id
        WHERE decisions.timestamp >= ?
          AND flows.source = ?
          AND flows.destination = ?
          AND flows.traffic_type = ?
        ORDER BY decisions.id DESC
        LIMIT 1
    """
    connection = sqlite3.connect(str(database_path), timeout=5.0)
    try:
        row = connection.execute(
            query, (since, source, destination, traffic_type)
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError(
            "controller recorded no {} routing decision for {} -> {} in this trial".format(
                traffic_type, source, destination
            )
        )
    path = tuple(int(node) for node in json.loads(row[0]))
    if not path:
        raise RuntimeError("controller recorded an empty selected path")
    return path


def _output_port(net, node_name: str, next_name: str) -> int:
    node = net[node_name]
    neighbor = net[next_name]
    links = net.linksBetween(node, neighbor)
    if len(links) != 1:
        raise RuntimeError(
            "expected one link between {} and {}, found {}".format(
                node_name, next_name, len(links)
            )
        )
    link = links[0]
    interface = link.intf1 if link.intf1.node is node else link.intf2
    return int(node.ports[interface])


def _measurement_cookie(started_at: float) -> int:
    """Return a trial-unique cookie in MedRoute's experiment namespace."""
    return 0x4D52000000000000 | (
        int(started_at * 1_000_000) & 0x0000FFFFFFFFFFFF
    )


def _remove_icmp_measurement_path(
    net, switch_names, cookie: int, strict: bool = True,
) -> None:
    errors = []
    for switch_name in sorted(set(switch_names)):
        result = net[switch_name].cmd(
            "ovs-ofctl -O OpenFlow13 del-flows {switch} "
            "'cookie={cookie:#x}/0xffffffffffffffff' 2>&1".format(
                switch=switch_name, cookie=cookie,
            )
        ).strip()
        if result:
            errors.append("{}: {}".format(switch_name, result))
    if errors and strict:
        raise RuntimeError(
            "could not remove temporary ICMP measurement rules: {}".format(
                "; ".join(errors)
            )
        )


def _install_icmp_measurement_path(
    net, path, source_host: str, destination_host: str, cookie: int,
):
    """Pin only the latency probe to the controller-selected UDP path."""
    source_ip = net[source_host].IP()
    destination_ip = net[destination_host].IP()
    installed_switches = set()
    directions = (
        (tuple(path), destination_host, source_ip, destination_ip),
        (tuple(reversed(path)), source_host, destination_ip, source_ip),
    )
    try:
        for switch_path, egress_host, match_source, match_destination in directions:
            for index, dpid in enumerate(switch_path):
                switch_name = "s{}".format(dpid)
                next_name = (
                    "s{}".format(switch_path[index + 1])
                    if index + 1 < len(switch_path)
                    else egress_host
                )
                output_port = _output_port(net, switch_name, next_name)
                result = net[switch_name].cmd(
                    "ovs-ofctl -O OpenFlow13 add-flow {switch} "
                    "'cookie={cookie:#x},idle_timeout=5,hard_timeout=10,"
                    "priority=300,dl_type=0x0800,nw_proto=1,"
                    "nw_src={source},nw_dst={destination},"
                    "actions=output:{port}' 2>&1".format(
                        switch=switch_name, cookie=cookie, source=match_source,
                        destination=match_destination, port=output_port,
                    )
                ).strip()
                if result:
                    raise RuntimeError(
                        "could not install ICMP measurement rule on {}: {}".format(
                            switch_name, result
                        )
                    )
                installed_switches.add(switch_name)
    except Exception:
        _remove_icmp_measurement_path(
            net, installed_switches, cookie, strict=False
        )
        raise
    return tuple(sorted(installed_switches))


def _prepare_trial_topology(
    net, candidate_enabled: bool, controller_log: str | Path, set_candidate_link,
) -> None:
    """Learn hosts with the candidate down, then enable it for AFTER trials."""
    set_candidate_link(net, False)
    _wait_for_telemetry_ready(controller_log, expected_links=8)

    reachability_loss_pct = float(net.pingAll())
    if reachability_loss_pct != 0.0:
        raise RuntimeError(
            "trial reachability validation failed: pingAll loss={:.2f}%".format(
                reachability_loss_pct
            )
        )

    if candidate_enabled:
        log_offset = Path(controller_log).stat().st_size
        set_candidate_link(net, True)
        _wait_for_telemetry_ready(
            controller_log, expected_links=10, after_offset=log_offset
        )


def measure(
    label: str,
    candidate_enabled: bool,
    routing_mode: str,
    duration: int,
    controller_log: str | Path,
    database_path: str | Path = "results/medroute.db",
) -> Path:
    from braess_topology import create_braess_network, set_candidate_link
    # Every trial begins with the candidate down. The final topology must have
    # complete measured telemetry before the comparable traffic window starts.
    net = create_braess_network(candidate_link_up=False)
    started = time.time()
    child_processes = []
    probe_cookie = _measurement_cookie(started)
    probe_switches = ()
    try:
        net.start()
        _prepare_trial_topology(
            net, candidate_enabled, controller_log, set_candidate_link
        )

        protected_duration = duration + 12
        protected_output = "/tmp/medroute-braess-protected.json"
        protected_error = "/tmp/medroute-braess-ecg-client.log"
        protected_status = "/tmp/medroute-braess-ecg-client.status"
        server_pid = _start_iperf_server(
            net["h4"], ECG_UDP_PORT, "/tmp/medroute-braess-ecg-server.log"
        )
        child_processes.append((net["h4"], server_pid))
        net["h1"].cmd(
            "rm -f {} {} {}".format(
                protected_output, protected_error, protected_status
            )
        )
        protected_pid = _last_line(net["h1"].cmd(
            "sh -c 'iperf3 -c 10.0.0.4 -u -b {rate} -t {duration} -p {port} -J "
            ">{output} 2>{error}; echo $? >{status}' "
            ">/dev/null 2>&1 & echo $!".format(
                rate=iperf_rate(ECG_RATE_BPS), duration=protected_duration,
                port=ECG_UDP_PORT, output=protected_output,
                error=protected_error, status=protected_status,
            )
        ))
        if not protected_pid.isdigit():
            raise RuntimeError("could not start protected ECG iperf3 client")
        child_processes.append((net["h1"], protected_pid))

        # Telemetry for the final trial topology is READY before this measured
        # flow starts, giving every trial the same traffic-window sequence.
        time.sleep(3)

        # Each IMAGING client uses the declared scenario rate. The aggregate
        # offered load and link capacity are recorded in the evidence. This is
        # a stress workload; the controller chooses paths normally, and the
        # experiment records rather than assumes any traffic redistribution.
        for _source, destination in IMAGING_FLOW_PAIRS:
            server_pid = _start_iperf_server(
                net[destination], IMAGING_UDP_PORT,
                "/tmp/medroute-braess-{}-server.log".format(destination),
            )
            child_processes.append((net[destination], server_pid))
        for source, destination in IMAGING_FLOW_PAIRS:
            background_pid = _last_line(net[source].cmd(_background_iperf_command(
                net[destination].IP(), duration + 5, source
            )))
            if not background_pid.isdigit():
                raise RuntimeError("could not start imaging iperf3 client on {}".format(source))
            child_processes.append((net[source], background_pid))
        time.sleep(2)

        selected_ecg_path = _latest_selected_path(
            database_path, "10.0.0.1", "10.0.0.4", "ECG", started
        )
        probe_switches = _install_icmp_measurement_path(
            net, selected_ecg_path, "h1", "h4", probe_cookie
        )
        try:
            ping_output = net["h1"].cmd("ping -c 10 -i 0.2 10.0.0.4")
        finally:
            _remove_icmp_measurement_path(
                net, probe_switches, probe_cookie
            )
            probe_switches = ()
        ping = parse_ping(ping_output)
        # wait is executed by the same persistent Mininet host shell that
        # launched the job, so completion, output flushing, and reaping happen
        # before the JSON and exit-status files are inspected.
        net["h1"].cmd("wait {} >/dev/null 2>&1 || true".format(protected_pid))
        iperf_report, iperf = _read_completed_udp_report(
            net["h1"], protected_output, protected_error, protected_status
        )
        record = {
            "label": label,
            "candidate_link": "up" if candidate_enabled else "down",
            "routing_mode": routing_mode,
            "trial_configuration": {
                "protected_flow": "ECG UDP {} at {} bit/s".format(
                    ECG_UDP_PORT, ECG_RATE_BPS
                ),
                "protected_duration_seconds": protected_duration,
                "background_flows": {
                    "traffic_type": "IMAGING",
                    "count": len(IMAGING_FLOW_PAIRS),
                    "protocol": "UDP",
                    "destination_port": IMAGING_UDP_PORT,
                    "rate_per_flow_bps": IMAGING_RATE_BPS,
                    "aggregate_offered_rate_bps": len(IMAGING_FLOW_PAIRS) * IMAGING_RATE_BPS,
                },
                "inter_switch_capacity_bps": INTERSWITCH_CAPACITY_BPS,
                "inter_switch_capacity_mbps": INTERSWITCH_CAPACITY_MBPS,
                "latency_probe_path": list(selected_ecg_path),
                "latency_probe_method": "ICMP RTT pinned to selected ECG switch path",
            },
            "started_at": started,
            "finished_at": time.time(),
            "ping_output": ping_output,
            "ping": ping,
            "iperf": iperf_report,
        }
        raw_directory = Path("results/raw")
        raw_directory.mkdir(parents=True, exist_ok=True)
        raw_path = raw_directory / "{}-braess-{}-{}.json".format(int(started), label, routing_mode)
        raw_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        store = ResultsStore(database_path)
        store.record_experiment({
            "scenario": "braess_{}".format(label), "routing_mode": routing_mode,
            "traffic_type": "ECG", "latency_ms": ping["latency_ms"],
            "jitter_ms": iperf["jitter_ms"], "packet_loss_pct": iperf["packet_loss_pct"],
            "throughput_bps": iperf["throughput_bps"],
            "selected_path_json": json.dumps(list(selected_ecg_path)),
            "notes": "candidate_link={}; load={}x{}bps_IMAGING; capacity={}M; "
                     "latency=ICMP_RTT_pinned_to_ECG_path; raw evidence={}".format(
                "up" if candidate_enabled else "down",
                len(IMAGING_FLOW_PAIRS), IMAGING_RATE_BPS,
                INTERSWITCH_CAPACITY_MBPS, raw_path
            ),
        })
        store.close()
        return raw_path
    finally:
        if probe_switches:
            _remove_icmp_measurement_path(
                net, probe_switches, probe_cookie, strict=False
            )
        for host, pid in reversed(child_processes):
            _stop_child(host, pid)
        net.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--candidate-link", choices=("up", "down"), required=True)
    parser.add_argument("--routing-mode", choices=("qos", "medroute"), required=True)
    parser.add_argument("--duration", type=int, default=10)
    parser.add_argument("--controller-log", required=True)
    parser.add_argument("--database", default="results/medroute.db")
    args = parser.parse_args()
    result = measure(
        args.label, args.candidate_link == "up", args.routing_mode, args.duration,
        args.controller_log, args.database,
    )
    print("Braess trial evidence written to {}".format(result))
