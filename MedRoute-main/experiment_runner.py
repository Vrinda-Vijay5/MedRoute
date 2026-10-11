"""Run real Mininet measurements and persist raw/structured evidence.

This script never supplies fallback numbers. Missing tools or malformed output
fail the experiment so an incomplete run cannot be mistaken for evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Dict, Mapping, Sequence

from storage import ResultsStore
from traffic_classifier import HealthcareTrafficClassifier


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
    time.sleep(1)
    raw = client.cmd("iperf3 -c {} -u -b {} -t {} -p {} -J".format(destination, rate, duration, port))
    try:
        report = json.loads(raw)
        parsed = parse_iperf_udp_report(report)
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise RuntimeError("iperf3 did not return a valid UDP report: {}".format(raw)) from error
    return report, parsed


def run_experiment(scenario: str, routing_mode: str, traffic_type: str, duration: int) -> Path:
    # Keep Mininet optional when importing the JSON parser for unit tests.
    from hospital_topology import apply_jitter, create_network

    profile = HealthcareTrafficClassifier().classify(traffic_type)
    port = int(profile["udp_port"])
    net = create_network(scenario)
    raw_record = {"scenario": scenario, "routing_mode": routing_mode,
                  "traffic_type": traffic_type, "started_at": time.time()}
    try:
        net.start()
        if scenario == "jitter":
            apply_jitter(net)
        time.sleep(8)  # topology discovery and two OpenFlow counter intervals
        if scenario == "congestion":
            net["bg2"].cmd("iperf3 -s -1 -p 6001 >/tmp/medroute-background.log 2>&1 &")
            net["bg1"].cmd("iperf3 -c 10.0.0.4 -u -b 85M -t {} -p 6001 >/tmp/medroute-bg-client.log 2>&1 &".format(duration + 3))
            time.sleep(2)
        ping_output = net["h1"].cmd("ping -c 10 -i 0.2 10.0.0.2")
        ping = parse_ping(ping_output)
        iperf_report, iperf = run_iperf(net["h1"], net["h2"], "10.0.0.2", port, "2M", duration)
        raw_record.update({"ping_output": ping_output, "ping": ping, "iperf": iperf_report,
                           "finished_at": time.time()})
        results = Path("results")
        raw_dir = results / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / "{}-{}-{}-{}.json".format(
            int(raw_record["started_at"]), scenario, routing_mode, traffic_type.lower()
        )
        raw_path.write_text(json.dumps(raw_record, indent=2), encoding="utf-8")
        store = ResultsStore(results / "medroute.db")
        store.record_experiment({
            "scenario": scenario, "routing_mode": routing_mode, "traffic_type": traffic_type,
            "latency_ms": ping["latency_ms"], "jitter_ms": iperf["jitter_ms"],
            "packet_loss_pct": iperf["packet_loss_pct"],
            "throughput_bps": iperf["throughput_bps"],
            "notes": "raw evidence: {}".format(raw_path),
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
    args = parser.parse_args()
    output = run_experiment(args.scenario, args.routing_mode, args.traffic_type, args.duration)
    print("Actual experiment evidence written to {}".format(output))
