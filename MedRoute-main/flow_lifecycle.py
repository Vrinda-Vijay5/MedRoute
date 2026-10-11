"""Pure helpers for controller-owned OpenFlow rule lifecycle.

The controller uses a MedRoute cookie namespace, monotonically increasing
route versions, protocol-aware matches, and destination-to-source install
order.  These helpers contain no Ryu dependency, so the rule plan is unit
testable outside Mininet.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence, Tuple

from traffic_classifier import ipv4_openflow_match_fields


COOKIE_NAMESPACE = 0x4D52_0000_0000_0000
COOKIE_NAMESPACE_MASK = 0xFFFF_0000_0000_0000


def make_cookie(flow_id: str, route_version: int, direction: str) -> int:
    if route_version < 1 or route_version > 0x7FFF:
        raise ValueError("route_version must be between 1 and 32767")
    if direction not in {"forward", "reverse"}:
        raise ValueError("direction must be forward or reverse")
    digest = int.from_bytes(
        hashlib.blake2s(flow_id.encode("utf-8"), digest_size=4).digest(), "big"
    )
    direction_bit = 1 if direction == "reverse" else 0
    return COOKIE_NAMESPACE | (route_version << 33) | (direction_bit << 32) | digest


@dataclass(frozen=True)
class RuleSpec:
    dpid: int
    output_port: int
    match_fields: Mapping[str, object]
    direction: str


def reverse_transport_ports(
    source_port: Optional[int], destination_port: Optional[int]
) -> Tuple[Optional[int], Optional[int]]:
    return destination_port, source_port


def build_rule_plan(
    path: Sequence[int], source_ip: str, destination_ip: str, ip_protocol: int,
    source_port: Optional[int], destination_port: Optional[int],
    destination_host_port: int, port_map: Mapping[Tuple[int, int], int],
    direction: str = "forward",
) -> Tuple[RuleSpec, ...]:
    """Return rules in safe destination-to-source installation order."""
    path_tuple = tuple(int(dpid) for dpid in path)
    if not path_tuple:
        raise ValueError("path cannot be empty")
    fields = ipv4_openflow_match_fields(
        source_ip, destination_ip, ip_protocol, source_port, destination_port
    )
    rules = []
    for index, dpid in enumerate(path_tuple):
        if index == len(path_tuple) - 1:
            output_port = int(destination_host_port)
        else:
            edge = (dpid, path_tuple[index + 1])
            if edge not in port_map:
                raise ValueError("missing switch port for edge {}".format(edge))
            output_port = int(port_map[edge])
        rules.append(RuleSpec(dpid, output_port, fields, direction))
    return tuple(reversed(rules))


def path_contains_any_edge(
    path: Sequence[int], edges: Sequence[Tuple[int, int]]
) -> bool:
    return bool(set(zip(path, path[1:])).intersection(edges))
