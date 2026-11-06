"""Load-balancing decisions. Pure Python (no Ryu) so it can be unit-tested.

static   = plain round-robin, ignores load and health (the baseline)
adaptive = lowest score among healthy servers, where
           score = W_CONN*connections/capacity + W_CPU*cpu + W_NET*link utilisation
"""
import itertools
import time

LINK_BPS = 10e6            # must match the bw of the s1-s2 / s1-s3 links in topo.py
DRAIN_AT = 0.9             # load (active/capacity) at which a server stops getting new flows
RESUME_AT = 0.7            # ...and the load at which it gets them again (hysteresis)
DEAD_AFTER = 3.0           # seconds without a load report => server is down
W_CONN, W_CPU, W_NET = 0.5, 0.2, 0.3


class Server:
    def __init__(self, name, ip, mac, link):
        self.name, self.ip, self.mac, self.link = name, ip, mac, link
        self.active, self.cap, self.cpu = 0, 1, 0.0
        self.pending = 0       # flows we assigned since its last report (stops herding)
        self.rate = 0.0        # requests/s it completes (from reports)
        self.last = None       # time of last report
        self.state = 'unknown'  # unknown | healthy | draining | down


class Balancer:
    def __init__(self, servers, mode='adaptive', now=time.time):
        assert mode in ('static', 'adaptive')
        self.servers = {s.name: s for s in servers}
        self.mode, self.now = mode, now
        self._rr = itertools.cycle(servers)
        self.link_bps = {}

    def report(self, name, active, cap, cpu, done=0):
        """Apply a load report; returns the new state if it changed, else None."""
        s, now = self.servers[name], self.now()
        if s.last is not None and now > s.last:
            s.rate = done / (now - s.last)
        s.active, s.cap, s.cpu = active, max(cap, 1), cpu
        s.pending, s.last = 0, now
        load = s.active / s.cap
        if s.state == 'draining':
            new = 'healthy' if load < RESUME_AT else 'draining'
        else:
            new = 'draining' if load >= DRAIN_AT else 'healthy'
        changed, s.state = new != s.state, new
        return new if changed else None

    def set_link_rate(self, link, bps):
        self.link_bps[link] = bps

    def link_util(self, link):
        return min(1.0, self.link_bps.get(link, 0) / LINK_BPS)

    def check_dead(self):
        """Mark servers that stopped reporting as down; returns the newly dead ones."""
        dead = [s for s in self.servers.values()
                if s.state in ('healthy', 'draining') and self.now() - s.last > DEAD_AFTER]
        for s in dead:
            s.state = 'down'
        return dead

    def est_active(self, s):
        """Connections now = last report + flows assigned since - flows it has finished since."""
        since = self.now() - s.last if s.last is not None else 0
        return max(0.0, s.active + s.pending - s.rate * since)

    def score(self, s):
        return (W_CONN * self.est_active(s) / s.cap
                + W_CPU * s.cpu
                + W_NET * self.link_util(s.link))

    def pick(self):
        if self.mode == 'static':
            s = next(self._rr)
        else:
            pool = ([s for s in self.servers.values() if s.state == 'healthy']
                    or [s for s in self.servers.values() if s.state == 'draining'])
            s = min(pool, key=self.score) if pool else next(self._rr)
        s.pending += 1
        return s
