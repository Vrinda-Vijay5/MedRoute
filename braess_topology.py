"""Four-switch topology for empirical Braess-type degradation trials.

The central s2--s3 link is the apparently attractive candidate. Whether the
measured run actually exhibits degradation depends on offered load and is
recorded rather than assumed.
"""

from __future__ import annotations

from mininet.link import TCLink
from mininet.net import Mininet
from mininet.node import OVSKernelSwitch, RemoteController


def create_braess_network(candidate_link_up: bool = False) -> Mininet:
    net = Mininet(controller=None, switch=OVSKernelSwitch, link=TCLink, autoSetMacs=True)
    net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6653)
    source_ids = (1, 2, 3, 7, 8, 9)
    destination_ids = (4, 5, 6, 10, 11, 12)
    sources = [
        net.addHost("h{}".format(index), ip="10.0.0.{}/24".format(index))
        for index in source_ids
    ]
    destinations = [
        net.addHost("h{}".format(index), ip="10.0.0.{}/24".format(index))
        for index in destination_ids
    ]
    s1, s2, s3, s4 = [
        net.addSwitch("s{}".format(index), protocols="OpenFlow13") for index in range(1, 5)
    ]
    for host in sources:
        net.addLink(host, s1, bw=100, delay="1ms")
    for host in destinations:
        net.addLink(host, s4, bw=100, delay="1ms")
    # Five 20 Mbit/s imaging flows fit together on one 100 Mbit/s branch. The
    # central link is attractive because it joins the two low-delay edges, but
    # using it makes the 100 Mbit/s aggregate also consume the ECG branch.
    net.addLink(s1, s2, bw=100, delay="0.5ms")
    net.addLink(s2, s4, bw=100, delay="12ms")
    net.addLink(s1, s3, bw=100, delay="12ms")
    net.addLink(s3, s4, bw=100, delay="0.5ms")
    central = net.addLink(s2, s3, bw=100, delay="0.5ms")
    net.braess_candidate_link = central
    net.braess_candidate_initially_up = candidate_link_up
    return net


def set_candidate_link(net: Mininet, enabled: bool) -> None:
    state = "up" if enabled else "down"
    net.configLinkStatus("s2", "s3", state)
