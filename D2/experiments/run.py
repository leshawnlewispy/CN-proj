"""Run one experiment end to end: start Ryu, build Mininet, start servers + clients,
inject the scenario event, collect CSVs.

  sudo python3 experiments/run.py --scenario E4 --mode adaptive [--threads 4] [--duration 30]

Scenarios (clients h1-h3 always generate the load; h4 is the "disturbance"):
  E1 balanced          3 equal servers
  E2 unequal servers   srv3 is a smaller machine: 2 workers instead of 8, 2x slower
  E3 hot server        at t=D/3, h4 hammers srv1 directly (bypassing the LB) with slow requests
  E4 server failure    at t=D/3, srv2 process is killed
  E5 link congestion   at t=D/3, h4 floods link A with 9 Mbit/s UDP iperf toward srv1
  E6 scale-up          E1 with --threads 1/2/4/8 (run_all.sh loops it)
Output: results/<scenario>_<mode>_t<threads>/{h1,h2,h3}.csv, controller.csv, meta.json
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'topo'))
from topo import build  # noqa: E402

from mininet.log import setLogLevel  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--scenario', default='E1', choices=['E1', 'E2', 'E3', 'E4', 'E5', 'E6'])
ap.add_argument('--mode', default='adaptive', choices=['static', 'adaptive'])
ap.add_argument('--threads', type=int, default=4, help='threads per client host')
ap.add_argument('--duration', type=float, default=30)
ap.add_argument('--out', default=os.path.join(ROOT, 'results'))
ap.add_argument('--user-dp', action='store_true', help='userspace OVS datapath (no kernel module)')
args = ap.parse_args()

PY = sys.executable
APP = os.path.join(ROOT, 'app')
out = os.path.join(args.out, f'{args.scenario}_{args.mode}_t{args.threads}')
os.makedirs(out, exist_ok=True)

setLogLevel('warning')
ryu = subprocess.Popen([os.environ.get('RYU', 'ryu-manager'), os.path.join(ROOT, 'controller', 'lb_controller.py')],
                       env={**os.environ, 'LB_MODE': args.mode, 'LB_LOG': os.path.join(out, 'controller.csv')},
                       stdout=open(os.path.join(out, 'ryu.log'), 'w'), stderr=subprocess.STDOUT)
time.sleep(3)
net = build(args.user_dp)
procs, servers = [], {}
try:
    net.start()
    net.waitConnected(timeout=15)
    for n in ('srv1', 'srv2', 'srv3'):
        weak = args.scenario == 'E2' and n == 'srv3'   # smaller machine: 2 workers, 2x slower
        servers[n] = net[n].popen([PY, os.path.join(APP, 'server.py'), '--name', n,
                                   '--workers', '2' if weak else '8', '--slow', '2.0' if weak else '1.0'])
    if args.scenario == 'E5':
        procs.append(net['srv1'].popen(['iperf', '-s', '-u']))
    time.sleep(4)  # first load reports reach the controller; servers become healthy

    clients = [net[h].popen([PY, os.path.join(APP, 'client.py'), '--name', h, '--threads', str(args.threads),
                             '--duration', str(args.duration), '--out', os.path.join(out, f'{h}.csv')])
               for h in ('h1', 'h2', 'h3')]
    time.sleep(args.duration / 3)
    event_ts = time.time()
    rest = str(int(args.duration * 2 / 3))
    if args.scenario == 'E3':
        procs.append(net['h4'].popen([PY, os.path.join(APP, 'client.py'), '--name', 'h4', '--vip', '10.0.0.11',
                                      '--threads', '12', '--work', '500', '--duration', rest,
                                      '--out', os.path.join(out, 'h4_direct.csv')]))
    elif args.scenario == 'E4':
        servers['srv2'].kill()
    elif args.scenario == 'E5':
        procs.append(net['h4'].popen(['iperf', '-c', '10.0.0.11', '-u', '-b', '9M', '-t', rest]))
    else:
        event_ts = None
    for c in clients:
        c.wait()
    json.dump({'scenario': args.scenario, 'mode': args.mode, 'threads': args.threads,
               'duration': args.duration, 'event_ts': event_ts}, open(os.path.join(out, 'meta.json'), 'w'))
    print('done ->', out)
finally:
    for p in procs + list(servers.values()):
        p.kill()
    net.stop()
    ryu.terminate()
    ryu.wait()
