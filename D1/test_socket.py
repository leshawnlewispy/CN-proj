"""Socket test on localhost (no Mininet, no root). Run: python3 test_socket.py

Starts server.py, runs client.py against it, and checks that
  1. every TCP request got a reply from the server, and
  2. the server's UDP load report arrives and is valid JSON.
"""
import json
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PORT, REPORT = 18080, 19999

udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp.bind(('127.0.0.1', REPORT))
udp.settimeout(3)
srv = subprocess.Popen([sys.executable, os.path.join(HERE, 'server.py'), '--name', 'srvT',
                        '--host', '127.0.0.1', '--port', str(PORT),
                        '--report', f'127.0.0.1:{REPORT}'])
try:
    time.sleep(1)
    out = subprocess.run([sys.executable, os.path.join(HERE, 'client.py'), '--vip', '127.0.0.1',
                          '--port', str(PORT), '--threads', '4', '--requests', '5'],
                         capture_output=True, text=True, timeout=30).stdout
    counts = json.loads(out.split(': ', 1)[1])
    assert counts == {'srvT': 20}, f'expected 20 replies from srvT, got {counts}'
    print('PASS  TCP: 4 threads x 5 requests, all 20 answered')

    report = json.loads(udp.recv(1024))
    assert report['srv'] == 'srvT' and isinstance(report['active'], int), report
    print('PASS  UDP: load report received', report)
finally:
    srv.terminate()
