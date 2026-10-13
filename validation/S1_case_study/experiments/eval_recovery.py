"""E6 - Recovery & Alert Lifecycle.

Cases: permanent recovery (remove the 30% branch, detect, then restore) and
intermittent recovery (branch repeatedly toggled). Evaluates the resolve half of
the lifecycle and quantifies flapping.

Metrics: resolution latency, alert stability (active/resolved transition counts),
residual FP after recovery.
"""
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "harness"))

import reference_model as rm
import detector_io as dio
import metrics as mx
import eval_common as ec

REMOVED_EDGE = ("TOP.M1", "TOP.b_M1_M4")  # the branch edge we track for stability

FIELDS = ["condition", "trial", "detect_latency", "resolution_latency",
          "n_active", "n_resolved", "residual_fp"]


def run_permanent(dt, base):
    """Remove the branch over [t1, t2], then restore; measure resolve + stability."""
    added, removed, routing = ec.compute_expected(
        base, lambda env, rng: _restore_never_selection(rng))
    rows, mets = [], []
    for i in range(ec.N_TRIALS):
        seed = i
        t1 = random.Random(9000 + seed).uniform(*ec.T_WINDOW)
        t2 = t1 + 120.0
        duration = t2 + 150.0

        def factory(env, rng, t1=t1, t2=t2):
            sels = rm.baseline_selections(rng)
            def m1():
                if t1 <= env.now < t2:
                    return 0  # branch removed
                return 0 if rng.random() < rm.BASELINE_M1_MAIN_P else 1
            sels[1] = m1
            return sels

        events = rm.run_capture(factory, duration=duration, seed=seed)
        _, log = dio.run_detector(dt, dio.sink_entries(events), removal_window=ec.REMOVAL_WINDOW)

        detect_lat = mx.typed_latency(log, removed, "removed_transition", t1)
        res_lat = mx.resolution_latency(log, t2, alert_type="removed_transition")
        n_active, n_resolved = mx.alert_stability(log, alert_type="removed_transition",
                                                  src=REMOVED_EDGE[0], dst=REMOVED_EDGE[1])
        # residual FP: active alerts still standing well after restore
        residual = sum(1 for a in log if a["event"] == "active" and a["sim_time"] > t2 + 90)
        rows.append({
            "condition": "permanent_recovery", "trial": i + 1,
            "detect_latency": round(detect_lat, 2) if detect_lat is not None else "",
            "resolution_latency": round(res_lat, 2) if res_lat is not None else "",
            "n_active": n_active, "n_resolved": n_resolved, "residual_fp": residual,
        })
    return rows


def _restore_never_selection(rng):
    """Steady 'branch removed' program for ground-truth derivation."""
    sels = rm.baseline_selections(rng)
    sels[1] = lambda: 0
    return sels


def run_intermittent(dt, base):
    """Toggle the branch on/off in cycles; measure flapping (active/resolved counts)."""
    rows = []
    period = 200.0     # full on/off cycle; off-half (100) exceeds the 60u window
    n_cycles = 4
    for i in range(ec.N_TRIALS):
        seed = i
        t_start = 60.0
        duration = t_start + n_cycles * period + 60.0

        def factory(env, rng, t_start=t_start, period=period):
            sels = rm.baseline_selections(rng)
            def m1():
                if env.now >= t_start:
                    phase = int((env.now - t_start) // (period / 2))
                    if phase % 2 == 1:   # "off" half -> branch removed
                        return 0
                return 0 if rng.random() < rm.BASELINE_M1_MAIN_P else 1
            sels[1] = m1
            return sels

        events = rm.run_capture(factory, duration=duration, seed=seed)
        _, log = dio.run_detector(dt, dio.sink_entries(events), removal_window=ec.REMOVAL_WINDOW)
        n_active, n_resolved = mx.alert_stability(log, alert_type="removed_transition",
                                                  src=REMOVED_EDGE[0], dst=REMOVED_EDGE[1])
        rows.append({
            "condition": "intermittent_recovery", "trial": i + 1,
            "detect_latency": "", "resolution_latency": "",
            "n_active": n_active, "n_resolved": n_resolved, "residual_fp": "",
        })
    return rows


def run(output="eval_recovery.csv"):
    print("\n=== E6 - Recovery & Alert Lifecycle ===")
    dt, base = ec.build_baseline()

    perm = run_permanent(dt, base)
    res = [r["resolution_latency"] for r in perm if r["resolution_latency"] != ""]
    act = [r["n_active"] for r in perm]
    print(f"  permanent_recovery          resolved={len(res)}/{len(perm)} "
          f"mean_res_lat={sum(res)/len(res):.1f} " if res else "  permanent_recovery  no resolutions ",
          f"mean_active={sum(act)/len(act):.2f}")

    interm = run_intermittent(dt, base)
    na = [r["n_active"] for r in interm]
    nr = [r["n_resolved"] for r in interm]
    print(f"  intermittent_recovery       mean_active(flaps)={sum(na)/len(na):.2f} "
          f"mean_resolved={sum(nr)/len(nr):.2f}")

    all_rows = perm + interm
    path = ec.write_csv(output, all_rows, FIELDS)
    print(f"  saved -> {path}")
    return all_rows


if __name__ == "__main__":
    run()
