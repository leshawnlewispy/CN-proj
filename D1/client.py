"""Concurrent TCP client: --threads threads each send --requests requests to the VIP.

Each request opens a new TCP connection (so the controller makes a fresh decision),
sends {"id": n}, and reads the reply line. Prints how many replies each server sent.
"""
import argparse
import collections
import json
import socket
import threading

p = argparse.ArgumentParser()
p.add_argument('--vip', default='10.0.0.100')
p.add_argument('--port', type=int, default=8080)
p.add_argument('--threads', type=int, default=4)
p.add_argument('--requests', type=int, default=10, help='per thread')
p.add_argument('--name', default='client')
args = p.parse_args()

counts = collections.Counter()
lock = threading.Lock()


def one(req_id):
    try:
        with socket.create_connection((args.vip, args.port), timeout=5) as s:
            s.sendall((json.dumps({'id': req_id}) + '\n').encode())
            reply = json.loads(s.makefile('r').readline())
        return reply.get('server', '?') if 'error' not in reply else 'error'
    except (OSError, ValueError):
        return 'error'


def worker(t):
    for i in range(args.requests):
        who = one(t * 1000 + i)
        with lock:
            counts[who] += 1


threads = [threading.Thread(target=worker, args=(t,)) for t in range(args.threads)]
for th in threads:
    th.start()
for th in threads:
    th.join()
print(f'{args.name}: ' + json.dumps(dict(sorted(counts.items()))), flush=True)
