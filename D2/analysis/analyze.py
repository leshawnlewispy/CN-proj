"""Turn results/*/ CSVs into results/summary.csv, summary.md and graphs.

  python3 analysis/analyze.py [results_dir]
"""
import glob
import json
import os
import sys

import matplotlib
import pandas as pd

matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402

RES = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), '..', 'results')
COLORS = {'static': '#9aa0a6', 'adaptive': '#1a73e8'}


def jain(xs):
    xs = [x for x in xs if x == x]
    return sum(xs) ** 2 / (len(xs) * sum(x * x for x in xs)) if xs and any(xs) else float('nan')


def load_runs():
    rows, runs = [], {}
    for d in sorted(glob.glob(os.path.join(RES, 'E*_*_t*'))):
        if not os.path.exists(os.path.join(d, 'meta.json')):
            continue
        meta = json.load(open(os.path.join(d, 'meta.json')))
        c = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(os.path.join(d, 'h[1-3].csv')))])
        ctl = pd.read_csv(os.path.join(d, 'controller.csv'))
        ok = c[c.ok == 1]
        ev = meta['event_ts']
        after = ok[ok.ts >= ev] if ev else ok
        errs = c[c.ok == 0]
        st = ctl[(ctl.event == 'state') & (ctl.state != 'down')]
        util = (st.active / st.cap).groupby(st.server).mean()
        share = ok.server.value_counts(normalize=True)
        row = dict(scenario=meta['scenario'], mode=meta['mode'], threads=meta['threads'],
                   requests=len(c), error_pct=round(100 * len(errs) / max(len(c), 1), 1),
                   throughput_rps=round(len(ok) / meta['duration'], 1),
                   mean_ms=round(ok.latency_ms.mean(), 1), p95_ms=round(ok.latency_ms.quantile(.95), 1),
                   p95_after_event_ms=round(after.latency_ms.quantile(.95), 1),
                   jain_util=round(jain(util.tolist()), 3),
                   **{f'{s}_share': round(share.get(s, 0), 2) for s in ('srv1', 'srv2', 'srv3')})
        if meta['scenario'] == 'E4' and ev:
            e = errs[errs.ts >= ev]
            row['errors_after_failure'] = len(e)
            row['error_window_s'] = round(e.ts.max() - ev, 1) if len(e) else 0.0
        rows.append(row)
        runs[(meta['scenario'], meta['mode'], meta['threads'])] = (c, ctl, meta)
    return pd.DataFrame(rows), runs


def bars(df, cols, titles, fname, ylabel):
    base = df[(df.scenario != 'E6') & (df.threads == 4)]
    fig, axes = plt.subplots(1, len(cols), figsize=(5.5 * len(cols), 4))
    for ax, col, title in zip(axes if len(cols) > 1 else [axes], cols, titles):
        pv = base.pivot(index='scenario', columns='mode', values=col).reindex(columns=['static', 'adaptive'])
        pv.plot.bar(ax=ax, color=[COLORS['static'], COLORS['adaptive']], rot=0, width=0.75)
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.set_xlabel('')
        ax.grid(axis='y', alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(RES, fname), dpi=130)
    plt.close(fig)


def timeline(runs, scenario, fname):
    fig, axes = plt.subplots(1, 2, figsize=(12, 3.8), sharey=True)
    for ax, mode in zip(axes, ('static', 'adaptive')):
        if (scenario, mode, 4) not in runs:
            continue
        c, _, meta = runs[(scenario, mode, 4)]
        t0 = c.ts.min()
        ok = c[c.ok == 1].assign(sec=lambda x: (x.ts - t0).astype(int))
        per = ok.groupby(['sec', 'server']).size().unstack(fill_value=0)
        per.plot(ax=ax)
        err = c[c.ok == 0].assign(sec=lambda x: (x.ts - t0).astype(int)).groupby('sec').size()
        if len(err):
            ax.bar(err.index, err.values, color='#d93025', alpha=0.5, label='errors')
        if meta['event_ts']:
            ax.axvline(meta['event_ts'] - t0, color='k', ls='--', lw=1)
        ax.set_title(f'{scenario} {mode}')
        ax.set_xlabel('time (s)')
        ax.set_ylabel('requests / s')
        ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(RES, fname), dpi=130)
    plt.close(fig)


def scale(df):
    e6 = df[df.scenario == 'E6']
    if e6.empty:
        return
    fig, ax = plt.subplots(figsize=(6, 4))
    for mode in ('static', 'adaptive'):
        m = e6[e6['mode'] == mode].sort_values('threads')
        ax.plot(m.threads * 3, m.p95_ms, marker='o', color=COLORS[mode], label=mode)
    ax.set_xlabel('concurrent client threads (3 hosts)')
    ax.set_ylabel('p95 latency (ms)')
    ax.set_title('E6 scale-up')
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(RES, 'e6_scale.png'), dpi=130)
    plt.close(fig)


def main():
    df, runs = load_runs()
    if df.empty:
        sys.exit(f'no finished runs in {RES}')
    df = df.sort_values(['scenario', 'threads', 'mode'], ascending=[True, True, False])
    df.to_csv(os.path.join(RES, 'summary.csv'), index=False)
    open(os.path.join(RES, 'summary.md'), 'w').write(df.to_markdown(index=False))
    print(df.to_string(index=False))
    bars(df, ['mean_ms', 'p95_after_event_ms'], ['Mean latency', 'p95 latency after the event'],
         'latency.png', 'ms')
    bars(df, ['error_pct', 'jain_util'], ['Failed requests (%)', "Jain's fairness of server utilisation"],
         'errors_fairness.png', '')
    for s in ('E3', 'E4', 'E5'):
        timeline(runs, s, f'{s.lower()}_timeline.png')
    scale(df)


if __name__ == '__main__':
    main()
