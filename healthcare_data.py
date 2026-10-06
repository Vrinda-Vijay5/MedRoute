"""Replaceable healthcare metadata sources for MedRoute.

These records describe traffic; they never contain private patient data. The
SDN controller consumes the same fields regardless of whether records come
from the synthetic source, a CSV dataset, or a future wearable adapter.
"""

from __future__ import annotations

import csv
import random
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional


WORKLOAD_PROFILE_CSV = Path(__file__).resolve().parent / "data" / "healthcare_traffic_profiles.csv"


def load_healthcare_workload_profiles(path: str | Path = WORKLOAD_PROFILE_CSV) -> Dict[str, Dict[str, object]]:
    """Load declared traffic workloads, not measured QoS outcomes, from CSV."""
    required = {
        "traffic_type", "criticality", "transport_protocol", "destination_port",
        "offered_rate_bps", "packet_size_bytes", "duration_seconds",
        "source_role", "destination_role", "workload_rationale",
    }
    profiles: Dict[str, Dict[str, object]] = {}
    with Path(path).open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError("workload profile CSV is missing columns: {}".format(", ".join(sorted(missing))))
        for line_number, row in enumerate(reader, start=2):
            name = row["traffic_type"].strip().upper()
            if not name or name in profiles:
                raise ValueError("empty or duplicate traffic_type on CSV line {}".format(line_number))
            protocol = row["transport_protocol"].strip().upper()
            if protocol not in {"TCP", "UDP"}:
                raise ValueError("unsupported transport_protocol on CSV line {}".format(line_number))
            try:
                numeric = {
                    "criticality": int(row["criticality"]),
                    "destination_port": int(row["destination_port"]),
                    "offered_rate_bps": int(row["offered_rate_bps"]),
                    "packet_size_bytes": int(row["packet_size_bytes"]),
                    "duration_seconds": int(row["duration_seconds"]),
                }
            except (TypeError, ValueError) as error:
                raise ValueError("invalid numeric workload field on CSV line {}".format(line_number)) from error
            if not 1 <= numeric["criticality"] <= 5:
                raise ValueError("criticality must be in [1, 5] on CSV line {}".format(line_number))
            if not 1 <= numeric["destination_port"] <= 65535 or any(
                numeric[key] <= 0 for key in ("offered_rate_bps", "packet_size_bytes", "duration_seconds")
            ):
                raise ValueError("workload values must be positive and port valid on CSV line {}".format(line_number))
            profiles[name] = {
                **numeric,
                "traffic_type": name,
                "transport_protocol": protocol,
                "source_role": row["source_role"].strip(),
                "destination_role": row["destination_role"].strip(),
                "workload_rationale": row["workload_rationale"].strip(),
            }
    expected = {"ECG", "ICU", "TELEMEDICINE", "EHR", "IMAGING", "CCTV"}
    if set(profiles) != expected:
        raise ValueError("workload profile CSV must define exactly {}".format(sorted(expected)))
    return profiles


@dataclass(frozen=True)
class HealthcareRecord:
    record_id: str
    traffic_type: str
    timestamp: float
    source: str = "10.0.0.1"
    destination: str = "10.0.0.2"
    payload_bytes: int = 256
    expected_rate_bps: int = 1_000_000
    transport_protocol: str = "UDP"
    destination_port: int = 0
    criticality: int = 0
    duration_seconds: int = 60

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class HealthcareDataSource(ABC):
    """Interface shared by present and future healthcare data adapters."""

    @abstractmethod
    def records(self, count: Optional[int] = None) -> Iterable[HealthcareRecord]:
        """Return traffic metadata records without patient-identifying data."""


class SyntheticHealthcareDataSource(HealthcareDataSource):
    WORKLOAD_PROFILES = load_healthcare_workload_profiles()
    TRAFFIC_TYPES = tuple(WORKLOAD_PROFILES)
    DEFAULT_RATES = {
        name: int(profile["offered_rate_bps"])
        for name, profile in WORKLOAD_PROFILES.items()
    }

    def __init__(self, seed: Optional[int] = None) -> None:
        self._random = random.Random(seed)

    def records(self, count: Optional[int] = 10) -> Iterable[HealthcareRecord]:
        if count is None:
            raise ValueError("The synthetic source requires a finite count")
        if count < 0:
            raise ValueError("count must be non-negative")
        started = time.time()
        return [self._make_record(index + 1, started + index * 0.001) for index in range(count)]

    def _make_record(self, sequence: int, timestamp: float) -> HealthcareRecord:
        traffic_type = self._random.choice(self.TRAFFIC_TYPES)
        profile = self.WORKLOAD_PROFILES[traffic_type]
        return HealthcareRecord(
            record_id=str(sequence),
            traffic_type=traffic_type,
            timestamp=timestamp,
            payload_bytes=int(profile["packet_size_bytes"]),
            expected_rate_bps=int(profile["offered_rate_bps"]),
            transport_protocol=str(profile["transport_protocol"]),
            destination_port=int(profile["destination_port"]),
            criticality=int(profile["criticality"]),
            duration_seconds=int(profile["duration_seconds"]),
        )


class CsvHealthcareDataSource(HealthcareDataSource):
    """Read non-sensitive traffic metadata from a CSV file."""

    REQUIRED_COLUMNS = {"record_id", "traffic_type", "timestamp"}

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self, count: Optional[int] = None) -> Iterable[HealthcareRecord]:
        with self.path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            missing = self.REQUIRED_COLUMNS.difference(reader.fieldnames or [])
            if missing:
                raise ValueError("CSV is missing columns: {}".format(", ".join(sorted(missing))))
            return list(self._read_rows(reader, count))

    @staticmethod
    def _read_rows(reader: csv.DictReader, count: Optional[int]) -> Iterator[HealthcareRecord]:
        for index, row in enumerate(reader):
            if count is not None and index >= count:
                break
            traffic_type = row["traffic_type"].upper()
            profile = SyntheticHealthcareDataSource.WORKLOAD_PROFILES.get(traffic_type, {})
            yield HealthcareRecord(
                record_id=row["record_id"],
                traffic_type=traffic_type,
                timestamp=float(row["timestamp"]),
                source=row.get("source") or "10.0.0.1",
                destination=row.get("destination") or "10.0.0.2",
                payload_bytes=int(row.get("payload_bytes") or profile.get("packet_size_bytes", 256)),
                expected_rate_bps=int(row.get("expected_rate_bps") or profile.get("offered_rate_bps", 1_000_000)),
                transport_protocol=(row.get("transport_protocol") or profile.get("transport_protocol", "UDP")).upper(),
                destination_port=int(row.get("destination_port") or profile.get("destination_port", 0)),
                criticality=int(row.get("criticality") or profile.get("criticality", 0)),
                duration_seconds=int(row.get("duration_seconds") or profile.get("duration_seconds", 60)),
            )
