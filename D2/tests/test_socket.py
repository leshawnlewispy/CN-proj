"""End-to-end socket test on localhost (no Mininet needed). Run: python3 tests/test_socket.py"""
import csv
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app')
PORT = 18080


def test_server_and_client():
    srv = subprocess.Popen([sys.executable, os.path.join(APP, 'server.py'), '--name', 'srvT',
                            '--port', str(PORT), '--report', 'none'], stdout=subprocess.DEVNULL)
    try:
        time.sleep(0.5)
        out = os.path.join(tempfile.mkdtemp(), 'c.csv')
        subprocess.run([sys.executable, os.path.join(APP, 'client.py'), '--vip', '127.0.0.1',
                        '--port', str(PORT), '--threads', '4', '--duration', '2', '--work', '20',
                        '--out', out], check=True)
        rows = list(csv.DictReader(open(out)))
        assert len(rows) > 20, len(rows)
        assert all(r['ok'] == '1' and r['server'] == 'srvT' for r in rows), rows[:3]

        with socket.create_connection(('127.0.0.1', PORT)) as s:   # malformed request
            s.sendall(b'not json\n')
            assert json.loads(s.makefile('rb').readline())['error'] == 'bad request'
    finally:
        srv.kill()


if __name__ == '__main__':
    test_server_and_client()
    print('ok test_server_and_client')
