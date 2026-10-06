"""Dataset-independent healthcare metadata pipeline."""

from __future__ import annotations

from typing import Dict, List, Optional

from healthcare_data import HealthcareDataSource, SyntheticHealthcareDataSource
from traffic_classifier import HealthcareTrafficClassifier


class HealthcarePipeline:
    def __init__(self, source: Optional[HealthcareDataSource] = None) -> None:
        self.source = source or SyntheticHealthcareDataSource()
        self.classifier = HealthcareTrafficClassifier()

    def process(self, count: Optional[int] = 10) -> List[Dict[str, object]]:
        results = []
        for record in self.source.records(count):
            item = record.to_dict()
            item.update(self.classifier.classify(record.traffic_type))
            results.append(item)
        return results


if __name__ == "__main__":
    print("MedRoute healthcare pipeline")
    for packet in HealthcarePipeline().process(10):
        print("ID: {} | Type: {} | Class: {} | Criticality: {}".format(
            packet["record_id"], packet["traffic_type"], packet["class"], packet["criticality"]
        ))
