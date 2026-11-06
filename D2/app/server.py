"""Threaded TCP application server + load agent.

Protocol (one request per connection, newline-delimited JSON):
  client -> server  {"id": 7, "work_ms": 50, "size_kb": 4}
  server -> client  {"id": 7, "server": "srv1", "queue_ms": 0.4, "payload": "xxxx..."}
  server -> client  {"id": 7, "error": "bad request"}              (on bad input)

Load agent: every --interval seconds (0.25) sends a UDP datagram to --report (default 10.0.0.254:9999).
No host owns 10.0.0.254; the switches send it to the Ryu controller as a PacketIn.
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
p.add_argument('--workers', type=int, default=8, help='requests processed in parallel (capacity)')
p.add_argument('--slow', type=float, default=1.0, help='work time multiplier (2.0 = half speed)')
p.add_argument('--report', default='10.0.0.254:9999', help="host:port for load reports, or 'none'")
p.add_argument('--interval', type=float, default=0.25,
               help='seconds between load reports; must be short compared to how fast flows arrive')
args = p.parse_args()

workers = threading.BoundedSemaphore(args.workers)
lock = threading.Lock()
active = 0      # connections currently open (being served or waiting for a worker)
done = 0        # requests completed since the last report


def do_work(seconds):
    """Simulated request cost: 20% CPU burn, 80% waiting (like I/O)."""
    end = time.time() + seconds * 0.2
    while time.time() < end:
        pass
    time.sleep(seconds * 0.8)


def handle(conn):
    global active, done
    with lock:
        active += 1
    req_id = None
    try:
        conn.settimeout(10)
        line = conn.makefile('rb').readline(65536)
        req = json.loads(line)
        req_id = req['id']
        t0 = time.time()
        with workers:
            queue_ms = (time.time() - t0) * 1000
            do_work(float(req.get('work_ms', 50)) * args.slow / 1000)
        payload = 'x' * int(float(req.get('size_kb', 1)) * 1024)
        resp = {'id': req_id, 'server': args.name, 'queue_ms': round(queue_ms, 1), 'payload': payload}
        conn.sendall((json.dumps(resp) + '\n').encode())
        with lock:
            done += 1
    except (ValueError, KeyError, TypeError):
        try:
            conn.sendall((json.dumps({'id': req_id, 'error': 'bad request'}) + '\n').encode())
        except OSError:
            pass
    except OSError:
        pass  # client went away / timed out
    finally:
        conn.close()
        with lock:
            active -= 1


def load_agent():
    global done
    host, port = args.report.split(':')
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    last_cpu, last_t = time.process_time(), time.time()
    while True:
        time.sleep(args.interval)
        cpu_now, t_now = time.process_time(), time.time()
        cpu = (cpu_now - last_cpu) / (t_now - last_t)
        last_cpu, last_t = cpu_now, t_now
        with lock:
            msg = {'srv': args.name, 'active': active, 'cap': args.workers,
                   'cpu': round(min(cpu, 1.0), 3), 'done': done, 'ts': t_now}
            done = 0
        try:
            sock.sendto(json.dumps(msg).encode(), (host, int(port)))
        except OSError:
            pass  # no route yet; try again next second


def main():
    if args.report != 'none':
        threading.Thread(target=load_agent, daemon=True).start()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(128)
    print(f'{args.name} listening on {args.host}:{args.port} workers={args.workers} slow={args.slow}',
          flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == '__main__':
    main()
