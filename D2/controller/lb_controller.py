"""Ryu app: adaptive (or static round-robin) TCP load balancer behind a virtual IP.

Run:  LB_MODE=adaptive LB_LOG=results/controller.csv ryu-manager controller/lb_controller.py

What it does
  * Answers ARP for the VIP 10.0.0.100 (and the report address 10.0.0.254).
  * Receives server load reports (UDP to 10.0.0.254:9999) as PacketIns.
  * Polls s1 port stats every second -> bytes/s on link A (port 5) and link B (port 6).
  * On a new TCP connection to VIP:8080 at s1, picks a server and installs two NAT flows:
      forward: client -> VIP  rewritten to  client -> server, out toward the server
      reverse: server -> client rewritten to VIP -> client
    Flows carry cookie = server index, so all flows of a dead server can be deleted at once.
  * Everything else (ARP between hosts, iperf, direct traffic) is a plain learning switch.
"""
import csv
import json
import os
import sys
import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.lib.packet import arp, ethernet, ether_types, ipv4, packet, tcp, udp
from ryu.ofproto import ofproto_v1_3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from balancer import Balancer, Server  # noqa: E402

VIP, VIP_MAC, APP_PORT = '10.0.0.100', '00:00:00:00:01:00', 8080
REPORT_IP, REPORT_MAC, REPORT_PORT = '10.0.0.254', '00:00:00:00:02:54', 9999
S1 = 1                        # dpid of the edge switch (clients attach here)
LINK_PORT = {'A': 5, 'B': 6}  # s1 port toward s2 (link A) and s3 (link B)
FLOW_IDLE = 10                # seconds an idle NAT flow lives
SERVERS = [
    Server('srv1', '10.0.0.11', '00:00:00:00:00:11', 'A'),
    Server('srv2', '10.0.0.12', '00:00:00:00:00:12', 'A'),
    Server('srv3', '10.0.0.13', '00:00:00:00:00:13', 'B'),
]
COOKIE = {s.name: i + 1 for i, s in enumerate(SERVERS)}


