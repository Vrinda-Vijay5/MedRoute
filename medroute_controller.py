from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, CONFIG_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import ether_types
from ryu.lib.packet import ipv4
from ryu.topology import event
from ryu.topology.api import get_switch, get_link
import networkx as nx


class MedRouteController(app_manager.RyuApp):

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(MedRouteController, self).__init__(*args, **kwargs)

        self.net = nx.DiGraph()

        self.port_map = {}
        self.host_port = {}

        self.mac_to_port = {}

        self.switches = {}
        self.links = []

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):

        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        match = parser.OFPMatch()

        actions = [
            parser.OFPActionOutput(
                ofproto.OFPP_CONTROLLER,
                ofproto.OFPCML_NO_BUFFER
            )
        ]

        self.add_flow(
            datapath,
            0,
            match,
            actions
        )

        self.switches[datapath.id] = datapath

        print("Switch connected:", datapath.id)

    def add_flow(self, datapath, priority, match, actions):

        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [
            parser.OFPInstructionActions(
                ofproto.OFPIT_APPLY_ACTIONS,
                actions
            )
        ]

        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=inst
        )

        datapath.send_msg(mod)

    @set_ev_cls(event.EventSwitchEnter)
    def switch_enter_handler(self, ev):

        self.update_topology()

    @set_ev_cls(event.EventLinkAdd)
    def link_add_handler(self, ev):

        self.update_topology()

    @set_ev_cls(event.EventLinkDelete)
    def link_delete_handler(self, ev):

        self.update_topology()

    def update_topology(self):

        switch_list = get_switch(self, None)

        link_list = get_link(self, None)

        self.net.clear()

        self.port_map.clear()

        for sw in switch_list:

            dpid = sw.dp.id

            self.net.add_node(dpid)

            self.switches[dpid] = sw.dp

        for link in link_list:

            src = link.src.dpid
            dst = link.dst.dpid

            src_port = link.src.port_no

            self.net.add_edge(
                src,
                dst
            )

            self.port_map[
                (src, dst)
            ] = src_port

        print(
            "Discovered switches:",
            list(self.net.nodes())
        )

        print(
            "Discovered links:",
            list(self.net.edges())
        )

    def get_path(self, src_ip, dst_ip):

        try:

            src_switch = self.get_host_switch(src_ip)
            dst_switch = self.get_host_switch(dst_ip)

            if src_switch is None or dst_switch is None:
                return None

            path = nx.shortest_path(
                self.net,
                src_switch,
                dst_switch
            )

            print(
                "Route",
                src_ip,
                "->",
                dst_ip,
                ":",
                path
            )

            return path

        except Exception as e:

            print(
                "Path error:",
                e
            )

            return None

    def get_host_switch(self, ip):

        if ip not in self.host_port:
            return None

        return self.host_port[ip][0]

    def learn_host(self, ip, dpid, port):

        self.host_port[ip] = (
            dpid,
            port
        )

        print(
            "Host learned:",
            ip,
            "->",
            "s{}".format(dpid),
            "port",
            port
        )

    def forward_packet(self, msg, path, dst_ip):

        if path is None:
            return

        datapath = msg.datapath
        dpid = datapath.id
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        if dpid not in path:
            return

        index = path.index(dpid)

        if index == len(path) - 1:

            if dst_ip not in self.host_port:
                print(
                    "ERROR: Destination host not known:",
                    dst_ip
                )
                return

            out_port = self.host_port[dst_ip][1]

        else:

            next_switch = path[index + 1]

            if (dpid, next_switch) not in self.port_map:

                print(
                    "ERROR: No port mapping for",
                    dpid,
                    "->",
                    next_switch
                )

                return

            out_port = self.port_map[
                (dpid, next_switch)
            ]

        print(
            "Forwarding packet:",
            "s{}".format(dpid),
            "-> port",
            out_port
        )

        actions = [
            parser.OFPActionOutput(
                out_port
            )
        ]

        in_port = msg.match["in_port"]

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=ofproto.OFP_NO_BUFFER,
            in_port=in_port,
            actions=actions,
            data=msg.data
        )

        datapath.send_msg(out)

    @set_ev_cls(
        ofp_event.EventOFPPacketIn,
        MAIN_DISPATCHER
    )
    def packet_in_handler(self, ev):

        msg = ev.msg

        datapath = msg.datapath

        dpid = datapath.id

        in_port = msg.match["in_port"]

        pkt = packet.Packet(
            msg.data
        )

        eth = pkt.get_protocol(
            ethernet.ethernet
        )

        if eth is None:
            return

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        src_mac = eth.src
        dst_mac = eth.dst

        self.mac_to_port.setdefault(
            dpid,
            {}
        )

        self.mac_to_port[dpid][
            src_mac
        ] = in_port

        ip_pkt = pkt.get_protocol(
            ipv4.ipv4
        )

        if ip_pkt is not None:

            src_ip = ip_pkt.src
            dst_ip = ip_pkt.dst

            self.learn_host(
                src_ip,
                dpid,
                in_port
            )

            path = self.get_path(
                src_ip,
                dst_ip
            )

            if path is not None:

                self.forward_packet(
                    msg,
                    path,
                    dst_ip
                )

                return

        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        if dst_mac in self.mac_to_port[dpid]:

            out_port = self.mac_to_port[
                dpid
            ][dst_mac]

        else:

            out_port = ofproto.OFPP_FLOOD

        actions = [
            parser.OFPActionOutput(
                out_port
            )
        ]

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=ofproto.OFP_NO_BUFFER,
            in_port=in_port,
            actions=actions,
            data=msg.data
        )

        datapath.send_msg(out)
