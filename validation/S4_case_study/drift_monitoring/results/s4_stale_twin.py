"""Stale twin: phase A plugins evaluated over the phase B window."""
import json
import os
from multiprocessing import Pool

import numpy as np

import twin_kpis as T

T.PLUGINS["stale"] = T.PLUGINS["dt0"]


def job(rep):
    import twin_kpis as T2
    T2.PLUGINS["stale"] = T2.PLUGINS["dt0"]
    # run_twin picks the window from the kind; force phase B for the stale twin
    orig = T2.run_twin

    def run_stale(rep):
        import contextlib, io, types, S4_GT
        from run_s4_experiment import window_kpis
        from factorysimpy.edges.converyorbelt import ConveyorBelt
        os.environ["DELAY_PLUGIN_DIR"] = T2.PLUGINS["dt0"]
        src = open(T2.GEN, encoding="utf-8").read().replace("TOP.run_simulation(10000)", "")
        ns = {"__file__": T2.GEN, "__name__": f"stale_{rep}"}
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(src, T2.GEN, "exec"), ns)
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
            env.process(S4_GT.sampler(env, view, rows, T2.INTERVAL))
            env.run(until=8000.0)
            rows.append(S4_GT.snapshot(view, env.now))
        return window_kpis(rows, *T2.WINDOWS["B"], "B")

    return run_stale(rep)


if __name__ == "__main__":
    with Pool(4) as pool:
        stale = pool.map(job, range(T.REPS))
    prev = json.load(open(T.OUT))
    out = os.path.join(os.path.dirname(T.OUT), "twin_kpis_with_stale.json")
    json.dump({"previous": prev, "stale": stale}, open(out, "w"), indent=1, default=float)

    ref = [res["B"] for kind, rep, res in prev if kind == "ref"]
    obs = [res["B"] for kind, rep, res in prev if kind == "ref" and rep == 0][0]
    dt1 = [res["B"] for kind, rep, res in prev if kind == "dt1"]
    print(f"Phase B window 4200-8000, {T.REPS} replications each")
    print(f"{'KPI':30s} {'observed':>9s} {'ref mean':>9s} {'stale DT0':>17s} {'resync DT1':>17s} {'stale%':>8s} {'DT1%':>7s}")
    for kpi in T.KPIS:
        r = np.mean([k[kpi] for k in ref]); s = np.array([k[kpi] for k in stale]); d = np.array([k[kpi] for k in dt1])
        print(f"{kpi:30s} {obs[kpi]:9.4f} {r:9.4f} {s.mean():9.4f} ({s.std(ddof=1):5.3f}) {d.mean():9.4f} ({d.std(ddof=1):5.3f}) "
              f"{100 * (s.mean() - r) / r:+7.2f}% {100 * (d.mean() - r) / r:+6.2f}%")
