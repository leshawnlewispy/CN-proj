"""Mininet topology (OpenFlow 1.3, remote Ryu controller on 127.0.0.1:6653).

  h1 h2 h3 h4 --- s1 --(port5, link A, 10 Mbit/s)-- s2 --- srv1 (10.0.0.11), srv2 (10.0.0.12)
                     \\-(port6, link B, 10 Mbit/s)-- s3 --- srv3 (10.0.0.13)

Run the CLI:  sudo python3 topo/topo.py        (add --user-dp if OVS kernel module is missing)
"""
import argparse
from functools import partial

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController

LINK_MBPS = 10  # keep in sync with LINK_BPS in controller/balancer.py


def build(user_dp=False):
    switch = partial(OVSSwitch, protocols='OpenFlow13', datapath='user' if user_dp else 'kernel')
    net = Mininet(switch=switch, link=TCLink, controller=None)
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)
    s1, s2, s3 = (net.addSwitch(f's{i}', dpid=f'{i:016x}') for i in (1, 2, 3))
    for i in range(1, 5):
        h = net.addHost(f'h{i}', ip=f'10.0.0.{i}/24', mac=f'00:00:00:00:00:0{i}')
        net.addLink(h, s1, port2=i)
    net.addLink(s1, s2, port1=5, port2=1, bw=LINK_MBPS)   # link A
    net.addLink(s1, s3, port1=6, port2=1, bw=LINK_MBPS)   # link B
    for n, sw, port in (('srv1', s2, 2), ('srv2', s2, 3), ('srv3', s3, 2)):
        h = net.addHost(n, ip=f'10.0.0.1{n[-1]}/24', mac=f'00:00:00:00:00:1{n[-1]}')
        net.addLink(h, sw, port2=port)
    if user_dp:  # userspace OVS drops packets with offloaded (unfilled) checksums
        for h in net.hosts:
            h.cmd(f'ethtool -K {h.defaultIntf()} tx off rx off')
    return net


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--user-dp', action='store_true', help='userspace OVS datapath')
    setLogLevel('info')
    net = build(ap.parse_args().user_dp)
    net.start()
    CLI(net)
    net.stop()
