# Adaptive Load Balancer: Deliverable 1

Team 9 · Project 20 · SDN + socket programming (TCP) · Mininet + Ryu

Three TCP servers share one virtual address, **10.0.0.100:8080**. Each server reports its load
to the Ryu controller over UDP. When a client opens a new TCP connection, the controller sends it
to the server with the fewest connections by installing OpenFlow rules on the switch.

## Objectives

1. TCP client/server application with concurrent clients (low-level sockets).
2. Mininet topology controlled by a Ryu (OpenFlow 1.3) controller.
3. Socket–SDN integration: application load reports change the controller's forwarding decisions.
4. End-to-end demo and tests.

## Architecture

```
  h1 10.0.0.1 --1--+                                  +--2-- srv1 10.0.0.11
  h2 10.0.0.2 --2--+       link A, 10 Mbit/s          |
  h3 10.0.0.3 --3--+-- s1 --5-------------------1-- s2 --3-- srv2 10.0.0.12
  h4 10.0.0.4 --4--+    \
                         6-- link B, 10 Mbit/s --1-- s3 --2-- srv3 10.0.0.13

  s1, s2, s3: Open vSwitch, OpenFlow 1.3, controlled by Ryu (controller.py) on 127.0.0.1:6653
```

Numbers on the lines are switch port numbers. Clients h1–h3 send requests in the demo; h4 is
part of the topology but idle in D1. srv1 and srv2 share link A, srv3 is alone on link B.

| File | Component |
|---|---|
| `server.py` | TCP server (thread per connection) + UDP load reporter |
| `client.py` | Concurrent TCP client (threads), one new connection per request |
| `controller.py` | Ryu app: virtual IP, load reports, least-connections choice, NAT flow rules |
| `topo.py` | Mininet topology + end-to-end demo |
| `test_socket.py` | Socket test on localhost (no Mininet) |

## Communication flow

1. Every 0.5 s each server sends UDP `{"srv": "srv1", "active": 2}` to 10.0.0.254:9999.
   A priority-100 switch rule sends these to the controller (PacketIn). No host has that address.
2. A client asks for 10.0.0.100's MAC; the controller answers the ARP itself.
3. The client's TCP SYN to 10.0.0.100:8080 has no matching rule, so the switch sends it to the controller.
4. The controller picks the server with the lowest `reported active + connections sent since that report`.
5. It installs two rules on s1 (idle timeout 10 s):
   - client → VIP: rewrite the destination to the chosen server's MAC/IP, send out the link
     toward it (port 5 = link A for srv1/srv2, port 6 = link B for srv3).
   - server → client: rewrite the source to the VIP, send out the client's port.
   s2 and s3 deliver the packet to the server as normal learning switches.
6. The rest of the connection is forwarded by the switches without the controller.
   The client sends `{"id": n}` and gets `{"id": n, "server": "srv2"}`.

Other traffic (ARP between real hosts, ping) is handled as a learning switch.

**Expected behaviour:** requests spread over all three servers. A server that is slower has
more connections open in its reports, so it receives fewer new connections.

## Run (Ubuntu VM with Mininet, Open vSwitch and Ryu)

```bash
python3 test_socket.py                                 # 1. socket test, no root needed
sudo RYU=$HOME/ryu/bin/ryu-manager python3 topo.py     # 2. Ryu + Mininet + demo, then Mininet CLI
```

Use `--no-cli` to exit after the demo. Leave out `RYU=...` if `ryu-manager` is on your PATH.

Expected demo output (numbers vary):

```
*** 3 clients x 4 threads x 10 requests to the VIP 10.0.0.100:8080
h1: {"srv1": 9, "srv2": 15, "srv3": 16}
h2: {"srv1": 13, "srv2": 16, "srv3": 11}
h3: {"srv1": 9, "srv2": 14, "srv3": 17}
*** total: {'srv1': 31, 'srv2': 45, 'srv3': 44}
*** srv1 is slow, so it should get the fewest requests: yes
*** RESULT: PASS
```

`controller.log` shows every decision, for example `10.0.0.1:35284 -> srv2 (load {...})`.

In the Mininet CLI you can also check:

```
mininet> pingall
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s1
mininet> h1 python3 client.py --threads 2 --requests 5
```

## Test cases

| Test | How | Pass when |
|---|---|---|
| TCP request/reply, concurrency | `test_socket.py` | 4 threads × 5 requests all answered |
| UDP load report format | `test_socket.py` | report arrives as valid JSON |
| Connectivity through the SDN switch | `topo.py` (ping h1 → srv2), `pingall` | 0% loss |
| VIP load balancing end to end | `topo.py` | no errors and every server answered requests |
| Load reports influence decisions | `topo.py` (srv1 is 6× slower) | srv1 gets the fewest requests |

## D1 rubric map

| Component (marks) | Where |
|---|---|
| Problem understanding & architecture (2) | This README: objectives, architecture, flow, expected behaviour |
| TCP/UDP socket implementation (4) | `server.py`, `client.py` |
| Mininet + SDN implementation (4) | `topo.py`, `controller.py` |
| Socket–SDN integration (3) | UDP reports → PacketIn → `on_report` → `pick` → FlowMod |
| Initial testing & demo (2) | `test_socket.py`, `topo.py` demo |
