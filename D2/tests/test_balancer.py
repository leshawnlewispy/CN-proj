"""Run: python3 tests/test_balancer.py   (or pytest)"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'controller'))
from balancer import Balancer, Server  # noqa: E402


def make(mode='adaptive'):
    clock = [0.0]
    servers = [Server('srv1', '10.0.0.11', 'm1', 'A'),
               Server('srv2', '10.0.0.12', 'm2', 'A'),
               Server('srv3', '10.0.0.13', 'm3', 'B')]
    lb = Balancer(servers, mode, now=lambda: clock[0])
    for s in servers:
        lb.report(s.name, 0, 10, 0.0)
    return lb, clock


def test_static_is_round_robin_even_when_down():
    lb, clock = make('static')
    clock[0] = 10
    lb.check_dead()
    assert [lb.pick().name for _ in range(6)] == ['srv1', 'srv2', 'srv3'] * 2


def test_adaptive_prefers_least_loaded():
    lb, _ = make()
    lb.report('srv1', 8, 10, 0.5)
    lb.report('srv2', 2, 10, 0.1)
    lb.report('srv3', 5, 10, 0.2)
    assert lb.pick().name == 'srv2'


def test_pending_spreads_flows_between_reports():
    lb, _ = make()
    picks = [lb.pick().name for _ in range(3)]
    assert sorted(picks) == ['srv1', 'srv2', 'srv3']


def test_drain_and_resume_hysteresis():
    lb, _ = make()
    assert lb.report('srv1', 9, 10, 0) == 'draining'
    assert lb.report('srv1', 8, 10, 0) is None          # 0.8 still draining
    assert all(lb.pick().name != 'srv1' for _ in range(5))
    assert lb.report('srv1', 6, 10, 0) == 'healthy'


def test_dead_server_skipped():
    lb, clock = make()
    clock[0] = 2
    lb.report('srv1', 0, 10, 0)
    lb.report('srv3', 0, 10, 0)
    clock[0] = 4
    assert [s.name for s in lb.check_dead()] == ['srv2']
    assert all(lb.pick().name != 'srv2' for _ in range(6))


def test_fast_server_finishes_its_pending_flows():
    lb, clock = make()
    clock[0] = 1.0
    lb.report('srv1', 2, 10, 0, done=40)   # fast: 40 req/s
    lb.report('srv2', 2, 10, 0, done=4)    # slow: 4 req/s
    lb.report('srv3', 9, 10, 0, done=0)
    for _ in range(4):
        lb.servers['srv1'].pending += 1
        lb.servers['srv2'].pending += 1
    clock[0] = 1.2                          # srv1 finished ~8 flows since, srv2 ~0.8
    assert lb.pick().name == 'srv1'


def test_congested_link_shifts_to_other_path():
    lb, _ = make()
    lb.set_link_rate('A', 9.5e6)
    assert lb.pick().name == 'srv3'


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
            print('ok', name)
