"""DT0 / DT1 KPI comparison for S4, using the reference's own KPI code."""
import contextlib
import io
import json
import os
import sys
import types
from multiprocessing import Pool

import numpy as np

from s3_paths import PLUGINS_A, PLUGINS_B, RERUN, TWIN_AB, TWIN_SEED_BASE, seed_twin

GEN = TWIN_AB
PLUGINS = {"dt0": PLUGINS_A, "dt1": PLUGINS_B}
WINDOWS = {"A": (0.0, 4000.0), "B": (4200.0, 8000.0)}
REPS = 10
INTERVAL = 50.0
OUT = os.path.join(RERUN, "twin_kpis.json")

KPIS = ["throughput_items_per_unit", "mean_cycle_time", "max_cycle_time",
        "mean_machine_utilisation_pct", "mean_queue_occupancy_items", "energy_per_item_kJ"]


def run_reference(seed):
    import simpy
    import S3_GT
    from run_s3_experiment import window_kpis
    env = simpy.Environment()
    rows = []
    with contextlib.redirect_stdout(io.StringIO()):
        model = S3_GT.build(env, seed_base=seed)
        env.process(S3_GT.sampler(env, model, rows, INTERVAL))
        env.run(until=8000.0)
        rows.append(S3_GT.snapshot(model, env.now))
    return {ph: window_kpis(rows, *WINDOWS[ph], ph) for ph in ("A", "B")}


def run_twin(kind, rep):
    import S3_GT
    from run_s3_experiment import window_kpis
    from factorysimpy.edges.converyorbelt import ConveyorBelt
    os.environ["DELAY_PLUGIN_DIR"] = PLUGINS[kind]
    seed_twin(TWIN_SEED_BASE[kind] + rep)
    src = open(GEN, encoding="utf-8").read().replace("TOP.run_simulation(10000)", "")
    ns = {"__file__": GEN, "__name__": f"twin_{kind}_{rep}"}
    with contextlib.redirect_stdout(io.StringIO()):
        exec(compile(src, GEN, "exec"), ns)
    top, env = ns["TOP"], ns["env"]
    # Adapter: expose the generated model through the reference's snapshot interface.
    lines = top.Lines
    view = types.SimpleNamespace(
        sink=top.sink, source=top.source,
        lines=[types.SimpleNamespace(edges=list(line.edges.values())) for line in lines],
        entry_buffers=top.B_IN, exit_buffers=top.B_OUT,
        machines=lambda: [m for line in lines for m in line.M],
        belts=lambda: [e for line in lines for e in line.edges.values() if isinstance(e, ConveyorBelt)],
    )
    machines = view.machines()
    assert all(callable(m.processing_delay) or hasattr(m.processing_delay, "__next__") for m in machines)
    phase = "A" if kind == "dt0" else "B"
    t_end = WINDOWS[phase][1]
    rows = []
    with contextlib.redirect_stdout(io.StringIO()):
        env.process(S3_GT.sampler(env, view, rows, INTERVAL))
        env.run(until=t_end)
        rows.append(S3_GT.snapshot(view, env.now))
    return {phase: window_kpis(rows, *WINDOWS[phase], phase)}


def job(spec):
    kind, rep = spec
    return kind, rep, (run_reference(rep) if kind == "ref" else run_twin(kind, rep))


if __name__ == "__main__":
    specs = [(k, r) for r in range(REPS) for k in ("ref", "dt0", "dt1")]
    os.makedirs(RERUN, exist_ok=True)
    with Pool(int(os.environ.get("S3_WORKERS", "4"))) as pool:
        results = pool.map(job, specs)
    json.dump(results, open(OUT, "w"), indent=1, default=float)
    by = {}
    for kind, rep, res in results:
        for ph, k in res.items():
            by.setdefault((kind, ph), []).append(k)

    for ph, twin in (("A", "dt0"), ("B", "dt1")):
        obs = [k for kind, rep, res in results if kind == "ref" and rep == 0 for p, k in res.items() if p == ph][0]
        print(f"\n=== Phase {ph}: window {WINDOWS[ph][0]:g}-{WINDOWS[ph][1]:g}, "
              f"{'DT0 (phase A plugins)' if twin == 'dt0' else 'DT1 (phase B plugins)'}, {REPS} replications each ===")
        print(f"{'KPI':30s} {'observed':>10s} {'ref mean (SD)':>20s} {'DT mean (SD)':>20s} {'DT-obs %':>9s} {'DT-ref %':>9s}")
        for kpi in KPIS:
            r = np.array([k[kpi] for k in by[("ref", ph)]]); d = np.array([k[kpi] for k in by[(twin, ph)]])
            print(f"{kpi:30s} {obs[kpi]:10.4f} {r.mean():10.4f} ({r.std(ddof=1):7.4f}) "
                  f"{d.mean():10.4f} ({d.std(ddof=1):7.4f}) {100 * (d.mean() - obs[kpi]) / obs[kpi]:+8.2f}% "
                  f"{100 * (d.mean() - r.mean()) / r.mean():+8.2f}%")
