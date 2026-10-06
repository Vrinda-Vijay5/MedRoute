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


@dataclass(frozen=True)
class HealthcareRecord:
    record_id: str
    traffic_type: str
    timestamp: float
    source: str = "10.0.0.1"
    destination: str = "10.0.0.2"
    payload_bytes: int = 256
    expected_rate_bps: int = 1_000_000

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class HealthcareDataSource(ABC):
    """Interface shared by present and future healthcare data adapters."""

    @abstractmethod
    def records(self, count: Optional[int] = None) -> Iterable[HealthcareRecord]:
        """Return traffic metadata records without patient-identifying data."""


class SyntheticHealthcareDataSource(HealthcareDataSource):
    TRAFFIC_TYPES = ("ECG", "ICU", "TELEMEDICINE", "EHR", "IMAGING", "CCTV")
    DEFAULT_RATES = {
        "ECG": 500_000,
        "ICU": 1_000_000,
        "TELEMEDICINE": 4_000_000,
        "EHR": 1_000_000,
        "IMAGING": 20_000_000,
        "CCTV": 8_000_000,
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
        return HealthcareRecord(
            record_id=str(sequence),
            traffic_type=traffic_type,
            timestamp=timestamp,
            expected_rate_bps=self.DEFAULT_RATES[traffic_type],
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
            yield HealthcareRecord(
                record_id=row["record_id"],
                traffic_type=row["traffic_type"].upper(),
                timestamp=float(row["timestamp"]),
                source=row.get("source") or "10.0.0.1",
                destination=row.get("destination") or "10.0.0.2",
                payload_bytes=int(row.get("payload_bytes") or 256),
                expected_rate_bps=int(row.get("expected_rate_bps") or 1_000_000),
            )
