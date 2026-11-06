"""Concurrent TCP load generator. Each request is a new TCP connection to the VIP,
so every request is a fresh load-balancing decision.

CSV columns: ts (unix seconds), client, latency_ms, server, ok (1/0), error
"""
import argparse
import csv
import itertools
import json
import socket
import threading
import time

p = argparse.ArgumentParser()
p.add_argument('--name', default='client')
p.add_argument('--vip', default='10.0.0.100')
p.add_argument('--port', type=int, default=8080)
p.add_argument('--threads', type=int, default=4)
p.add_argument('--duration', type=float, default=30)
p.add_argument('--work', type=float, default=100, help='work_ms per request')
p.add_argument('--size', type=float, default=4, help='response size in KB')
p.add_argument('--think', type=float, default=0.05, help='pause between requests (s)')
p.add_argument('--out', default='client.csv')
args = p.parse_args()

ids = itertools.count()
lock = threading.Lock()


def request():
    """One request on a new connection. Returns (server, error)."""
    with socket.create_connection((args.vip, args.port), timeout=5) as s:
        s.settimeout(15)
        s.sendall((json.dumps({'id': next(ids), 'work_ms': args.work, 'size_kb': args.size}) + '\n').encode())
        line = s.makefile('rb').readline()
    if not line:
        return '', 'closed'
    resp = json.loads(line)
    return resp.get('server', ''), resp.get('error', '')


def worker(writer, end):
    while time.time() < end:
        t0 = time.time()
        try:
            server, err = request()
        except (OSError, ValueError) as e:
            server, err = '', type(e).__name__
        row = [round(t0, 3), args.name, round((time.time() - t0) * 1000, 1), server, int(not err), err]
        with lock:
            writer.writerow(row)
        if err:
            time.sleep(0.2)  # don't spin on a refused connection
        time.sleep(args.think)


def main():
    end = time.time() + args.duration
    with open(args.out, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['ts', 'client', 'latency_ms', 'server', 'ok', 'error'])
        threads = [threading.Thread(target=worker, args=(w, end)) for _ in range(args.threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()


if __name__ == '__main__':
    main()
