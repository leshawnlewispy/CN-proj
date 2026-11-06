"""Local dashboard: run experiments from the browser and see the results from THIS machine.

  sudo -E python3 dashboard/server.py            then open http://localhost:8000
  (add --user-dp if Open vSwitch has no kernel module, e.g. in a container)

Listens on 127.0.0.1 only. Runs experiments/run.py (needs root for Mininet) and serves
results/ as JSON: GET /api/results, GET /api/status, POST /api/run, POST /api/tests.
"""
import csv
import glob
import json
import os
import platform
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, 'results')
HERE = os.path.dirname(os.path.abspath(__file__))
USER_DP = '--user-dp' in sys.argv
DURATIONS = {15, 20, 30, 60}
JOB = {'running': False, 'scenario': '', 'label': '', 'step': 0, 'steps': 0, 'started': 0.0,
       'finished': 0.0, 'log': [], 'error': ''}
LOCK = threading.Lock()


def p95(xs):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(0.95 * len(xs)))], 1)


def load_run(d):
    meta = json.load(open(os.path.join(d, 'meta.json')))
    rows = []
    for f in sorted(glob.glob(os.path.join(d, 'h[1-3].csv'))):
        with open(f, newline='') as fh:
            rows += list(csv.DictReader(fh))
    for r in rows:
        r['ts'], r['latency_ms'], r['ok'] = float(r['ts']), float(r['latency_ms']), r['ok'] == '1'
    ok = [r for r in rows if r['ok']]
    errs = [r for r in rows if not r['ok']]
    ev = meta.get('event_ts')
    t0 = min((r['ts'] for r in rows), default=0.0)
    n = int(meta['duration']) + 2
    tl = {k: [0] * n for k in ('srv1', 'srv2', 'srv3', 'err')}
    for r in rows:
        i = int(r['ts'] - t0)
        key = r['server'] if r['ok'] else 'err'
        if 0 <= i < n and key in tl:
            tl[key][i] += 1
    lat = [r['latency_ms'] for r in ok]
    after = [r['latency_ms'] for r in ok if not ev or r['ts'] >= ev]
    e_after = [r for r in errs if ev and r['ts'] >= ev]
    s = {
        'requests': len(rows),
        'error_pct': round(100 * len(errs) / max(len(rows), 1), 1),
        'throughput': round(len(ok) / meta['duration'], 1),
        'mean_ms': round(sum(lat) / len(lat), 1) if lat else None,
        'p95_ms': p95(lat),
        'p95_after_ms': p95(after),
        'share': {k: round(sum(1 for r in ok if r['server'] == k) / max(len(ok), 1), 3)
                  for k in ('srv1', 'srv2', 'srv3')},
        'errors_after_event': len(e_after),
        'error_window_s': round(max(r['ts'] for r in e_after) - ev, 1) if e_after else 0.0,
    }
    return {'scenario': meta['scenario'], 'mode': meta['mode'], 'threads': meta['threads'],
            'duration': meta['duration'], 'finished': os.path.getmtime(os.path.join(d, 'meta.json')),
            'event_offset': round(ev - t0, 1) if ev else None, 'summary': s, 'timeline': tl}


def all_results():
    runs = []
    for d in sorted(glob.glob(os.path.join(RES, 'E*_*_t*'))):
        if os.path.exists(os.path.join(d, 'meta.json')):
            try:
                runs.append(load_run(d))
            except (OSError, ValueError, KeyError) as e:
                print('skipping', d, e, flush=True)
    return {'machine': {'host': platform.node(), 'system': platform.platform(),
                        'python': platform.python_version()}, 'runs': runs}


def log(line):
    with LOCK:
        JOB['log'] = (JOB['log'] + [line.rstrip()])[-40:]


def run_job(scenario, duration, threads_list):
    steps = [(m, t) for t in threads_list for m in ('static', 'adaptive')]
    with LOCK:
        JOB.update(running=True, scenario=scenario, step=0, steps=len(steps), started=time.time(),
                   finished=0.0, log=[], error='')
    try:
        for i, (mode, t) in enumerate(steps, 1):
            with LOCK:
                JOB.update(step=i, label=f'{scenario} · {mode} · {t} threads/host')
            cmd = [sys.executable, os.path.join(ROOT, 'experiments', 'run.py'), '--scenario', scenario,
                   '--mode', mode, '--duration', str(duration), '--threads', str(t)]
            if USER_DP:
                cmd.append('--user-dp')
            log('$ ' + ' '.join(cmd[1:]))
            p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in p.stdout:
                log(line)
            if p.wait() != 0:
                raise RuntimeError(f'{mode} run exited with code {p.returncode}; see the log')
            subprocess.run(['mn', '-c'], capture_output=True)
    except Exception as e:  # report any failure to the page instead of dying silently
        with LOCK:
            JOB['error'] = str(e)
        log('ERROR: ' + str(e))
    finally:
        with LOCK:
            JOB.update(running=False, finished=time.time())


def run_tests():
    out, ok = [], True
    for t in ('test_balancer.py', 'test_socket.py'):
        p = subprocess.run([sys.executable, os.path.join(ROOT, 'tests', t)], capture_output=True, text=True,
                           timeout=60)
        ok &= p.returncode == 0
        out.append(f'$ python3 tests/{t}\n' + p.stdout + p.stderr)
    return {'ok': ok, 'output': '\n'.join(out)}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            return self._send(200, open(os.path.join(HERE, 'index.html'), 'rb').read(), 'text/html; charset=utf-8')
        if self.path == '/api/results':
            return self._send(200, all_results())
        if self.path == '/api/status':
            with LOCK:
                return self._send(200, dict(JOB, now=time.time(), root=os.geteuid() == 0))
        self._send(404, {'error': 'not found'})

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
        except ValueError:
            return self._send(400, {'error': 'body must be JSON'})
        if self.path == '/api/tests':
            return self._send(200, run_tests())
        if self.path != '/api/run':
            return self._send(404, {'error': 'not found'})
        sc, dur = body.get('scenario'), body.get('duration', 30)
        if sc not in {'E1', 'E2', 'E3', 'E4', 'E5', 'E6'} or dur not in DURATIONS:
            return self._send(400, {'error': 'scenario must be E1-E6 and duration one of 15, 20, 30, 60'})
        if os.geteuid() != 0:
            return self._send(403, {'error': 'Mininet needs root: restart with  sudo -E python3 dashboard/server.py'})
        with LOCK:
            if JOB['running']:
                return self._send(409, {'error': f'{JOB["label"]} is still running'})
            JOB['running'] = True  # claim before the thread starts
        threads = [1, 2, 4, 8] if sc == 'E6' else [4]
        threading.Thread(target=run_job, args=(sc, dur, threads), daemon=True).start()
        self._send(202, {'started': sc})

    def log_message(self, fmt, *args):
        pass


if __name__ == '__main__':
    port = 8000
    if os.geteuid() != 0:
        print('Note: not running as root, so the Run buttons will be refused. Use: sudo -E python3 dashboard/server.py')
    print(f'Dashboard on http://localhost:{port}  (Ctrl+C to stop)', flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()
