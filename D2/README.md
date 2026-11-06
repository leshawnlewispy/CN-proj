# Adaptive Load Balancer with SDN (Ryu + Mininet + TCP sockets)

**Project 20, Team 9, Section E**: Leshawn Arron Lewis (PES1UG25CS280), Kishor Kumar M (PES1UG25CS265),
Dhule Shashwati Vijay (PES1202604072)

Three TCP servers sit behind one virtual IP, **10.0.0.100:8080**. A Ryu controller picks a server for every new
TCP connection. It uses the servers' own load reports (sent over UDP sockets) and the traffic on each network link.
A static round-robin mode is included as the baseline to compare against.

## Topology

```
                         ┌── link A (s1 port 5, 10 Mbit/s) ── s2 ──┬── srv1 10.0.0.11
 h1 h2 h3 h4 ─── s1 ─────┤                                         └── srv2 10.0.0.12
 10.0.0.1-4     (edge)   └── link B (s1 port 6, 10 Mbit/s) ── s3 ───── srv3 10.0.0.13

                 Ryu controller (OpenFlow 1.3) manages s1, s2 and s3
```

srv1 and srv2 share link A on purpose. That way "server busy" and "path busy" are different signals, and the
controller has to weigh both.

## How it works

| Piece | File | What it does |
| --- | --- | --- |
| TCP server | `app/server.py` | Thread per connection with a fixed number of workers (`--workers`, the capacity). Simulated work per request (`--slow` makes it weaker). Newline-delimited JSON protocol. |
| Load agent | inside `server.py` | Every 0.25 s sends `{"srv","active","cap","cpu","done"}` over UDP to 10.0.0.254:9999. No host owns that IP, so a switch rule sends it straight to the controller. |
| Client | `app/client.py` | N threads. Each request is a new TCP connection to the VIP, so every request gets a fresh load-balancing decision. Logs latency, serving server and errors to CSV. |
| Controller | `controller/lb_controller.py` | Answers ARP for the VIP. On a new SYN at s1 it picks a server and installs two NAT flows (client→VIP rewritten to client→server, and the reverse). Polls port stats on s1 every 1 s for link load. When a server stops reporting, it marks it down and deletes its flows (by cookie). All other traffic is handled as a learning switch. |
| Decision logic | `controller/balancer.py` | Pure Python, unit-tested. **static**: round-robin. **adaptive**: lowest score among healthy servers. |
| Topology | `topo/topo.py` | The Mininet network above. |

**Adaptive score.** The lowest score wins:

```
score = 0.5 × (active + just_assigned) / capacity  +  0.2 × cpu  +  0.3 × link_utilisation
```

- `just_assigned` counts flows sent to a server since its last report. Without it, every new flow would pile onto
  the same server between two reports.
- A server whose load is ≥ 90% of capacity goes into **draining**: it gets no new flows until its load drops
  below 70%.
- If there's no report for 3 s, the server is **down**. Its flows are deleted, so clients reconnect elsewhere.

**Connection stickiness.** Only a fresh SYN gets a new decision. Later packets of the same connection that reach
the controller before the flow rule is active are sent to the same server, using a small connection table.

## Setup

Use the Mininet VM (Ubuntu 20.04), or any Ubuntu with Mininet and Open vSwitch installed.

```bash
sudo apt install mininet openvswitch-switch iperf python3-pip
# Ryu 4.34 needs Python 3.8 or 3.9 and old setuptools/eventlet:
python3 -m venv ~/ryu && ~/ryu/bin/pip install "setuptools<58" wheel
~/ryu/bin/pip install ryu==4.34 eventlet==0.30.2
export RYU=~/ryu/bin/ryu-manager
pip3 install pandas matplotlib tabulate      # for the analysis only
```

On Ubuntu 22.04 or newer, the system Python is too new for Ryu. Get a 3.9 interpreter with
`uv python install 3.9` or the deadsnakes PPA, and build the venv with it.

## Run a live demo (two terminals)

```bash
# terminal 1: controller (LB_MODE=static for the baseline)
LB_MODE=adaptive LB_LOG=/tmp/controller.csv $RYU controller/lb_controller.py

# terminal 2: network
sudo python3 topo/topo.py            # add --user-dp if the OVS kernel module is missing
mininet> srv1 python3 app/server.py --name srv1 &
mininet> srv2 python3 app/server.py --name srv2 &
mininet> srv3 python3 app/server.py --name srv3 --slow 3 &
mininet> h1 python3 app/client.py --name h1 --threads 4 --duration 20 --out /tmp/h1.csv
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s1          # the NAT rewrite rules
mininet> srv2 pkill -f "name srv2"                          # kill a server; watch terminal 1
```

Terminal 1 prints state changes like `srv2 -> down (no report for 3 s)`. In `/tmp/h1.csv` you can see which
server answered each request.

## Run all experiments

```bash
sudo -E ./experiments/run_all.sh          # 18 runs x ~40 s, then analysis/analyze.py
sudo -E python3 experiments/run.py --scenario E4 --mode adaptive   # a single run
```

| # | Scenario | How it is created |
| --- | --- | --- |
| E1 | Balanced | 3 equal servers; h1-h3 with 4 threads each |
| E2 | Unequal servers | srv3 is a smaller machine: 2 workers instead of 8, 2x slower |
| E3 | Hot server | at t = 10 s, h4 sends slow requests straight to srv1 (bypassing the LB) |
| E4 | Server failure | at t = 10 s, the srv2 process is killed |
| E5 | Link congestion | at t = 10 s, h4 sends 9 Mbit/s UDP iperf across link A |
| E6 | Scale-up | E1 with 1, 2, 4 and 8 threads per client host |

The output goes to `results/`: raw CSVs per run, `summary.csv` / `summary.md`, and graphs (`latency.png`,
`errors_fairness.png`, `e3/e4/e5_timeline.png`, `e6_scale.png`).

## Results

Run the experiments (see above) to fill results/ with summary.md, summary.csv and the graphs.

## Tests

```bash
python3 tests/test_balancer.py   # scoring, draining, failure, congestion, round-robin
python3 tests/test_socket.py     # real server + client over localhost, malformed request
```

## Rubric map

| Component | Where |
| --- | --- |
| Architecture | this README; the topology and "How it works" sections |
| TCP socket implementation | `app/server.py`, `app/client.py`, `tests/test_socket.py` |
| Mininet + SDN | `topo/topo.py`, `controller/lb_controller.py` (ARP, NAT flows, learning switch, port stats) |
| Socket–SDN integration | UDP load reports read by the controller from PacketIn and used to place flows |
| Dynamic SDN | per-flow placement, draining, failure detection with flow deletion, path-aware scoring |
| Challenging scenarios | E3 hot server, E4 server kill, E5 link congestion |
| Performance comparison | `experiments/`, `analysis/analyze.py`, Results section above |

## Team split

- **Leshawn:** controller and integration
- **Kishor:** socket application
- **Shashwati:** topology, experiments, analysis

## Dashboard (run experiments from the browser)

    sudo RYU=$HOME/ryu/bin/ryu-manager python3 dashboard/server.py
    # open http://localhost:8000

Each tab (E1–E6) has a Run button. It runs static and then adaptive on this machine
and draws the tiles, bar charts, timelines and table from `results/`. The Tests tab
runs the unit and socket tests. Add `--user-dp` if Open vSwitch has no kernel module.
