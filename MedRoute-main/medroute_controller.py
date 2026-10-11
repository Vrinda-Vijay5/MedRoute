"""Ryu/OpenFlow 1.3 controller for baseline, QoS, and full MedRoute routing."""

from __future__ import annotations

import os
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import networkx as nx
from ryu.base import app_manager
from ryu.base.app_manager import lookup_service_brick
from ryu.controller import ofp_event
from ryu.controller.handler import (
    CONFIG_DISPATCHER, DEAD_DISPATCHER, MAIN_DISPATCHER, set_ev_cls,
)
from ryu.lib import hub
from ryu.lib.packet import arp, ether_types, ethernet, ipv4, packet, tcp, udp
from ryu.ofproto import ofproto_v1_3
from ryu.topology import event
from ryu.topology.api import get_link, get_switch
from ryu.topology.switches import LLDPPacket

from braess_validator import ActiveFlow
from healthcare_data import SyntheticHealthcareDataSource
from network_monitor import NetworkMonitor, find_lldp_send_timestamp
from routing_engine import RoutingDecision, RoutingEngine
from storage import ResultsStore
from traffic_classifier import (
    HealthcareTrafficClassifier,
    build_flow_id,
    ipv4_openflow_match_fields,
)


@dataclass
class ManagedFlow:
    flow_id: str
    source_ip: str
    destination_ip: str
    ip_protocol: int
    source_port: Optional[int]
    destination_port: Optional[int]
    profile: Dict[str, object]
    expected_rate_bps: float
    path: Tuple[int, ...]
    last_change_at: float