class LoadBalancer(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.mode = os.environ.get('LB_MODE', 'adaptive')
        self.lb = Balancer(SERVERS, self.mode)
        self.dps = {}
        self.mac_to_port = {}
        self.port_bytes = {}   # port -> (bytes, time)
        self.conns = {}        # (client ip, client port) -> (server, last seen)
        log_path = os.environ.get('LB_LOG', 'controller.csv')
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        self.log_file = open(log_path, 'w', newline='')
        self.log = csv.writer(self.log_file)
        self.log.writerow(['ts', 'event', 'server', 'state', 'active', 'cap', 'cpu', 'score',
                           'linkA_mbps', 'linkB_mbps'])
        self.logger.info('load balancer mode=%s VIP=%s', self.mode, VIP)
        hub.spawn(self._monitor)

    # ---------- helpers ----------
    def _write(self, event, s=None):
        a, b = (round(self.lb.link_bps.get(k, 0) / 1e6, 2) for k in ('A', 'B'))
        row = [round(time.time(), 3), event]
        row += ([s.name, s.state, s.active, s.cap, s.cpu, round(self.lb.score(s), 3)] if s
                else [''] * 6)
        self.log.writerow(row + [a, b])
        self.log_file.flush()

    @staticmethod
    def _add_flow(dp, prio, match, actions, idle=0, cookie=0):
        p = dp.ofproto_parser
        inst = [p.OFPInstructionActions(dp.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(p.OFPFlowMod(datapath=dp, priority=prio, match=match, instructions=inst,
                                 idle_timeout=idle, cookie=cookie))

    @staticmethod
    def _packet_out(dp, msg, in_port, actions):
        data = msg.data if msg.buffer_id == dp.ofproto.OFP_NO_BUFFER else None
        dp.send_msg(dp.ofproto_parser.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                                   in_port=in_port, actions=actions, data=data))

    # ---------- switch setup ----------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def features(self, ev):
        dp = ev.msg.datapath
        ofp, p = dp.ofproto, dp.ofproto_parser
        self.dps[dp.id] = dp
        to_ctrl = [p.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self._add_flow(dp, 0, p.OFPMatch(), to_ctrl)  # table miss
        # load reports go straight to the controller from whichever switch sees them
        self._add_flow(dp, 100, p.OFPMatch(eth_type=0x0800, ip_proto=17, ipv4_dst=REPORT_IP,
                                           udp_dst=REPORT_PORT), to_ctrl)

    # ---------- packet in ----------
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in(self, ev):
        msg = ev.msg
        dp, in_port = msg.datapath, msg.match['in_port']
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        self.mac_to_port.setdefault(dp.id, {})[eth.src] = in_port

        a = pkt.get_protocol(arp.arp)
        if a and a.opcode == arp.ARP_REQUEST and a.dst_ip in (VIP, REPORT_IP):
            return self._arp_reply(dp, in_port, eth, a)

        ip = pkt.get_protocol(ipv4.ipv4)
        u = pkt.get_protocol(udp.udp)
        if ip and u and ip.dst == REPORT_IP and u.dst_port == REPORT_PORT:
            return self._on_report(pkt.protocols[-1])

        t = pkt.get_protocol(tcp.tcp)
        if dp.id == S1 and ip and t and ip.dst == VIP and t.dst_port == APP_PORT:
            return self._new_flow(dp, msg, in_port, ip, t)
        if ip and ip.dst == VIP:
            return  # stray VIP packet somewhere else: drop

        self._learning_switch(dp, msg, in_port, eth)

    def _arp_reply(self, dp, in_port, eth, a):
        mac = VIP_MAC if a.dst_ip == VIP else REPORT_MAC
        pkt = packet.Packet()
        pkt.add_protocol(ethernet.ethernet(ethertype=ether_types.ETH_TYPE_ARP, dst=eth.src, src=mac))
        pkt.add_protocol(arp.arp(opcode=arp.ARP_REPLY, src_mac=mac, src_ip=a.dst_ip,
                                 dst_mac=a.src_mac, dst_ip=a.src_ip))
        pkt.serialize()
        p = dp.ofproto_parser
        dp.send_msg(p.OFPPacketOut(datapath=dp, buffer_id=dp.ofproto.OFP_NO_BUFFER,
                                   in_port=dp.ofproto.OFPP_CONTROLLER,
                                   actions=[p.OFPActionOutput(in_port)], data=pkt.data))

    def _on_report(self, payload):
        try:
            r = json.loads(bytes(payload))
            changed = self.lb.report(r['srv'], int(r['active']), int(r['cap']), float(r['cpu']),
                                     int(r.get('done', 0)))
        except (ValueError, KeyError, TypeError):
            self.logger.warning('bad load report: %r', payload)
            return
        if changed:
            s = self.lb.servers[r['srv']]
            self.logger.info('%s -> %s (active=%d/%d)', s.name, changed, s.active, s.cap)
            self._write(changed, s)

    def _new_flow(self, dp, msg, in_port, ip, t):
        p = dp.ofproto_parser
        # Packets of one connection can reach us before its flow is active in the switch:
        # they must go to the same server, so only a fresh SYN gets a new decision.
        key, now = (ip.src, t.src_port), time.time()
        known = self.conns.get(key)
        if known and now - known[1] < FLOW_IDLE:
            s = known[0]
        elif t.has_flags(tcp.TCP_SYN) and not t.has_flags(tcp.TCP_ACK):
            s = self.lb.pick()
            self._write('assign', s)
        else:
            return  # stray packet of an expired connection: drop, client resets
        self.conns[key] = (s, now)
        fwd_actions = [p.OFPActionSetField(eth_dst=s.mac), p.OFPActionSetField(ipv4_dst=s.ip),
                       p.OFPActionOutput(LINK_PORT[s.link])]
        rev_actions = [p.OFPActionSetField(eth_src=VIP_MAC), p.OFPActionSetField(ipv4_src=VIP),
                       p.OFPActionOutput(in_port)]
        common = dict(eth_type=0x0800, ip_proto=6)
        self._add_flow(dp, 10, p.OFPMatch(ipv4_src=s.ip, ipv4_dst=ip.src, tcp_src=APP_PORT,
                                          tcp_dst=t.src_port, **common),
                       rev_actions, FLOW_IDLE, COOKIE[s.name])
        self._add_flow(dp, 10, p.OFPMatch(in_port=in_port, ipv4_src=ip.src, ipv4_dst=VIP,
                                          tcp_src=t.src_port, tcp_dst=APP_PORT, **common),
                       fwd_actions, FLOW_IDLE, COOKIE[s.name])
        self._packet_out(dp, msg, in_port, fwd_actions)

    def _learning_switch(self, dp, msg, in_port, eth):
        ofp, p = dp.ofproto, dp.ofproto_parser
        out = self.mac_to_port[dp.id].get(eth.dst, ofp.OFPP_FLOOD)
        actions = [p.OFPActionOutput(out)]
        if out != ofp.OFPP_FLOOD:
            self._add_flow(dp, 1, p.OFPMatch(in_port=in_port, eth_dst=eth.dst), actions, idle=30)
        self._packet_out(dp, msg, in_port, actions)

    # ---------- monitoring ----------
    def _monitor(self):
        while True:
            hub.sleep(1)
            dp = self.dps.get(S1)
            if dp:
                dp.send_msg(dp.ofproto_parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY))
            for s in self.lb.check_dead():
                self.logger.info('%s -> down (no report for 3 s)', s.name)
                self._write('down', s)
                if self.mode == 'adaptive' and dp:
                    self._delete_flows(dp, COOKIE[s.name])
                    self.conns = {k: v for k, v in self.conns.items() if v[0] is not s}
            now = time.time()
            self.conns = {k: v for k, v in self.conns.items() if now - v[1] < FLOW_IDLE}
            for s in self.lb.servers.values():
                self._write('state', s)

    def _delete_flows(self, dp, cookie):
        ofp, p = dp.ofproto, dp.ofproto_parser
        dp.send_msg(p.OFPFlowMod(datapath=dp, cookie=cookie, cookie_mask=0xFFFFFFFFFFFFFFFF,
                                 table_id=ofp.OFPTT_ALL, command=ofp.OFPFC_DELETE,
                                 out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY))

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats(self, ev):
        now = time.time()
        for st in ev.msg.body:
            for link, port in LINK_PORT.items():
                if st.port_no != port:
                    continue
                total = st.rx_bytes + st.tx_bytes
                prev = self.port_bytes.get(port)
                if prev and now > prev[1]:
                    self.lb.set_link_rate(link, (total - prev[0]) * 8 / (now - prev[1]))
                self.port_bytes[port] = (total, now)
