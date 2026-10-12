"""Long-run phase A comparison: true distributions against fitted plugins.

Reference: S4 with the phase A distributions and no changes (both change times
moved beyond the run). DT0: the generated twin with the phase A plugins fitted
from 500 readings per machine. Both run to T_END; KPIs over (WARMUP, T_END].
"""
import contextlib
import io
import json
import os
import sys
import types
from multiprocessing import Pool

import numpy as np
from scipy import stats

from s3_paths import PLUGINS_A, RERUN, TWIN_AB, TWIN_SEED_BASE, seed_twin

TWIN = TWIN_AB
PLUGINS = PLUGINS_A
T_END, WARMUP, REPS, INTERVAL = 50000.0, 1000.0, 5, 500.0
OUT = os.path.join(RERUN, "s3_long_run_phaseA.json")
KPIS = ["throughput_items_per_unit", "mean_cycle_time", "max_cycle_time",
        "mean_machine_utilisation_pct", "mean_queue_occupancy_items", "energy_per_item_kJ"]


def run_reference(seed):
    import simpy
    import S3_GT
    from run_s3_experiment import window_kpis
    S3_GT.T_PARAM_CHANGE = S3_GT.T_ROUTE_CHANGE = 1e12      # phase A throughout
    env, rows = simpy.Environment(), []
    with contextlib.redirect_stdout(io.StringIO()):
        model = S3_GT.build(env, seed_base=seed)
        env.process(S3_GT.sampler(env, model, rows, INTERVAL))
        env.run(until=T_END)
        rows.append(S3_GT.snapshot(model, env.now))
    return window_kpis(rows, WARMUP, T_END, "A")


def run_twin(rep):
    import S3_GT
    from run_s3_experiment import window_kpis
    from factorysimpy.edges.converyorbelt import ConveyorBelt
    os.environ["DELAY_PLUGIN_DIR"] = PLUGINS
    seed_twin(TWIN_SEED_BASE["dt0"] + 1000 + rep)
    src = open(TWIN, encoding="utf-8").read()
    src = src[:src.rindex("TOP.run_simulation")]
    ns = {"__file__": TWIN, "__name__": "dt0_long"}
    with contextlib.redirect_stdout(io.StringIO()):
        exec(compile(src, TWIN, "exec"), ns)
    top, env = ns["TOP"], ns["env"]
    lines = top.Lines
    view = types.SimpleNamespace(
        sink=top.sink, source=top.source,
        lines=[types.SimpleNamespace(edges=list(line.edges.values())) for line in lines],
        entry_buffers=top.B_IN, exit_buffers=top.B_OUT,
        machines=lambda: [m for line in lines for m in line.M],
        belts=lambda: [e for line in lines for e in line.edges.values() if isinstance(e, ConveyorBelt)],
    )
    rows = []
    with contextlib.redirect_stdout(io.StringIO()):
        env.process(S3_GT.sampler(env, view, rows, INTERVAL))
        env.run(until=T_END)
        rows.append(S3_GT.snapshot(view, env.now))
    return window_kpis(rows, WARMUP, T_END, "A")


def job(spec):
    kind, rep = spec
    return kind, rep, (run_reference(rep) if kind == "ref" else run_twin(rep))


if __name__ == "__main__":
    specs = [(k, r) for r in range(REPS) for k in ("ref", "dt0")]
    os.makedirs(RERUN, exist_ok=True)
    with Pool(4) as pool:
        results = pool.map(job, specs)
    json.dump(results, open(OUT, "w"), indent=1, default=float)
    by = {k: [res for kind, rep, res in results if kind == k] for k in ("ref", "dt0")}
    print(f"Phase A, no change, window {WARMUP:g}-{T_END:g}, {REPS} runs each")
    print(f"{'KPI':30s} {'reference mean (SD)':>22s} {'DT0 mean (SD)':>22s} {'diff %':>8s} {'95% CI of diff %':>20s} {'p':>7s}")
    for kpi in KPIS:
        r = np.array([k[kpi] for k in by["ref"]]); d = np.array([k[kpi] for k in by["dt0"]])
        diff = d.mean() - r.mean()
        se = np.sqrt(r.var(ddof=1) / len(r) + d.var(ddof=1) / len(d))
        t = stats.ttest_ind(d, r, equal_var=False)
        dof = se ** 4 / ((r.var(ddof=1) / len(r)) ** 2 / (len(r) - 1) + (d.var(ddof=1) / len(d)) ** 2 / (len(d) - 1))
        half = stats.t.ppf(0.975, dof) * se
        print(f"{kpi:30s} {r.mean():12.4f} ({r.std(ddof=1):7.4f}) {d.mean():12.4f} ({d.std(ddof=1):7.4f}) "
              f"{100 * diff / r.mean():+7.2f}% [{100 * (diff - half) / r.mean():+6.2f}%, {100 * (diff + half) / r.mean():+6.2f}%] {t.pvalue:7.4f}")
