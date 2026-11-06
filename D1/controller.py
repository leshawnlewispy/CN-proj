"""Ryu (OpenFlow 1.3) load balancer.

Run: ryu-manager controller.py

Topology (topo.py): clients on s1; s1 port 5 = link A to s2 (srv1, srv2), port 6 = link B to s3 (srv3).

* Clients connect to the virtual IP 10.0.0.100:8080. The controller answers ARP for it.
* Servers send UDP load reports to 10.0.0.254:9999; a priority-100 rule on every switch
  sends them here.
* On a new TCP connection (SYN to the VIP arriving at s1) it picks the server with the fewest
  connections (reported active + connections sent there since that report) and installs two
  NAT rules on s1: client->VIP becomes client->server (out the link toward that server), and
  server->client is rewritten to come from the VIP.
* s2, s3 and all other traffic: plain learning switch.
"""
import json
import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib.packet import arp, ethernet, ether_types, ipv4, packet, tcp, udp
from ryu.ofproto import ofproto_v1_3

VIP, VIP_MAC, VIP_PORT = '10.0.0.100', '00:00:00:00:01:00', 8080
REPORT_IP, REPORT_MAC, REPORT_PORT = '10.0.0.254', '00:00:00:00:02:54', 9999
S1 = 1             # dpid of the edge switch the clients attach to
IDLE = 10          # seconds an idle NAT rule lives
# name: (ip, mac, s1 port toward it) -- must match topo.py (5 = link A, 6 = link B)
SERVERS = {
    'srv1': ('10.0.0.11', '00:00:00:00:00:11', 5),
    'srv2': ('10.0.0.12', '00:00:00:00:00:12', 5),
    'srv3': ('10.0.0.13', '00:00:00:00:00:13', 6),
}


