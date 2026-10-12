"""E3 - Routing Policy Change (topology unchanged).

Cases: moderate (70/30 -> 50/50), large (70/30 -> 20/80), gradual (70/30 -> 40/60
linearly). The reference baseline is 70/30 at M1, so "large" is expressed as a
0.5 shift on that reference rather than a separate 90/10 baseline.

Metrics: routing detection latency, precision (not misclassified as add/remove),
recall, FP-rate.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "harness"))

import reference_model as rm
import eval_common as ec

FIELDS = ["condition", "trial", "t_change", "t_restore", "duration",
          "precision", "recall", "tp", "fp", "fn", "detection_latency",
          "detected", "target_conditions", "activated_conditions", "resolved_conditions",
          "unresolved_conditions", "resolution_latency_mean",
          "resolution_latency_max"]

GRADUAL_SPAN = 120.0


def _m1_split(rng, main_p):
    return 0 if rng.random() < main_p else 1


def mk_shift(t_change, t_restore, main_p_after):
    def factory(env, rng):
        sels = rm.baseline_selections(rng)
        def m1():
            p = (main_p_after
                 if t_change <= env.now < t_restore
                 else rm.BASELINE_M1_MAIN_P)
            return _m1_split(rng, p)
        sels[1] = m1
        return sels
    return factory


def steady_shift(main_p_after):
    def factory(env, rng):
        sels = rm.baseline_selections(rng)
        sels[1] = lambda: _m1_split(rng, main_p_after)
        return sels
    return factory


def mk_gradual(t_change, t_restore):
    def factory(env, rng):
        sels = rm.baseline_selections(rng)
        def m1():
            if env.now < t_change or env.now >= t_restore:
                p = rm.BASELINE_M1_MAIN_P
            else:
                frac = min(1.0, (env.now - t_change) / GRADUAL_SPAN)
                p = rm.BASELINE_M1_MAIN_P - (rm.BASELINE_M1_MAIN_P - 0.40) * frac
            return _m1_split(rng, p)
        sels[1] = m1
        return sels
    return factory


def run(output="eval_routing.csv"):
    print("\n=== E3 - Routing Policy Change ===")
    dt, base = ec.build_baseline()
    all_rows = []

    cases = [
        ("moderate_50_50", mk_shift, steady_shift(0.50), dict(main_p_after=0.50)),
        ("large_20_80", mk_shift, steady_shift(0.20), dict(main_p_after=0.20)),
        ("gradual_40_60", None, steady_shift(0.40), None),
    ]
    for name, mk, steady, kw in cases:
        expected = ec.compute_expected(base, steady)
        if name.startswith("gradual"):
            make = mk_gradual
        else:
            make = (lambda t, r, kw=kw: mk_shift(t, r, kw["main_p_after"]))
        rows, mets = ec.run_condition_lifecycle(name, dt, expected, make)
        ec.print_summary(name, mets)
        all_rows += rows

    path = ec.write_csv(output, all_rows, FIELDS)
    print(f"  saved -> {path}")
    return all_rows


if __name__ == "__main__":
    run()
