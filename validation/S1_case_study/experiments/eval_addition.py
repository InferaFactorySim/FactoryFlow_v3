"""E1 - Edge Addition.

Cases: add machine (M2 -> M6 -> M3), add bypass (M0 -> M5, original kept),
temporary addition (enable, then restore).

Metrics: detection latency, precision, recall, FP-rate; resolution latency (temporary).
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

FIELDS = ["condition", "trial", "t_change", "t_restore", "duration",
          "precision", "recall", "tp", "fp", "fn", "detection_latency",
          "detected", "target_conditions", "activated_conditions", "resolved_conditions",
          "unresolved_conditions", "resolution_latency_mean",
          "resolution_latency_max"]


# ── change programs ───────────────────────────────────────────────────────────

def mk_add_machine(t_change, t_restore=None):
    def factory(env, rng):
        sels = rm.baseline_selections(rng)
        def m2():
            if env.now >= t_change and (t_restore is None or env.now < t_restore) \
                    and rng.random() < 0.5:
                return 1   # M2 -> M6 (new machine)
            return 0
        sels[2] = m2
        return sels
    return factory


def steady_add_machine(env, rng):
    sels = rm.baseline_selections(rng)
    sels[2] = lambda: 1 if rng.random() < 0.5 else 0
    return sels


def mk_add_bypass(t_change, t_restore=None):
    def factory(env, rng):
        sels = rm.baseline_selections(rng)
        def m0():
            if env.now >= t_change and (t_restore is None or env.now < t_restore) \
                    and rng.random() < 0.3:
                return 1   # M0 -> M5 (bypass)
            return 0
        sels[0] = m0
        return sels
    return factory


def steady_add_bypass(env, rng):
    sels = rm.baseline_selections(rng)
    sels[0] = lambda: 1 if rng.random() < 0.3 else 0
    return sels


def run_temporary(dt, baseline_events):
    """Enable the new machine over [t1, t2], then restore; measure resolution."""
    added, removed, routing = ec.compute_expected(baseline_events, steady_add_machine)
    rows, mets = [], []
    for i in range(ec.N_TRIALS):
        seed = i
        t1 = random.Random(9000 + seed).uniform(*ec.T_WINDOW)
        t2 = t1 + 80.0
        duration = t2 + 120.0

        def factory(env, rng, t1=t1, t2=t2):
            sels = rm.baseline_selections(rng)
            def m2():
                if t1 <= env.now < t2 and rng.random() < 0.5:
                    return 1
                return 0
            sels[2] = m2
            return sels

        events = rm.run_capture(factory, duration=duration, seed=seed)
        _, log = dio.run_detector(dt, dio.sink_entries(events), removal_window=ec.REMOVAL_WINDOW)
        m = mx.classify(added, removed, routing, log, t1, t2)
        mets.append(m)
        lifecycle = mx.target_resolution_summary(added, removed, routing, log, t1, t2)
        rows.append({
            "condition": "temporary_addition", "trial": i + 1, "t_change": round(t1, 2),
            "t_restore": round(t2, 2), "duration": round(duration, 2),
            "precision": round(m["precision"], 3), "recall": round(m["recall"], 3),
            "tp": m["tp"], "fp": m["fp"], "fn": m["fn"],
            "detection_latency": round(m["detection_latency"], 2) if m["detection_latency"] is not None else "",
            "detected": int(m["detected"]),
            **{key: (round(value, 2) if key.startswith("resolution_latency")
                     and value is not None else ("" if value is None else value))
               for key, value in lifecycle.items()},
        })
    return rows, mets


def run(output="eval_addition.csv"):
    print("\n=== E1 - Edge Addition ===")
    dt, base = ec.build_baseline()
    all_rows = []

    for name, mk, steady in [("add_machine", mk_add_machine, steady_add_machine),
                             ("add_bypass", mk_add_bypass, steady_add_bypass)]:
        expected = ec.compute_expected(base, steady)
        rows, mets = ec.run_condition_lifecycle(name, dt, expected, mk)
        ec.print_summary(name, mets)
        all_rows += rows

    temp_rows, temp_mets = run_temporary(dt, base)
    ec.print_summary("temporary_addition", temp_mets)
    all_rows += temp_rows

    path = ec.write_csv(output, all_rows, FIELDS)
    print(f"  saved -> {path}")
    return all_rows


if __name__ == "__main__":
    run()
