"""E5 - Complete Independent Path Addition and Withdrawal.

Temporarily activate the dormant one-machine line:

    src_new -> b_new1 -> M_new -> b_new2 -> sink_new

The line is absent from the observed DT baseline. Its first completed item must
activate all four NEW_TRANSITION conditions; after generation stops, continued
traffic at the original sink advances the shared watermark and must resolve all
four conditions after the removal window.
"""
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "harness"))

import detector_io as dio
import eval_common as ec
import metrics as mx
import reference_model as rm


EXPECTED_TRANSITIONS = {
    ("TOP.src_new", "TOP.b_new1"),
    ("TOP.b_new1", "TOP.M_new"),
    ("TOP.M_new", "TOP.b_new2"),
    ("TOP.b_new2", "TOP.sink_new"),
}

FIELDS = [
    "condition", "trial", "t_change", "t_restore", "duration",
    "expected_transitions", "activated_conditions", "resolved_conditions",
    "unresolved_conditions", "addition_detected", "withdrawal_detected",
    "precision", "recall", "tp", "fp", "fn", "detection_latency",
    "resolution_latency_mean", "resolution_latency_max",
]


def run(output="eval_independent_path.csv", n_trials=ec.N_TRIALS):
    print("\n=== E5 - Complete Independent Path Addition and Withdrawal ===")
    dt, _baseline_events = ec.build_baseline()
    rows = []

    for trial_index in range(n_trials):
        seed = trial_index
        t_change = random.Random(12000 + seed).uniform(*ec.T_WINDOW)
        t_restore = t_change + 80.0
        duration = t_restore + ec.REMOVAL_WINDOW + 80.0

        def independent_factory(env, rng, start=t_change, stop=t_restore):
            return rm.temporary_independent_arrival(
                env, start, stop, interval=rm.DEFAULT_INTER_ARRIVAL
            )

        events = rm.run_capture(
            rm.baseline_factory,
            duration=duration,
            seed=seed,
            independent_arrival_factory=independent_factory,
        )
        _detector, log = dio.run_multi_sink_detector(
            dt,
            dio.sink_entries(events),
            removal_window=ec.REMOVAL_WINDOW,
            # Isolate the structural lifecycle under test. Routing sensitivity
            # is evaluated independently by E3 and the sensitivity sweep and can otherwise add random
            # branch alerts from the unchanged main line to this experiment.
            routing_threshold=1.0,
        )

        classification = mx.classify(
            EXPECTED_TRANSITIONS, set(), set(), log, t_change, t_restore
        )
        lifecycle = mx.target_resolution_summary(
            EXPECTED_TRANSITIONS, set(), set(), log, t_change, t_restore
        )
        addition_detected = lifecycle["activated_conditions"] == len(EXPECTED_TRANSITIONS)
        withdrawal_detected = (
            lifecycle["resolved_conditions"] == len(EXPECTED_TRANSITIONS)
            and lifecycle["unresolved_conditions"] == 0
        )

        rows.append({
            "condition": "independent_path_addition_withdrawal",
            "trial": trial_index + 1,
            "t_change": round(t_change, 2),
            "t_restore": round(t_restore, 2),
            "duration": round(duration, 2),
            "expected_transitions": len(EXPECTED_TRANSITIONS),
            "activated_conditions": lifecycle["activated_conditions"],
            "resolved_conditions": lifecycle["resolved_conditions"],
            "unresolved_conditions": lifecycle["unresolved_conditions"],
            "addition_detected": int(addition_detected),
            "withdrawal_detected": int(withdrawal_detected),
            "precision": round(classification["precision"], 3),
            "recall": round(classification["recall"], 3),
            "tp": classification["tp"],
            "fp": classification["fp"],
            "fn": classification["fn"],
            "detection_latency": (
                round(classification["detection_latency"], 2)
                if classification["detection_latency"] is not None else ""
            ),
            "resolution_latency_mean": (
                round(lifecycle["resolution_latency_mean"], 2)
                if lifecycle["resolution_latency_mean"] is not None else ""
            ),
            "resolution_latency_max": (
                round(lifecycle["resolution_latency_max"], 2)
                if lifecycle["resolution_latency_max"] is not None else ""
            ),
        })

    path = ec.write_csv(output, rows, FIELDS)
    additions = sum(row["addition_detected"] for row in rows)
    withdrawals = sum(row["withdrawal_detected"] for row in rows)
    print(f"  additions detected:  {additions}/{n_trials}")
    print(f"  withdrawals detected: {withdrawals}/{n_trials}")
    print(f"  saved -> {path}")

    if additions != n_trials or withdrawals != n_trials:
        raise AssertionError(
            f"Independent path lifecycle failed: additions={additions}/{n_trials}, "
            f"withdrawals={withdrawals}/{n_trials}"
        )
    return rows


if __name__ == "__main__":
    run()
