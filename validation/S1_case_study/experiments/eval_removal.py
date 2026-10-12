"""E2 - Edge Removal.

Cases: remove machine (M4 fully isolated), remove branch (30% M1->M4 path dies),
remove low-frequency edge (the QC edge, run against a low-QC baseline).

Metrics: removal detection latency (removal-specific), precision, recall, FN-rate,
FP-rate. Removing a branch also shifts the surviving edge to 100%, which the
detector legitimately reports as a routing change; the reported removal_latency
isolates the removed_transition alerts.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "harness"))

import reference_model as rm
import detector_io as dio
import metrics as mx
import eval_common as ec

LOW_QC = 0.03  # low-frequency QC rate for the low-frequency-edge case

FIELDS = ["condition", "trial", "t_change", "t_restore", "duration",
          "precision", "recall", "tp", "fp", "fn", "removal_latency",
          "detection_latency", "detected", "target_conditions", "activated_conditions",
          "resolved_conditions", "unresolved_conditions",
          "resolution_latency_mean", "resolution_latency_max"]


def mk_remove_machine(t_change, t_restore):
    def factory(env, rng):
        sels = rm.baseline_selections(rng)
        sels[1] = lambda: 0 if t_change <= env.now < t_restore else (0 if rng.random() < rm.BASELINE_M1_MAIN_P else 1)
        sels[3] = lambda: 0 if t_change <= env.now < t_restore else (1 if rng.random() < rm.BASELINE_M3_QC_P else 0)
        return sels
    return factory


def steady_remove_machine(env, rng):
    sels = rm.baseline_selections(rng)
    sels[1] = lambda: 0
    sels[3] = lambda: 0
    return sels


def mk_remove_branch(t_change, t_restore):
    def factory(env, rng):
        sels = rm.baseline_selections(rng)
        sels[1] = lambda: 0 if t_change <= env.now < t_restore else (0 if rng.random() < rm.BASELINE_M1_MAIN_P else 1)
        return sels
    return factory


def steady_remove_branch(env, rng):
    sels = rm.baseline_selections(rng)
    sels[1] = lambda: 0
    return sels


def mk_remove_lowfreq(t_change, t_restore):
    def factory(env, rng):
        sels = rm.baseline_selections(rng, qc_p=LOW_QC)
        sels[3] = lambda: 0 if t_change <= env.now < t_restore else (1 if rng.random() < LOW_QC else 0)
        return sels
    return factory


def steady_remove_lowfreq(env, rng):
    sels = rm.baseline_selections(rng, qc_p=LOW_QC)
    sels[3] = lambda: 0
    return sels


def run(output="eval_removal.csv"):
    print("\n=== E2 - Edge Removal ===")
    all_rows = []

    # default baseline for remove_machine / remove_branch
    dt, base = ec.build_baseline()
    for name, mk, steady in [("remove_machine", mk_remove_machine, steady_remove_machine),
                             ("remove_branch", mk_remove_branch, steady_remove_branch)]:
        rows, mets = _run_with_removal_latency(name, dt, base, mk, steady, duration=ec.DURATION)
        ec.print_summary(name, mets)
        all_rows += rows

    # dedicated low-QC baseline for the low-frequency-edge case
    dt_lf, base_lf = ec.build_baseline(qc_p=LOW_QC)
    rows, mets = _run_with_removal_latency("remove_low_freq", dt_lf, base_lf,
                                           mk_remove_lowfreq, steady_remove_lowfreq,
                                           duration=ec.DURATION)
    ec.print_summary("remove_low_freq", mets)
    all_rows += rows

    path = ec.write_csv(output, all_rows, FIELDS)
    print(f"  saved -> {path}")
    return all_rows


def _run_with_removal_latency(name, dt, base_events, mk, steady, duration):
    """Like ec.run_condition but also records the removal-specific latency."""
    import random
    expected = ec.compute_expected(base_events, steady)
    added, removed, routing = expected
    rows, mets = [], []
    for i in range(ec.N_TRIALS):
        seed = i
        t_change = random.Random(9000 + seed).uniform(*ec.T_WINDOW)
        t_restore = t_change + ec.CHANGE_HOLD
        trial_duration = t_restore + ec.POST_RESTORE
        events = rm.run_capture(mk(t_change, t_restore), duration=trial_duration, seed=seed)
        _, log = dio.run_detector(dt, dio.sink_entries(events), removal_window=ec.REMOVAL_WINDOW)
        m = mx.classify(added, removed, routing, log, t_change, t_restore)
        mets.append(m)
        rl = mx.typed_latency(log, removed, "removed_transition", t_change)
        lifecycle = mx.target_resolution_summary(
            added, removed, routing, log, t_change, t_restore
        )
        rows.append({
            "condition": name, "trial": i + 1, "t_change": round(t_change, 2),
            "t_restore": round(t_restore, 2), "duration": round(trial_duration, 2),
            "precision": round(m["precision"], 3), "recall": round(m["recall"], 3),
            "tp": m["tp"], "fp": m["fp"], "fn": m["fn"],
            "removal_latency": round(rl, 2) if rl is not None else "",
            "detection_latency": round(m["detection_latency"], 2) if m["detection_latency"] is not None else "",
            "detected": int(m["detected"]),
            **{key: (round(value, 2) if key.startswith("resolution_latency")
                     and value is not None else ("" if value is None else value))
               for key, value in lifecycle.items()},
        })
    return rows, mets


if __name__ == "__main__":
    run()
