"""Transparent healthcare traffic classification and QoS profiles."""

from __future__ import annotations

from copy import deepcopy
from typing import Dict, Optional


IPPROTO_TCP = 6
IPPROTO_UDP = 17


def build_flow_id(
    source_ip: str,
    destination_ip: str,
    source_port: Optional[int] = None,
    destination_port: Optional[int] = None,
) -> str:
    """Build an inspectable flow key while retaining measured L4 ports."""
    return "{}:{}->{}:{}".format(
        source_ip,
        source_port if source_port is not None else 0,
        destination_ip,
        destination_port if destination_port is not None else 0,
    )


def ipv4_openflow_match_fields(
    source_ip: str,
    destination_ip: str,
    ip_protocol: int,
    source_port: Optional[int] = None,
    destination_port: Optional[int] = None,
) -> Dict[str, object]:
    """Return a protocol-specific OpenFlow IPv4 match.

    Including ``ip_proto`` prevents an ICMP rule learned during ``pingall``
    from consuming later UDP/TCP healthcare packets for the same host pair.
    """
    fields: Dict[str, object] = {
        "eth_type": 0x0800,
        "ipv4_src": source_ip,
        "ipv4_dst": destination_ip,
        "ip_proto": int(ip_protocol),
    }
    if ip_protocol == IPPROTO_UDP:
        if source_port is not None:
            fields["udp_src"] = int(source_port)
        if destination_port is not None:
            fields["udp_dst"] = int(destination_port)
    elif ip_protocol == IPPROTO_TCP:
        if source_port is not None:
            fields["tcp_src"] = int(source_port)
        if destination_port is not None:
            fields["tcp_dst"] = int(destination_port)
    return fields


class HealthcareTrafficClassifier:
    """Classify traffic using an explicit application type or UDP port."""

    TRAFFIC_TYPES = {
        "ECG": {
            "class": "EMERGENCY", "criticality": 5, "udp_port": 5001,
            "weights": {"latency": 0.35, "jitter": 0.30, "loss": 0.25, "utilization": 0.10},
            "reason": "Continuous life-critical waveform; late or missing samples reduce clinical value.",
        },
        "ICU": {
            "class": "CRITICAL", "criticality": 5, "udp_port": 5002,
            "weights": {"latency": 0.35, "jitter": 0.25, "loss": 0.30, "utilization": 0.10},
            "reason": "Bedside alarms and vital signs require low delay and very high delivery reliability.",
        },
        "TELEMEDICINE": {
            "class": "HIGH", "criticality": 4, "udp_port": 5003,
            "weights": {"latency": 0.35, "jitter": 0.35, "loss": 0.15, "utilization": 0.15},
            "reason": "Interactive audio/video quality is dominated by delay and jitter.",
        },
        "EHR": {
            "class": "HIGH", "criticality": 4, "udp_port": 5004,
            "weights": {"latency": 0.20, "jitter": 0.10, "loss": 0.35, "utilization": 0.35},
            "reason": "Clinical records must arrive reliably; modest delay variation is tolerable.",
        },
        "IMAGING": {
            "class": "MEDIUM", "criticality": 3, "udp_port": 5005,
            "weights": {"latency": 0.15, "jitter": 0.05, "loss": 0.20, "utilization": 0.60},
            "reason": "Large image transfers benefit most from uncongested, high-available-bandwidth paths.",
        },
        "CCTV": {
            "class": "LOW", "criticality": 1, "udp_port": 5006,
            "weights": {"latency": 0.20, "jitter": 0.25, "loss": 0.15, "utilization": 0.40},
            "reason": "Routine surveillance yields capacity to clinical traffic while retaining usable video.",
        },
    }
    PORT_TO_TYPE = {profile["udp_port"]: name for name, profile in TRAFFIC_TYPES.items()}

    def classify(self, traffic_type: str) -> Dict[str, object]:
        name = str(traffic_type).strip().upper()
        if name not in self.TRAFFIC_TYPES:
            return {
                "traffic_type": name, "class": "UNKNOWN", "criticality": 0,
                "udp_port": None,
                "weights": {"latency": 0.25, "jitter": 0.25, "loss": 0.25, "utilization": 0.25},
                "reason": "Unknown traffic receives a neutral profile.",
            }
        result = deepcopy(self.TRAFFIC_TYPES[name])
        result["traffic_type"] = name
        return result

    def classify_udp_port(self, destination_port: Optional[int]) -> Dict[str, object]:
        return self.classify(self.PORT_TO_TYPE.get(destination_port, "UNKNOWN"))


if __name__ == "__main__":
    classifier = HealthcareTrafficClassifier()
    print("MedRoute healthcare traffic profiles")
    for traffic in classifier.TRAFFIC_TYPES:
        result = classifier.classify(traffic)
        print("{:<14} class={:<9} criticality={} UDP={}".format(
            traffic, result["class"], result["criticality"], result["udp_port"]
        ))
