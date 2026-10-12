"""Shared plumbing for the eval_* experiments: baseline/DT build, a trial runner,
ground-truth computation, and CSV/summary output.
"""
import csv
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import reference_model as rm
import detector_io as dio
import metrics as mx

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "results")

# Standard timing defaults.
DURATION = 300.0
STEADY_DURATION = 600.0
T_WINDOW = (50.0, 100.0)
N_TRIALS = 20
REMOVAL_WINDOW = 60.0
CHANGE_HOLD = 120.0
POST_RESTORE = 120.0


def baseline_factory(m1_main_p=rm.BASELINE_M1_MAIN_P, qc_p=rm.BASELINE_M3_QC_P):
    """Selection factory for a pure-baseline run with configurable split / QC rate."""
    def factory(env, rng):
        return rm.baseline_selections(rng, m1_main_p=m1_main_p, qc_p=qc_p)
    return factory


def build_baseline(seed=0, duration=STEADY_DURATION, m1_main_p=rm.BASELINE_M1_MAIN_P,
                   qc_p=rm.BASELINE_M3_QC_P):
    """Run the baseline and return (dt_graph, baseline_events)."""
    events = rm.run_capture(baseline_factory(m1_main_p, qc_p), duration=duration, seed=seed)
    return dio.build_dt_graph(events), events


def compute_expected(baseline_events, steady_factory, duration=STEADY_DURATION, seed=7):
    """Derive (added, removed, routing_nodes) by diffing a steady modified run."""
    steady = rm.run_capture(steady_factory, duration=duration, seed=seed)
    return dio.expected_changes(baseline_events, steady)


def run_condition(condition, dt, expected, make_factory, *,
                  n_trials=N_TRIALS, t_window=T_WINDOW, duration=DURATION,
                  seed0=0, removal_window=REMOVAL_WINDOW, inter_arrival=rm.DEFAULT_INTER_ARRIVAL,
                  routing_threshold=0.15, min_routing_samples=10,
                  post_process=None):
    """Run n_trials of one condition; return (rows, per_trial_metrics).

    make_factory(t_change) -> selection_factory(env, rng).
    post_process(sink_evts, rng) optionally perturbs the sink stream before detection
    (used by R1); it must return the (possibly reordered) event list to feed as-is.
    """
    added, removed, routing = expected
    rows, metrics = [], []
    for i in range(n_trials):
        seed = seed0 + i
        t_rng = random.Random(9000 + seed)
        t_change = t_rng.uniform(*t_window)

        events = rm.run_capture(make_factory(t_change), duration=duration, seed=seed,
                                inter_arrival=inter_arrival)
        sink = dio.sink_entries(events)
        if post_process is not None:
            sink = post_process(sink, random.Random(5000 + seed))

        _, log = dio.run_detector(dt, sink, removal_window=removal_window,
                                  routing_threshold=routing_threshold,
                                  min_routing_samples=min_routing_samples)
        m = mx.classify(added, removed, routing, log, t_change)
        metrics.append(m)
        rows.append({
            "condition": condition,
            "trial": i + 1,
            "t_change": round(t_change, 2),
            "precision": round(m["precision"], 3),
            "recall": round(m["recall"], 3),
            "tp": m["tp"], "fp": m["fp"], "fn": m["fn"],
            "detection_latency": round(m["detection_latency"], 2) if m["detection_latency"] is not None else "",
            "detected": int(m["detected"]),
        })
    return rows, metrics


def run_condition_lifecycle(condition, dt, expected, make_factory, *,
                            n_trials=N_TRIALS, t_window=T_WINDOW,
                            change_hold=CHANGE_HOLD, post_restore=POST_RESTORE,
                            seed0=0, removal_window=REMOVAL_WINDOW,
                            routing_threshold=0.15, min_routing_samples=10):
    """Run a change, restore baseline, and measure exact-condition resolution."""
    added, removed, routing = expected
    rows, metrics = [], []
    for i in range(n_trials):
        seed = seed0 + i
        t_change = random.Random(9000 + seed).uniform(*t_window)
        t_restore = t_change + change_hold
        duration = t_restore + post_restore
        events = rm.run_capture(make_factory(t_change, t_restore),
                                duration=duration, seed=seed)
        _, log = dio.run_detector(
            dt, dio.sink_entries(events),
            removal_window=removal_window,
            routing_threshold=routing_threshold,
            min_routing_samples=min_routing_samples,
        )
        m = mx.classify(added, removed, routing, log, t_change, t_restore)
        lifecycle = mx.target_resolution_summary(
            added, removed, routing, log, t_change, t_restore
        )
        metrics.append(m)
        rows.append({
            "condition": condition,
            "trial": i + 1,
            "t_change": round(t_change, 2),
            "t_restore": round(t_restore, 2),
            "duration": round(duration, 2),
            "precision": round(m["precision"], 3),
            "recall": round(m["recall"], 3),
            "tp": m["tp"], "fp": m["fp"], "fn": m["fn"],
            "detection_latency": (
                round(m["detection_latency"], 2)
                if m["detection_latency"] is not None else ""
            ),
            "detected": int(m["detected"]),
            **{
                key: (
                    round(value, 2)
                    if key.startswith("resolution_latency") and value is not None
                    else ("" if value is None else value)
                )
                for key, value in lifecycle.items()
            },
        })
    return rows, metrics


def write_csv(filename, rows, fields=None):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, filename)
    if fields is None:
        fields = list(rows[0].keys()) if rows else []
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return path


def print_summary(condition, metrics):
    s = mx.summarise(metrics)
    lat = f"{s['latency_mean']:.1f}/{s['latency_worst']:.1f}" if s["latency_mean"] is not None else "-/-"
    print(f"  {condition:28s} recall={s['recall']:.2f} prec={s['precision']:.2f} "
          f"FP/trial={s['fp_per_trial']:.2f} FNrate={s['fn_rate']:.2f} "
          f"lat(mean/worst)={lat} detected={s['detected_frac']:.2f}")
    return s
