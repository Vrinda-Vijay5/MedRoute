"""Backward-compatible synthetic traffic metadata generator."""

from __future__ import annotations

from typing import Dict, List, Optional

from healthcare_data import SyntheticHealthcareDataSource


class HealthcareTrafficGenerator:
    TRAFFIC_TYPES = list(SyntheticHealthcareDataSource.TRAFFIC_TYPES)

    def __init__(self, seed: Optional[int] = None) -> None:
        self.source = SyntheticHealthcareDataSource(seed=seed)

    def generate(self, count: int = 10) -> List[Dict[str, object]]:
        generated = []
        for record in self.source.records(count):
            item = record.to_dict()
            item["id"] = item.pop("record_id")
            item["type"] = item.pop("traffic_type")
            generated.append(item)
        return generated


if __name__ == "__main__":
    for packet in HealthcareTrafficGenerator().generate(10):
        print("Packet ID: {} | Type: {} | Rate: {} bps".format(
            packet["id"], packet["type"], packet["expected_rate_bps"]
        ))