class MedRouteController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    MONITOR_INTERVAL = 2.0
    REEVALUATE_INTERVAL = 6.0
    TELEMETRY_LOG_INTERVAL = 10.0

    def __init__(self, *args, **kwargs):
        super(MedRouteController, self).__init__(*args, **kwargs)
        requested_mode = os.environ.get("MEDROUTE_MODE", "medroute").lower()
        self.routing_mode = requested_mode if requested_mode in RoutingEngine.MODES else "medroute"
        self.net = nx.DiGraph()
        self.port_map: Dict[Tuple[int, int], int] = {}
        self.link_ports: Dict[Tuple[int, int], Tuple[int, int]] = {}
        self.switch_ports: Dict[int, set] = {}
        self.host_port: Dict[str, Tuple[int, int]] = {}
        self.host_mac: Dict[str, str] = {}
        self.mac_to_port: Dict[int, Dict[str, int]] = {}
        self.datapaths = {}
        self.managed_flows: Dict[str, ManagedFlow] = {}
        self.classifier = HealthcareTrafficClassifier()
        self.monitor = NetworkMonitor()
        self.router = RoutingEngine()
        configured_capacity = os.environ.get("MEDROUTE_LINK_CAPACITY_BPS")
        self.link_capacity_bps = (
            float(configured_capacity) if configured_capacity else None
        )
        if self.link_capacity_bps is not None and self.link_capacity_bps <= 0:
            raise ValueError("MEDROUTE_LINK_CAPACITY_BPS must be positive")
        result_path = Path(os.environ.get("MEDROUTE_DB", "results/medroute.db"))
        self.store = ResultsStore(result_path)
        self.switches_service = lookup_service_brick("switches")
        self._last_reevaluation = 0.0
        self._last_telemetry_log = 0.0
        self._last_telemetry_signature = None
        self._flow_decision_stage: Dict[str, str] = {}
        self._monitor_thread = hub.spawn(self._monitor_loop)
        self.logger.info(
            "MedRoute controller started in %s mode; evidence=%s; link_capacity_bps=%s",
            self.routing_mode, result_path,
            self.link_capacity_bps if self.link_capacity_bps is not None else "OpenFlow/default",
        )

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        self.datapaths[datapath.id] = datapath
        actions = [datapath.ofproto_parser.OFPActionOutput(
            datapath.ofproto.OFPP_CONTROLLER, datapath.ofproto.OFPCML_NO_BUFFER
        )]
        self.add_flow(datapath, 0, datapath.ofproto_parser.OFPMatch(), actions)
        self.logger.info("Switch connected: s%s", datapath.id)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(datapath.id, None)

    def add_flow(self, datapath, priority, match, actions, idle_timeout=0, hard_timeout=0):
        parser = datapath.ofproto_parser
        instructions = [parser.OFPInstructionActions(datapath.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        datapath.send_msg(parser.OFPFlowMod(
            datapath=datapath, priority=priority, match=match, instructions=instructions,
            idle_timeout=idle_timeout, hard_timeout=hard_timeout,
        ))

    @set_ev_cls(event.EventSwitchEnter)
    @set_ev_cls(event.EventLinkAdd)
    @set_ev_cls(event.EventLinkDelete)
    def topology_change_handler(self, _ev):
        self.update_topology()

    def update_topology(self):
        switch_list = get_switch(self, None)
        link_list = get_link(self, None)
        graph = nx.DiGraph()
        port_map = {}
        link_ports = {}
        switch_ports = {}
        for switch in switch_list:
            graph.add_node(switch.dp.id)
            self.datapaths[switch.dp.id] = switch.dp
            switch_ports[switch.dp.id] = {
                port_no for port_no in switch.dp.ports
                if port_no < switch.dp.ofproto.OFPP_MAX
            }
        for link in link_list:
            edge = (link.src.dpid, link.dst.dpid)
            graph.add_edge(*edge)
            port_map[edge] = link.src.port_no
            link_ports[edge] = (link.src.port_no, link.dst.port_no)
            self.monitor.register_link(
                *edge, link.src.port_no, link.dst.port_no,
                capacity_bps=self.link_capacity_bps,
            )
        self.net = graph
        self.port_map = port_map
        self.link_ports = link_ports
        self.switch_ports = switch_ports
        self.monitor.remove_missing_links(graph.edges())
        self.logger.info("Discovered switches: %s", list(graph.nodes()))
        self.logger.info("Discovered directed links: %s", list(graph.edges()))

    def learn_host(self, ip_address: str, mac_address: str, dpid: int, port_no: int):
        previous = self.host_port.get(ip_address)
        self.host_port[ip_address] = (dpid, port_no)
        self.host_mac[ip_address] = mac_address
        if previous != (dpid, port_no):
            self.logger.info("Host learned: %s (%s) -> s%s port %s", ip_address, mac_address, dpid, port_no)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        in_port = msg.match["in_port"]
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            self._record_lldp(msg)
            return
        self.mac_to_port.setdefault(datapath.id, {})[eth.src] = in_port

        arp_packet = pkt.get_protocol(arp.arp)
        if arp_packet is not None:
            self.learn_host(arp_packet.src_ip, eth.src, datapath.id, in_port)
            self._forward_arp_without_loop(msg, arp_packet.dst_ip)
            return

        ip_packet = pkt.get_protocol(ipv4.ipv4)
        if ip_packet is None:
            self._learning_switch_packet_out(msg, eth.dst)
            return
        self.learn_host(ip_packet.src, eth.src, datapath.id, in_port)
        destination_location = self.host_port.get(ip_packet.dst)
        if destination_location is None:
            self._learning_switch_packet_out(msg, eth.dst)
            return

        udp_packet = pkt.get_protocol(udp.udp)
        tcp_packet = pkt.get_protocol(tcp.tcp)
        transport_packet = udp_packet if udp_packet is not None else tcp_packet
        source_port = transport_packet.src_port if transport_packet is not None else None
        destination_port = transport_packet.dst_port if transport_packet is not None else None
        profile = (
            self.classifier.classify_udp_port(destination_port)
            if udp_packet is not None
            else self.classifier.classify("UNKNOWN")
        )
        flow_id = build_flow_id(
            ip_packet.src, ip_packet.dst, source_port, destination_port
        )
        expected_rate = SyntheticHealthcareDataSource.DEFAULT_RATES.get(
            profile["traffic_type"], 1_000_000
        )
        managed = self.managed_flows.get(flow_id)
        decision = self._make_decision(
            flow_id, ip_packet.src, ip_packet.dst, destination_port, profile,
            expected_rate, managed.path if managed else None,
            managed.last_change_at if managed else 0.0,
        )
        self._record_and_apply_decision(
            flow_id, ip_packet.src, ip_packet.dst, int(ip_packet.proto),
            source_port, destination_port, profile, expected_rate, decision,
        )
        self._packet_out_on_path(msg, decision.selected_path, ip_packet.dst)

    def _make_decision(self, flow_id, source_ip, destination_ip, destination_port,
                       profile, expected_rate, current_path, last_change_at):
        source_switch = self.host_port[source_ip][0]
        destination_switch = self.host_port[destination_ip][0]
        active = [self._as_active_flow(flow) for key, flow in self.managed_flows.items() if key != flow_id]
        return self.router.select_path(
            self.net, source_switch, destination_switch, self.monitor.snapshot(), profile,
            mode=self.routing_mode, current_path=current_path, active_flows=active,
            flow_rate_bps=expected_rate, last_change_at=last_change_at,
        )

    @staticmethod
    def _as_active_flow(flow: ManagedFlow) -> ActiveFlow:
        return ActiveFlow(flow.flow_id, flow.path, str(flow.profile["traffic_type"]),
                          int(flow.profile["criticality"]), flow.expected_rate_bps,
                          dict(flow.profile["weights"]))

    def _record_and_apply_decision(
        self, flow_id, source_ip, destination_ip, ip_protocol, source_port,
        destination_port, profile, expected_rate, decision: RoutingDecision,
    ):
        now = time.time()
        old = self.managed_flows.get(flow_id)
        last_change = now if decision.changed or old is None else old.last_change_at
        self.install_path(
            decision.selected_path, source_ip, destination_ip, ip_protocol,
            source_port, destination_port, int(profile["criticality"]),
        )
        self.managed_flows[flow_id] = ManagedFlow(
            flow_id, source_ip, destination_ip, ip_protocol, source_port,
            destination_port, dict(profile), expected_rate, decision.selected_path, last_change,
        )
        self.store.upsert_flow(flow_id, str(profile["traffic_type"]), int(profile["criticality"]),
                               source_ip, destination_ip, expected_rate, now)
        braess_by_path = dict(decision.braess_results)
        for evaluation in decision.evaluations:
            self.store.record_evaluation(flow_id, evaluation, braess_by_path.get(evaluation.path))
            self.logger.info(self.router.format_evaluation(evaluation))
        self.store.record_decision(flow_id, decision.selected_path, decision.previous_path,
                                   decision.mode, decision.reason, decision.changed)
        self._flow_decision_stage[flow_id] = self._decision_stage(decision)
        if decision.reason.startswith("TELEMETRY WARMUP/FALLBACK"):
            self.logger.warning(
                "TELEMETRY WARMUP/FALLBACK flow=%s type=%s criticality=%s selected=%s reason=%s",
                flow_id, profile["traffic_type"], profile["criticality"],
                decision.selected_path, decision.reason,
            )
        elif decision.mode == "medroute":
            self.logger.info(
                "MEDROUTE QOS/BRAESS DECISION flow=%s type=%s criticality=%s selected=%s reason=%s",
                flow_id, profile["traffic_type"], profile["criticality"],
                decision.selected_path, decision.reason,
            )
        else:
            self.logger.info(
                "ROUTING DECISION flow=%s mode=%s type=%s criticality=%s selected=%s reason=%s",
                flow_id, decision.mode, profile["traffic_type"], profile["criticality"],
                decision.selected_path, decision.reason,
            )
        for path, result in decision.braess_results:
            self.logger.info("BRAESS path=%s status=%s aggregate_change=%.2f%% protected_change=%.2f%% reason=%s",
                             path, result.status, result.aggregate_change_pct,
                             result.worst_protected_change_pct, result.reason)

    def install_path(
        self, path, source_ip, destination_ip, ip_protocol, source_port,
        destination_port, criticality,
    ):
        for index, dpid in enumerate(path):
            datapath = self.datapaths.get(dpid)
            if datapath is None:
                continue
            if index == len(path) - 1:
                out_port = self.host_port[destination_ip][1]
            else:
                out_port = self.port_map.get((dpid, path[index + 1]))
            if out_port is None:
                continue
            fields = ipv4_openflow_match_fields(
                source_ip, destination_ip, ip_protocol, source_port, destination_port
            )
            match = datapath.ofproto_parser.OFPMatch(**fields)
            actions = [datapath.ofproto_parser.OFPActionOutput(out_port)]
            self.add_flow(datapath, 100 + 10 * criticality, match, actions, idle_timeout=20)
            self.logger.info("Installed flow on s%s -> port %s for %s -> %s", dpid, out_port,
                             source_ip, destination_ip)

    def _packet_out_on_path(self, msg, path, destination_ip):
        dpid = msg.datapath.id
        if dpid not in path:
            return
        index = path.index(dpid)
        out_port = (self.host_port[destination_ip][1] if index == len(path) - 1
                    else self.port_map.get((dpid, path[index + 1])))
        if out_port is not None:
            self._send_packet_out(msg, out_port)

    def _learning_switch_packet_out(self, msg, destination_mac):
        ports = self.mac_to_port.get(msg.datapath.id, {})
        out_port = ports.get(destination_mac)
        if out_port is not None:
            self._send_packet_out(msg, out_port)
        else:
            self._controlled_access_flood(msg)

    def _forward_arp_without_loop(self, msg, destination_ip):
        location = self.host_port.get(destination_ip)
        if location is None:
            self._controlled_access_flood(msg)
            return
        target_datapath = self.datapaths.get(location[0])
        if target_datapath is not None:
            self._send_raw_packet(target_datapath, msg.data, [location[1]])

    def _controlled_access_flood(self, msg):
        """Replicate only to host-facing ports, avoiding loops between switches."""
        inter_switch = {}
        for (source, _destination), source_port in self.port_map.items():
            inter_switch.setdefault(source, set()).add(source_port)
        for dpid, datapath in list(self.datapaths.items()):
            access_ports = self.switch_ports.get(dpid, set()).difference(
                inter_switch.get(dpid, set())
            )
            if dpid == msg.datapath.id:
                access_ports.discard(msg.match["in_port"])
            if access_ports:
                self._send_raw_packet(datapath, msg.data, sorted(access_ports))

    @staticmethod
    def _send_raw_packet(datapath, data, output_ports):
        actions = [datapath.ofproto_parser.OFPActionOutput(port) for port in output_ports]
        datapath.send_msg(datapath.ofproto_parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=datapath.ofproto.OFP_NO_BUFFER,
            in_port=datapath.ofproto.OFPP_CONTROLLER,
            actions=actions,
            data=data,
        ))

    @staticmethod
    def _send_packet_out(msg, out_port):
        datapath = msg.datapath
        actions = [datapath.ofproto_parser.OFPActionOutput(out_port)]
        data = msg.data if msg.buffer_id == datapath.ofproto.OFP_NO_BUFFER else None
        datapath.send_msg(datapath.ofproto_parser.OFPPacketOut(
            datapath=datapath, buffer_id=msg.buffer_id, in_port=msg.match["in_port"],
            actions=actions, data=data,
        ))

    def _record_lldp(self, msg):
        try:
            source, source_port = LLDPPacket.lldp_parse(msg.data)
            # lookup_service_brick() can be None during application __init__,
            # so resolve it lazily. Ryu 4.34 Port cannot be constructed from
            # only (dpid, port_no); find the PortDataState key Ryu created.
            service = self.switches_service or lookup_service_brick("switches")
            self.switches_service = service
            if service is None:
                return
            sent_at = find_lldp_send_timestamp(
                service.ports.items(), source, source_port
            )
            destination = int(msg.datapath.id)
            if (
                sent_at is not None
                and self.monitor.echo_ready(source, destination)
            ):
                self.monitor.record_lldp_delay(
                    int(source), destination, time.time() - sent_at
                )
        except LLDPPacket.LLDPUnknownFormat:
            return

    def _monitor_loop(self):
        while True:
            for datapath in list(self.datapaths.values()):
                parser = datapath.ofproto_parser
                datapath.send_msg(parser.OFPPortStatsRequest(datapath, 0, datapath.ofproto.OFPP_ANY))
                datapath.send_msg(parser.OFPPortDescStatsRequest(datapath, 0))
                datapath.send_msg(parser.OFPEchoRequest(datapath, data=struct.pack("!d", time.time())))
            now = time.time()
            snapshot = self.monitor.snapshot()
            self._log_telemetry_status(now)
            for edge, metric in snapshot.items():
                self.store.record_link_metric(edge, metric)
            if now - self._last_reevaluation >= self.REEVALUATE_INTERVAL:
                self._reevaluate_flows()
                self._last_reevaluation = now
            hub.sleep(self.MONITOR_INTERVAL)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        self.monitor.process_port_stats(ev.msg.datapath.id, ev.msg.body)

    @set_ev_cls(ofp_event.EventOFPPortDescStatsReply, MAIN_DISPATCHER)
    def port_desc_reply_handler(self, ev):
        for port in ev.msg.body:
            # OpenFlow 1.3 curr_speed is in kilobits per second; OVS may report 0.
            if getattr(port, "curr_speed", 0) > 0:
                self.monitor.update_port_capacity(ev.msg.datapath.id, port.port_no, port.curr_speed * 1000.0)

    @set_ev_cls(ofp_event.EventOFPEchoReply, MAIN_DISPATCHER)
    def echo_reply_handler(self, ev):
        try:
            sent_at = struct.unpack("!d", ev.msg.data)[0]
            self.monitor.record_echo_rtt(ev.msg.datapath.id, (time.time() - sent_at) * 1000.0)
        except (struct.error, TypeError):
            return

    def _log_telemetry_status(self, now):
        readiness = self.monitor.readiness(self.net.edges(), now=now)
        signature = (
            readiness.complete,
            readiness.total,
            tuple(sorted(readiness.missing.items())),
        )
        if (
            signature == self._last_telemetry_signature
            and now - self._last_telemetry_log < self.TELEMETRY_LOG_INTERVAL
        ):
            return
        self._last_telemetry_signature = signature
        self._last_telemetry_log = now
        if readiness.ready:
            self.logger.info(
                "TELEMETRY STATUS READY complete=%s/%s; measured QoS/Braess routing enabled",
                readiness.complete, readiness.total,
            )
        else:
            missing = [
                "{}->{}:{}".format(edge[0], edge[1], ",".join(reasons))
                for edge, reasons in sorted(readiness.missing.items())
            ]
            self.logger.warning(
                "TELEMETRY STATUS WARMUP complete=%s/%s missing=[%s] counters=%s",
                readiness.complete, readiness.total, "; ".join(missing),
                dict(self.monitor.diagnostics()),
            )

    @staticmethod
    def _decision_stage(decision):
        return "warmup" if decision.reason.startswith("TELEMETRY WARMUP/FALLBACK") else "measured"

    def _reevaluate_flows(self):
        if not self.net or not self.monitor.snapshot():
            return
        for flow_id, flow in list(self.managed_flows.items()):
            if flow.source_ip not in self.host_port or flow.destination_ip not in self.host_port:
                continue
            try:
                decision = self._make_decision(
                    flow_id, flow.source_ip, flow.destination_ip, flow.destination_port,
                    flow.profile, flow.expected_rate_bps, flow.path, flow.last_change_at,
                )
                stage_changed = (
                    self._flow_decision_stage.get(flow_id) != self._decision_stage(decision)
                )
                if decision.changed or stage_changed:
                    self._record_and_apply_decision(
                        flow_id, flow.source_ip, flow.destination_ip, flow.ip_protocol,
                        flow.source_port, flow.destination_port, flow.profile,
                        flow.expected_rate_bps, decision,
                    )
            except (nx.NetworkXNoPath, KeyError, ValueError) as error:
                self.logger.warning("Could not reevaluate %s: %s", flow_id, error)