class LoadBalancer(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.load = {s: 0 for s in SERVERS}      # last reported active connections
        self.sent = {s: 0 for s in SERVERS}      # connections sent since that report
        self.mac_to_port = {}                    # dpid -> {mac: port}
        self.conns = {}                          # (client ip, client port) -> (server, time)

    # ---- helpers -------------------------------------------------------------
    @staticmethod
    def add_flow(dp, prio, match, actions, idle=0):
        p = dp.ofproto_parser
        inst = [p.OFPInstructionActions(dp.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(p.OFPFlowMod(datapath=dp, priority=prio, match=match, instructions=inst,
                                 idle_timeout=idle))

    @staticmethod
    def packet_out(dp, msg, in_port, actions):
        p = dp.ofproto_parser
        data = msg.data if msg.buffer_id == dp.ofproto.OFP_NO_BUFFER else None
        dp.send_msg(p.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id, in_port=in_port,
                                   actions=actions, data=data))

    def pick(self):
        name = min(SERVERS, key=lambda s: (self.load[s] + self.sent[s], s))
        self.sent[name] += 1
        return name

    # ---- switch connects -------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def features(self, ev):
        dp = ev.msg.datapath
        p, o = dp.ofproto_parser, dp.ofproto
        to_ctrl = [p.OFPActionOutput(o.OFPP_CONTROLLER, o.OFPCML_NO_BUFFER)]
        self.add_flow(dp, 0, p.OFPMatch(), to_ctrl)                       # table miss
        self.add_flow(dp, 100, p.OFPMatch(eth_type=0x0800, ip_proto=17, ipv4_dst=REPORT_IP,
                                          udp_dst=REPORT_PORT), to_ctrl)  # load reports

    # ---- every packet the switch sends us --------------------------------------
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in(self, ev):
        msg = ev.msg
        dp, in_port = msg.datapath, msg.match['in_port']
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        a = pkt.get_protocol(arp.arp)
        if a and a.opcode == arp.ARP_REQUEST and a.dst_ip in (VIP, REPORT_IP):
            return self.arp_reply(dp, in_port, eth, a)
        ip, u, t = pkt.get_protocol(ipv4.ipv4), pkt.get_protocol(udp.udp), pkt.get_protocol(tcp.tcp)
        if ip and u and ip.dst == REPORT_IP and u.dst_port == REPORT_PORT:
            return self.on_report(pkt.protocols[-1])
        if ip and ip.dst == VIP:
            if dp.id == S1 and t and t.dst_port == VIP_PORT:
                self.new_connection(dp, msg, in_port, ip, t)
            return                       # anything else to the VIP: drop
        self.learning_switch(dp, msg, in_port, eth)

    def arp_reply(self, dp, in_port, eth, a):
        mac = VIP_MAC if a.dst_ip == VIP else REPORT_MAC
        r = packet.Packet()
        r.add_protocol(ethernet.ethernet(dst=eth.src, src=mac, ethertype=ether_types.ETH_TYPE_ARP))
        r.add_protocol(arp.arp(opcode=arp.ARP_REPLY, src_mac=mac, src_ip=a.dst_ip,
                               dst_mac=a.src_mac, dst_ip=a.src_ip))
        r.serialize()
        p = dp.ofproto_parser
        dp.send_msg(p.OFPPacketOut(datapath=dp, buffer_id=dp.ofproto.OFP_NO_BUFFER,
                                   in_port=dp.ofproto.OFPP_CONTROLLER,
                                   actions=[p.OFPActionOutput(in_port)], data=r.data))

    def on_report(self, payload):
        try:
            r = json.loads(bytes(payload))
            name = r['srv']
            if name in SERVERS:
                self.load[name], self.sent[name] = int(r['active']), 0
        except (ValueError, KeyError, TypeError):
            self.logger.warning('bad load report ignored')

    def new_connection(self, dp, msg, in_port, ip, t):
        # A retransmitted SYN can arrive before the rule is active: keep it on the same server.
        key, now = (ip.src, t.src_port), time.time()
        known = self.conns.get(key)
        if known and now - known[1] < IDLE:
            name = known[0]
        elif t.bits & tcp.TCP_SYN and not t.bits & tcp.TCP_ACK:
            self.conns = {k: v for k, v in self.conns.items() if now - v[1] < IDLE}  # forget old
            name = self.pick()
            self.logger.info('%s:%s -> %s  (load %s)', ip.src, t.src_port, name,
                             {s: self.load[s] + self.sent[s] for s in SERVERS})
        else:
            return                       # stray packet of an expired connection: drop
        self.conns[key] = (name, now)
        s_ip, s_mac, s_port = SERVERS[name]
        p = dp.ofproto_parser
        fwd = [p.OFPActionSetField(eth_dst=s_mac), p.OFPActionSetField(ipv4_dst=s_ip),
               p.OFPActionOutput(s_port)]
        self.add_flow(dp, 10, p.OFPMatch(in_port=in_port, eth_type=0x0800, ip_proto=6,
                                         ipv4_src=ip.src, ipv4_dst=VIP, tcp_src=t.src_port,
                                         tcp_dst=VIP_PORT), fwd, idle=IDLE)
        rev = [p.OFPActionSetField(eth_src=VIP_MAC), p.OFPActionSetField(ipv4_src=VIP),
               p.OFPActionOutput(in_port)]
        self.add_flow(dp, 10, p.OFPMatch(in_port=s_port, eth_type=0x0800, ip_proto=6,
                                         ipv4_src=s_ip, ipv4_dst=ip.src, tcp_src=VIP_PORT,
                                         tcp_dst=t.src_port), rev, idle=IDLE)
        self.packet_out(dp, msg, in_port, fwd)

    def learning_switch(self, dp, msg, in_port, eth):
        p, o = dp.ofproto_parser, dp.ofproto
        table = self.mac_to_port.setdefault(dp.id, {})
        table[eth.src] = in_port
        out = table.get(eth.dst, o.OFPP_FLOOD)
        actions = [p.OFPActionOutput(out)]
        if out != o.OFPP_FLOOD:
            self.add_flow(dp, 1, p.OFPMatch(in_port=in_port, eth_dst=eth.dst), actions, idle=30)
        self.packet_out(dp, msg, in_port, actions)
