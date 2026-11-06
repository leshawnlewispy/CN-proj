"""TCP application server + UDP load reporter.

TCP: accepts connections on --port, one thread per connection. Each request is one JSON line,
     e.g. {"id": 7}; the reply is one JSON line {"id": 7, "server": "srv1"}.
UDP: every --interval seconds sends {"srv": name, "active": n} to --report (10.0.0.254:9999).
     No host owns 10.0.0.254; the switch hands these datagrams to the Ryu controller.
"""
import argparse
import json
import socket
import threading
import time

p = argparse.ArgumentParser()
p.add_argument('--name', required=True)
p.add_argument('--host', default='0.0.0.0')
p.add_argument('--port', type=int, default=8080)
p.add_argument('--work', type=float, default=0.05, help='seconds of simulated work per request')
p.add_argument('--report', default='10.0.0.254:9999')
p.add_argument('--interval', type=float, default=0.5)
args = p.parse_args()

active = 0                 # connections being served right now
lock = threading.Lock()


def handle(conn):
    global active
    with lock:
        active += 1
    try:
        conn.settimeout(5)
        line = conn.makefile('r').readline()
        if not line:
            return
        req = json.loads(line)
        time.sleep(args.work)
        conn.sendall((json.dumps({'id': req.get('id'), 'server': args.name}) + '\n').encode())
    except (OSError, ValueError) as e:      # timeout, reset, or bad JSON: tell the client if we can
        try:
            conn.sendall((json.dumps({'error': str(e), 'server': args.name}) + '\n').encode())
        except OSError:
            pass
    finally:
        conn.close()
        with lock:
            active -= 1


def reporter():
    host, port = args.report.split(':')
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    while True:
        with lock:
            msg = {'srv': args.name, 'active': active}
        try:
            sock.sendto(json.dumps(msg).encode(), (host, int(port)))
        except OSError:
            pass                            # network not ready yet; next report will retry
        time.sleep(args.interval)


def main():
    threading.Thread(target=reporter, daemon=True).start()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(64)
    print(f'{args.name} listening on {args.host}:{args.port}', flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == '__main__':
    main()
