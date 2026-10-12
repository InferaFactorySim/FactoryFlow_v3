"""DT2 KPI comparison for S4 phase C, using the reference's own KPI code.

Reference: the phase C plant (phase B delays from t = 0, line 00 no longer fed
from t = 150). DT2: the twin regenerated after the change (line 00 kept but
unfed) with the phase B plugins. Stale twin: DT1, the twin that still feeds all
ten lines, with the same plugins. KPIs over the steady state 1,150 - 4,150.
"""
import contextlib
import io
import json
import os
import sys
import types
from multiprocessing import Pool

import numpy as np

from s3_paths import PLUGINS_B, RERUN, TWIN_AB, TWIN_C, TWIN_SEED_BASE, seed_twin

MODELS = {"dt2": TWIN_C, "stale": TWIN_AB}
PLUGINS = PLUGINS_B
T_CHANGE, WINDOW, T_END = 150.0, (1150.0, 4150.0), 4150.0
REPS, INTERVAL = 10, 50.0
OUT = os.path.join(RERUN, "s3_twin_kpis_phaseC.json")
KPIS = ["throughput_items_per_unit", "mean_cycle_time", "max_cycle_time",
        "mean_machine_utilisation_pct", "mean_queue_occupancy_items", "energy_per_item_kJ"]


def run_reference(seed):
    import simpy
    import S3_GT
    from run_s3_experiment import window_kpis
    S3_GT.T_PARAM_CHANGE, S3_GT.T_ROUTE_CHANGE = 0.0, T_CHANGE   # as S3_GT_phaseC.py
    env, rows = simpy.Environment(), []
    with contextlib.redirect_stdout(io.StringIO()):
        model = S3_GT.build(env, seed_base=seed)
        env.process(S3_GT.sampler(env, model, rows, INTERVAL))
        env.run(until=T_END)
        rows.append(S3_GT.snapshot(model, env.now))
    return window_kpis(rows, *WINDOW, "C")


def run_twin(kind, rep):
    import S3_GT
    from run_s3_experiment import window_kpis
    from factorysimpy.edges.converyorbelt import ConveyorBelt
    os.environ["DELAY_PLUGIN_DIR"] = PLUGINS
    seed_twin(TWIN_SEED_BASE["dt2" if kind == "dt2" else "stale_C"] + rep)
    path = MODELS[kind]
    src = open(path, encoding="utf-8").read()
    src = src[:src.rindex("TOP.run_simulation")]
    ns = {"__file__": path, "__name__": f"twin_{kind}"}
    with contextlib.redirect_stdout(io.StringIO()):
        exec(compile(src, path, "exec"), ns)
    top, env = ns["TOP"], ns["env"]
    lines = top.Lines
    line_edges = lambda line: list(line.edges.values()) if isinstance(line.edges, dict) else list(line.edges)  # noqa: E731
    view = types.SimpleNamespace(
        sink=top.sink, source=top.source,
        lines=[types.SimpleNamespace(edges=line_edges(line)) for line in lines],
        entry_buffers=top.B_IN, exit_buffers=top.B_OUT,
        machines=lambda: [m for line in lines for m in line.M],
        belts=lambda: [e for line in lines for e in line_edges(line) if isinstance(e, ConveyorBelt)],
    )
    rows = []
    with contextlib.redirect_stdout(io.StringIO()):
        env.process(S3_GT.sampler(env, view, rows, INTERVAL))
        env.run(until=T_END)
        rows.append(S3_GT.snapshot(view, env.now))
    return window_kpis(rows, *WINDOW, "C")


def job(spec):
    kind, rep = spec
    return kind, rep, (run_reference(rep) if kind == "ref" else run_twin(kind, rep))


if __name__ == "__main__":
    specs = [(k, r) for r in range(REPS) for k in ("ref", "dt2", "stale")]
    os.makedirs(RERUN, exist_ok=True)
    with Pool(4) as pool:
        results = pool.map(job, specs)
    json.dump(results, open(OUT, "w"), indent=1, default=float)
    by = {k: [res for kind, rep, res in results if kind == k] for k in ("ref", "dt2", "stale")}
    print(f"Phase C, window {WINDOW[0]:g}-{WINDOW[1]:g}, {REPS} runs each")
    print(f"{'KPI':30s} {'ref mean (SD)':>20s} {'stale twin':>11s} {'DT2':>9s}")
    for kpi in KPIS:
        r = np.array([k[kpi] for k in by["ref"]])
        s = np.mean([k[kpi] for k in by["stale"]]); d = np.mean([k[kpi] for k in by["dt2"]])
        print(f"{kpi:30s} {r.mean():10.4f} ({r.std(ddof=1):7.4f}) {100 * (s - r.mean()) / r.mean():+10.2f}% "
              f"{100 * (d - r.mean()) / r.mean():+8.2f}%")
