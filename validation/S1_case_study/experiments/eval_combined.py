"""E4 - Combined Structural Drift (multiple simultaneous changes).

Cases: add + routing (new machine at M2 AND 70/30->50/50 at M1), add + remove
(new machine at M2 AND QC edge removed). Evaluates whether concurrent changes
are raised as distinct, correctly-typed conditions.
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
          "precision", "recall", "tp", "fp", "fn", "add_latency",
          "removal_latency", "routing_detected", "detection_latency", "detected",
          "target_conditions", "activated_conditions", "resolved_conditions", "unresolved_conditions",
          "resolution_latency_mean", "resolution_latency_max"]


def mk_add_routing(t, t_restore):
    def factory(env, rng):
        sels = rm.baseline_selections(rng)
        sels[2] = lambda: 1 if (t <= env.now < t_restore and rng.random() < 0.5) else 0
        sels[1] = lambda: (0 if rng.random() < 0.5 else 1) if t <= env.now < t_restore \
            else (0 if rng.random() < rm.BASELINE_M1_MAIN_P else 1)
        return sels
    return factory


def steady_add_routing(env, rng):
    sels = rm.baseline_selections(rng)
    sels[2] = lambda: 1 if rng.random() < 0.5 else 0
    sels[1] = lambda: 0 if rng.random() < 0.5 else 1
    return sels


def mk_add_remove(t, t_restore):
    def factory(env, rng):
        sels = rm.baseline_selections(rng)
        sels[2] = lambda: 1 if (t <= env.now < t_restore and rng.random() < 0.5) else 0
        sels[3] = lambda: 0 if t <= env.now < t_restore else (1 if rng.random() < rm.BASELINE_M3_QC_P else 0)
        return sels
    return factory


def steady_add_remove(env, rng):
    sels = rm.baseline_selections(rng)
    sels[2] = lambda: 1 if rng.random() < 0.5 else 0
    sels[3] = lambda: 0
    return sels


def run_case(name, dt, base, mk, steady):
    added, removed, routing = ec.compute_expected(base, steady)
    rows, mets = [], []
    for i in range(ec.N_TRIALS):
        seed = i
        t = random.Random(9000 + seed).uniform(*ec.T_WINDOW)
        t_restore = t + ec.CHANGE_HOLD
        duration = t_restore + ec.POST_RESTORE
        events = rm.run_capture(mk(t, t_restore), duration=duration, seed=seed)
        _, log = dio.run_detector(dt, dio.sink_entries(events), removal_window=ec.REMOVAL_WINDOW)
        m = mx.classify(added, removed, routing, log, t, t_restore)
        mets.append(m)
        add_lat = mx.typed_latency(log, added, "new_transition", t)
        rem_lat = mx.typed_latency(log, removed, "removed_transition", t)
        routing_ok = any(a["event"] == "active" and a["alert_type"] == "routing_policy_change"
                         and a["src"] in routing and t <= a["sim_time"] < t_restore
                         for a in log)
        lifecycle = mx.target_resolution_summary(
            added, removed, routing, log, t, t_restore
        )
        rows.append({
            "condition": name, "trial": i + 1, "t_change": round(t, 2),
            "t_restore": round(t_restore, 2), "duration": round(duration, 2),
            "precision": round(m["precision"], 3), "recall": round(m["recall"], 3),
            "tp": m["tp"], "fp": m["fp"], "fn": m["fn"],
            "add_latency": round(add_lat, 2) if add_lat is not None else "",
            "removal_latency": round(rem_lat, 2) if rem_lat is not None else "",
            "routing_detected": int(routing_ok) if routing else "",
            "detection_latency": round(m["detection_latency"], 2) if m["detection_latency"] is not None else "",
            "detected": int(m["detected"]),
            **{key: (round(value, 2) if key.startswith("resolution_latency")
                     and value is not None else ("" if value is None else value))
               for key, value in lifecycle.items()},
        })
    return rows, mets


def run(output="eval_combined.csv"):
    print("\n=== E4 - Combined Structural Drift ===")
    dt, base = ec.build_baseline()
    all_rows = []
    for name, mk, steady in [("add+routing", mk_add_routing, steady_add_routing),
                             ("add+remove", mk_add_remove, steady_add_remove)]:
        rows, mets = run_case(name, dt, base, mk, steady)
        ec.print_summary(name, mets)
        all_rows += rows
    path = ec.write_csv(output, all_rows, FIELDS)
    print(f"  saved -> {path}")
    return all_rows


if __name__ == "__main__":
    run()
