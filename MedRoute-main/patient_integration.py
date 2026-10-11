"""Validated patient-source envelopes and demonstration routing policy.

This module deliberately avoids patient identity fields.  A source_id is a
pseudonymous device/application identifier.  Policy output controls routing
priority only and is not a diagnosis, alarm, or treatment recommendation.
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple


SOURCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
VALID_MODES = {"test", "replay", "live"}
METRIC_UNITS = {
    "heart_rate": {"bpm"},
    "spo2": {"percent"},
    "respiratory_rate": {"breaths/min"},
    "temperature": {"degC"},
    # priority_signal is an upstream, non-clinical demo input. It is the only
    # default policy trigger until a real source and approved mapping exist.
    "priority_signal": {"level"},
}


@dataclass(frozen=True)
class ValidatedPatientUpdate:
    source_id: str
    source_type: str
    interface: str
    operating_mode: str
    timestamp: float
    freshness_seconds: float
    readings: Tuple[Mapping[str, object], ...]
    flow_binding: Mapping[str, object]
    metadata: Mapping[str, object]


class PatientValidationError(ValueError):
    pass


def validate_update(payload: Mapping[str, object], operating_mode: str,
                    now: Optional[float] = None) -> ValidatedPatientUpdate:
    if operating_mode not in VALID_MODES:
        raise PatientValidationError("operating mode must be test, replay, or live")
    clock = time.time() if now is None else now
    source_id = str(payload.get("source_id", ""))
    if not SOURCE_ID_PATTERN.fullmatch(source_id):
        raise PatientValidationError("source_id must be 1-64 safe identifier characters")
    source_type = str(payload.get("source_type", ""))
    interface = str(payload.get("interface", ""))
    if not source_type or not interface:
        raise PatientValidationError("source_type and interface are required")
    try:
        timestamp = float(payload["timestamp"])
        freshness_seconds = float(payload.get("freshness_seconds", 10.0))
    except (KeyError, TypeError, ValueError) as error:
        raise PatientValidationError("timestamp and freshness_seconds must be numeric") from error
    if timestamp > clock + 60:
        raise PatientValidationError("source timestamp is more than 60 seconds in the future")
    if timestamp < clock - 86400:
        raise PatientValidationError("source timestamp is more than 24 hours old")
    if not 1 <= freshness_seconds <= 300:
        raise PatientValidationError("freshness_seconds must be between 1 and 300")

    raw_readings = payload.get("readings")
    if not isinstance(raw_readings, list) or not raw_readings:
        raise PatientValidationError("readings must be a non-empty list")
    readings = []
    for raw in raw_readings:
        if not isinstance(raw, Mapping):
            raise PatientValidationError("each reading must be an object")
        metric = str(raw.get("metric", ""))
        unit = str(raw.get("unit", ""))
        if metric not in METRIC_UNITS or unit not in METRIC_UNITS[metric]:
            raise PatientValidationError(
                "unsupported metric/unit pair: {}/{}".format(metric, unit)
            )
        try:
            value = float(raw["value"])
        except (KeyError, TypeError, ValueError) as error:
            raise PatientValidationError("reading value must be numeric") from error
        if not math.isfinite(value):
            raise PatientValidationError("reading value must be finite")
        readings.append({"metric": metric, "value": value, "unit": unit})

    raw_binding = payload.get("flow_binding")
    if not isinstance(raw_binding, Mapping):
        raise PatientValidationError("flow_binding is required for routing integration")
    try:
        binding = {
            "source_ip": str(raw_binding["source_ip"]),
            "destination_ip": str(raw_binding["destination_ip"]),
            "ip_protocol": int(raw_binding["ip_protocol"]),
            "source_port": _optional_port(raw_binding.get("source_port")),
            "destination_port": _optional_port(raw_binding.get("destination_port")),
        }
    except (KeyError, TypeError, ValueError) as error:
        raise PatientValidationError("invalid flow_binding") from error
    if binding["ip_protocol"] not in (1, 6, 17):
        raise PatientValidationError("ip_protocol must be ICMP(1), TCP(6), or UDP(17)")
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise PatientValidationError("metadata must be an object")
    forbidden = {"patient_name", "patient_id", "email", "phone", "address"}
    if forbidden.intersection(str(key).lower() for key in metadata):
        raise PatientValidationError("identifying patient metadata is not accepted")
    return ValidatedPatientUpdate(
        source_id, source_type, interface, operating_mode, timestamp,
        freshness_seconds, tuple(readings), binding, dict(metadata),
    )


def _optional_port(value: object) -> Optional[int]:
    if value is None:
        return None
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError("port out of range")
    return port


class DemonstrationCriticalityPolicy:
    OPERATORS = {
        ">=": lambda value, threshold: value >= threshold,
        ">": lambda value, threshold: value > threshold,
        "<=": lambda value, threshold: value <= threshold,
        "<": lambda value, threshold: value < threshold,
        "==": lambda value, threshold: value == threshold,
    }

    def __init__(self, path: str | Path = "config/criticality_policy.json") -> None:
        self.path = Path(path)
        self.config = json.loads(self.path.read_text(encoding="utf-8"))
        self.name = str(self.config["name"])
        self.default_criticality = int(self.config["default_criticality"])
        if not 0 <= self.default_criticality <= 5:
            raise ValueError("default_criticality must be between 0 and 5")

    def evaluate(self, readings: Sequence[Mapping[str, object]]) -> Tuple[int, str]:
        values = {str(item["metric"]): float(item["value"]) for item in readings}
        for rule in self.config.get("rules", []):
            metric = str(rule["metric"])
            operator = str(rule["operator"])
            if metric in values and self.OPERATORS[operator](
                values[metric], float(rule["threshold"])
            ):
                return int(rule["criticality"]), str(rule["reason"])
        return self.default_criticality, "demonstration policy default"
