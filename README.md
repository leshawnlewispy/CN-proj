# Adaptive Load Balancer with SDN

**Computer Networks (UE25CS243A) · Jackfruit Mini Project · Project 20 · Team 9, Section E**

| Name | SRN |
|---|---|
| Leshawn Arron Lewis | PES1UG25CS280 |
| Kishor Kumar M | PES1UG25CS265 |
| Dhule Shashwati Vijay | PES1202604072 |

Three TCP servers share one virtual IP, **10.0.0.100:8080**. The servers report their load to a **Ryu SDN
controller** over UDP sockets. For every new TCP connection, the controller picks a server and installs
OpenFlow rules on the switch so the connection goes there. Everything runs in a **Mininet** network.

- **D1** is the foundation: TCP client/server, the Mininet topology, the Ryu controller, and the controller
  choosing the server with the fewest connections from the servers' load reports.
- **D2** is the full adaptive balancer: a weighted score of server load, CPU and link traffic, draining of
  overloaded servers, failure detection, a static round-robin baseline, six experiments, analysis and a web dashboard.

## Repository layout

```
D1/          Deliverable 1 code (see D1/README.md)
  server.py        TCP server + UDP load reporter
  client.py        concurrent TCP client
  controller.py    Ryu controller: VIP, load reports, least-connections choice, NAT flow rules
  topo.py          Mininet topology + end-to-end demo (prints PASS/FAIL)
  test_socket.py   socket test on localhost
D2/          Deliverable 2 code (see D2/README.md)
  app/             server.py, client.py
  controller/      balancer.py (decision logic), lb_controller.py (Ryu app)
  topo/            topo.py
  experiments/     run.py (one experiment), run_all.sh (all 18 runs)
  analysis/        analyze.py (summary tables + graphs)
  tests/           test_balancer.py, test_socket.py
  dashboard/       server.py + index.html (run experiments from the browser)
docs/        slides and explanations (PDF)
```

## Topology (D1 and D2)

```
  h1 --1--+                                  +--2-- srv1 10.0.0.11
  h2 --2--+       link A, 10 Mbit/s          |
  h3 --3--+-- s1 --5-------------------1-- s2 --3-- srv2 10.0.0.12
  h4 --4--+    \
                6-- link B, 10 Mbit/s --1-- s3 --2-- srv3 10.0.0.13

  s1, s2, s3: Open vSwitch, OpenFlow 1.3, controlled by Ryu on 127.0.0.1:6653
```

## Requirements

Ubuntu with Mininet, Open vSwitch, iperf and Ryu 4.34. Ryu needs Python 3.8 or 3.9:

```bash
sudo apt install mininet openvswitch-switch iperf python3-pip
uv python install 3.9 && uv venv --python 3.9 ~/ryu      # or any Python 3.9 venv
uv pip install --python ~/ryu/bin/python "setuptools<58" wheel ryu==4.34 eventlet==0.30.2
export RYU=~/ryu/bin/ryu-manager
pip3 install pandas matplotlib tabulate                  # D2 analysis only
```

## Quick start

**D1**

```bash
cd D1
python3 test_socket.py                                   # PASS TCP, PASS UDP
sudo RYU=$RYU python3 topo.py                            # Ryu + Mininet + demo -> RESULT: PASS
```

**D2**

```bash
cd D2
python3 tests/test_balancer.py                           # 7 "ok" lines
python3 tests/test_socket.py
sudo -E python3 experiments/run.py --scenario E4 --mode static
sudo -E python3 experiments/run.py --scenario E4 --mode adaptive
sudo -E ./experiments/run_all.sh                         # all 18 runs + graphs (about 15 min)
sudo RYU=$RYU python3 dashboard/server.py                # website on http://localhost:8000
```

If a run hangs, clean up with `sudo mn -c`.

## D2 experiments

| # | Scenario | Event at one third of the run |
|---|---|---|
| E1 | Balanced | none |
| E2 | Unequal servers | srv3 has 2 workers instead of 8 and runs 2× slower |
| E3 | Hot server | h4 floods srv1 directly |
| E4 | Server failure | srv2 is killed |
| E5 | Link congestion | h4 sends 9 Mbit/s iperf across link A |
| E6 | Scale-up | E1 at 1, 2, 4 and 8 threads per client |

Each runs in **static** (round-robin) and **adaptive** mode. Metrics: throughput, mean and p95 latency,
p95 after the event, error rate, server share, Jain's fairness, and (E4) how long errors last.

## Documents

| File | Contents |
|---|---|
| `docs/D1-slides.pdf` | D1 presentation, with the D2 design and predictions |
| `docs/D1-code-explained.pdf` | Every D1 file and code block explained |
| `docs/D1-how-to-run.pdf` | How to run D1 |
| `docs/D2-slides.pdf` | D2 presentation with the experiment results |
| `docs/D2-files-explained.pdf` | Every D2 file explained, plus the commands and what their output means |
