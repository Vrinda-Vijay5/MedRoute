#!/usr/bin/env python3
"""Send explicitly labeled test input to the patient gateway."""

import argparse
import json
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", default="http://127.0.0.1:8091/v1/readings")
    parser.add_argument("--source-id", default="wearable-test-01")
    parser.add_argument("--priority", type=int, choices=range(0, 6), default=5)
    parser.add_argument("--source-ip", default="10.0.0.1")
    parser.add_argument("--destination-ip", default="10.0.0.2")
    parser.add_argument("--destination-port", type=int, default=5001)
    parser.add_argument("--freshness-seconds", type=float, default=10.0)
    args = parser.parse_args()
    payload = {
        "source_id": args.source_id,
        "source_type": "test-generator",
        "interface": "http-json-test",
        "timestamp": time.time(),
        "freshness_seconds": args.freshness_seconds,
        "readings": [
            {"metric": "priority_signal", "value": args.priority, "unit": "level"}
        ],
        "flow_binding": {
            "source_ip": args.source_ip,
            "destination_ip": args.destination_ip,
            "ip_protocol": 17,
            "destination_port": args.destination_port,
        },
        "metadata": {"label": "non-clinical demonstration input"},
    }
    request = urllib.request.Request(
        args.gateway, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        print(response.read().decode("utf-8"))


if __name__ == "__main__":
    main()
