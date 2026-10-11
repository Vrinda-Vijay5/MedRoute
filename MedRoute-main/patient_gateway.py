"""Small non-blocking HTTP gateway for patient-source adapters.

The dashboard is read-only; this gateway is the separate ingestion boundary.
Until a real device/app/API is identified, run it only in ``test`` mode.
"""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from patient_integration import (
    DemonstrationCriticalityPolicy, PatientValidationError, validate_update,
)
from storage import ResultsStore


class PatientGateway:
    def __init__(self, database: str, mode: str, adapter_name: str,
                 policy_path: str) -> None:
        if mode == "live" and adapter_name in {"generic", "test-webhook"}:
            raise ValueError("live mode requires a named, source-specific verified adapter")
        self.mode = mode
        self.adapter_name = adapter_name
        self.store = ResultsStore(database)
        self.policy = DemonstrationCriticalityPolicy(policy_path)
        self.store.record_status(
            "patient_gateway", "ready", mode,
            "measured" if mode == "live" else mode,
            {"adapter": adapter_name, "policy": self.policy.name},
        )

    def ingest(self, payload):
        update = validate_update(payload, self.mode)
        criticality, reason = self.policy.evaluate(update.readings)
        self.store.record_patient_update(
            update.source_id, update.source_type, update.interface,
            update.operating_mode, update.freshness_seconds, update.readings,
            update.flow_binding, criticality, reason, self.policy.name,
            update.timestamp, time.time(), update.metadata,
        )
        return {
            "accepted": True, "source_id": update.source_id,
            "operating_mode": update.operating_mode,
            "criticality": criticality, "policy": self.policy.name,
            "expires_at": update.timestamp + update.freshness_seconds,
            "routing_binding": dict(update.flow_binding),
        }


def handler_factory(gateway: PatientGateway):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/v1/readings":
                self._json(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 65536:
                    raise PatientValidationError("body must be 1-65536 bytes")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise PatientValidationError("body must be a JSON object")
                self._json(202, gateway.ingest(payload))
            except (PatientValidationError, json.JSONDecodeError) as error:
                self._json(400, {"accepted": False, "error": str(error)})

        def do_GET(self):
            if self.path == "/health":
                self._json(200, {"status": "ready", "operating_mode": gateway.mode,
                                 "adapter": gateway.adapter_name})
            else:
                self._json(404, {"error": "not found"})

        def log_message(self, format, *args):
            return

        def _json(self, status, body):
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="results/medroute.db")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--mode", choices=("test", "replay", "live"), default="test")
    parser.add_argument("--adapter-name", default="test-webhook")
    parser.add_argument("--policy", default="config/criticality_policy.json")
    args = parser.parse_args()
    gateway = PatientGateway(args.database, args.mode, args.adapter_name, args.policy)
    print("Patient gateway {} mode on http://{}:{}".format(args.mode, args.host, args.port))
    ThreadingHTTPServer((args.host, args.port), handler_factory(gateway)).serve_forever()


if __name__ == "__main__":
    main()
