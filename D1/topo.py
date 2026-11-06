"""Mininet topology + end-to-end demo.

  h1 10.0.0.1 --1--+                                  +--2-- srv1 10.0.0.11
  h2 10.0.0.2 --2--+       link A, 10 Mbit/s          |
  h3 10.0.0.3 --3--+-- s1 --5-------------------1-- s2 --3-- srv2 10.0.0.12
  h4 10.0.0.4 --4--+    \\
                         6-- link B, 10 Mbit/s --1-- s3 --2-- srv3 10.0.0.13

  All three switches are controlled by Ryu (127.0.0.1:6653, OpenFlow 1.3).

  sudo python3 topo.py           start Ryu + network, run the demo, then open the Mininet CLI
  sudo python3 topo.py --no-cli  same, but exit after the demo (prints PASS/FAIL)

Set RYU=/path/to/ryu-manager if ryu-manager is not on PATH.
"""
import json
import os
import subprocess
import sys
import time

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENTS = [('h1', '10.0.0.1'), ('h2', '10.0.0.2'), ('h3', '10.0.0.3')]   # h4 is idle in D1
SERVERS = [('srv1', '10.0.0.11'), ('srv2', '10.0.0.12'), ('srv3', '10.0.0.13')]
SLOW = 'srv1'   # this server takes 6x longer per request, so it reports more active connections
LINK_MBPS = 10


def build():
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink, build=False)
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)
    s1, s2, s3 = (net.addSwitch(f's{i}', dpid=f'{i:016x}', protocols='OpenFlow13')
                  for i in (1, 2, 3))
    for i in range(1, 5):                                            # h1..h4 on s1 ports 1..4
        net.addLink(net.addHost(f'h{i}', ip=f'10.0.0.{i}/24', mac=f'00:00:00:00:00:0{i}'),
                    s1, port2=i)
    net.addLink(s1, s2, port1=5, port2=1, bw=LINK_MBPS)              # link A
    net.addLink(s1, s3, port1=6, port2=1, bw=LINK_MBPS)              # link B
    for name, sw, port in (('srv1', s2, 2), ('srv2', s2, 3), ('srv3', s3, 2)):
        n = name[-1]                                                 # MACs match controller.py
        net.addLink(net.addHost(name, ip=f'10.0.0.1{n}/24', mac=f'00:00:00:00:00:1{n}'),
                    sw, port2=port)
    net.build()
    return net


def demo(net):
    log = open(os.path.join(HERE, 'servers.log'), 'w')
    for name, _ in SERVERS:
        work = '0.3' if name == SLOW else '0.05'
        net.get(name).popen([sys.executable, os.path.join(HERE, 'server.py'), '--name', name,
                             '--work', work], stdout=log, stderr=log)
    time.sleep(2)                                   # servers up, first load reports sent
    print('\n*** connectivity: h1 -> srv2 by real IP')
    print(net.get('h1').cmd('ping -c1 -W1 10.0.0.12 | tail -2'))
    print('*** 3 clients x 4 threads x 10 requests to the VIP 10.0.0.100:8080')
    procs = [net.get(h).popen([sys.executable, os.path.join(HERE, 'client.py'), '--name', h],
                              stdout=subprocess.PIPE, text=True) for h, _ in CLIENTS]
    outs = [p.communicate()[0].strip() for p in procs]
    print('\n'.join(outs))
    total = {}
    for line in outs:
        for k, v in json.loads(line.split(': ', 1)[1]).items():   # "h1: {...}" from client.py
            total[k] = total.get(k, 0) + v
    print('*** total:', total)
    ok = total.get('error', 0) == 0 and all(total.get(s, 0) > 0 for s, _ in SERVERS)
    print(f'*** {SLOW} is slow, so it should get the fewest requests:',
          'yes' if total.get(SLOW, 0) == min(total.get(s, 0) for s, _ in SERVERS) else 'no')
    print('*** RESULT:', 'PASS' if ok else 'FAIL (see controller.log and servers.log)')
    return ok


def main():
    setLogLevel('warning')
    subprocess.run(['mn', '-c'], capture_output=True)
    ryu = os.environ.get('RYU', 'ryu-manager')
    clog = open(os.path.join(HERE, 'controller.log'), 'w')
    ctrl = subprocess.Popen([ryu, os.path.join(HERE, 'controller.py')], stdout=clog, stderr=clog)
    time.sleep(3)
    net = build()
    ok = False
    try:
        net.start()
        time.sleep(2)                               # switch connects to Ryu
        ok = demo(net)
        if '--no-cli' not in sys.argv:
            CLI(net)
    finally:
        net.stop()
        ctrl.terminate()
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
