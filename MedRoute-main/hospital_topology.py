"""Reproducible four-switch MedRoute Mininet topology.

Run only on Linux/WSL2 with Mininet and Open vSwitch installed. The Ryu
controller must already be listening on TCP 6653.
"""

from __future__ import annotations

import argparse

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSKernelSwitch, RemoteController


def create_network(scenario: str = "normal") -> Mininet:
    if scenario not in {"normal", "congestion", "loss", "delay", "jitter"}:
        raise ValueError("unsupported scenario: {}".format(scenario))
    net = Mininet(controller=None, switch=OVSKernelSwitch, link=TCLink, autoSetMacs=True)
    net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6653)

    h1 = net.addHost("h1", ip="10.0.0.1/24")
    h2 = net.addHost("h2", ip="10.0.0.2/24")
    bg1 = net.addHost("bg1", ip="10.0.0.3/24")
    bg2 = net.addHost("bg2", ip="10.0.0.4/24")
    switches = [net.addSwitch("s{}".format(index), protocols="OpenFlow13") for index in range(1, 5)]
    s1, s2, s3, s4 = switches

    net.addLink(h1, s1, bw=100, delay="1ms")
    net.addLink(bg1, s1, bw=100, delay="1ms")
    net.addLink(h2, s4, bw=100, delay="1ms")
    net.addLink(bg2, s4, bw=100, delay="1ms")

    route_a = {"bw": 100, "delay": "2ms"}
    route_b = {"bw": 100, "delay": "5ms"}
    if scenario == "loss":
        route_a["loss"] = 3
    elif scenario == "delay":
        route_a["delay"] = "30ms"
    net.addLink(s1, s2, **route_a)
    net.addLink(s2, s4, **route_a)
    net.addLink(s1, s3, **route_b)
    net.addLink(s3, s4, **route_b)
    return net


def apply_jitter(net: Mininet) -> None:
    """Apply measured netem delay variation to route A for the jitter scenario."""
    for left, right in ((net["s1"], net["s2"]), (net["s2"], net["s4"])):
        for interface in left.connectionsTo(right)[0]:
            interface.node.cmd(
                "tc qdisc replace dev {} root netem delay 8ms 4ms distribution normal".format(interface.name)
            )


def print_topology() -> None:
    print("\n========== MedRoute hospital topology ==========")
    print("               s2")
    print("              /  \\")
    print("h1,bg1 --- s1    s4 --- h2,bg2")
    print("              \\  /")
    print("               s3")
    print("Route A: s1 -> s2 -> s4 (shorter)")
    print("Route B: s1 -> s3 -> s4 (alternative)")
    print("Healthcare UDP ports: ECG 5001, ICU 5002, TELEMEDICINE 5003,")
    print("                      EHR 5004, IMAGING 5005, CCTV 5006")
    print("================================================\n")


def run(scenario: str = "normal") -> None:
    net = create_network(scenario)
    try:
        net.start()
        if scenario == "jitter":
            apply_jitter(net)
        print_topology()
        print("Try: h1 ping -c 5 h2")
        print("Try: h2 iperf3 -s -p 5001 &")
        print("     h1 iperf3 -c 10.0.0.2 -u -b 2M -t 10 -p 5001")
        CLI(net)
    finally:
        net.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("normal", "congestion", "loss", "delay", "jitter"),
                        default="normal")
    args = parser.parse_args()
    setLogLevel("info")
    run(args.scenario)

