"""E0 - Unchanged-baseline alert check.

Runs the S1 baseline without an introduced change and records every detector
alert over the complete simulation horizon.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "harness"))

import detector_io as dio
import eval_common as ec
import reference_model as rm


FIELDS = [
    "condition", "trial", "seed", "duration", "total_active", "total_resolved",
    "new_transition_active", "removed_transition_active",
    "routing_policy_change_active", "unique_active_conditions", "first_alert_time",
]


def run(output="eval_no_change.csv"):
    dt, _ = ec.build_baseline()
    rows = []

    for seed in range(ec.N_TRIALS):
        events = rm.run_capture(
            ec.baseline_factory(), duration=ec.DURATION, seed=seed
        )
        _, log = dio.run_detector(
            dt,
            dio.sink_entries(events),
            removal_window=ec.REMOVAL_WINDOW,
            routing_threshold=0.15,
            min_routing_samples=10,
        )

        active = [alert for alert in log if alert["event"] == "active"]
        resolved = [alert for alert in log if alert["event"] == "resolved"]
        unique = {
            (alert["alert_type"], alert["src"], alert["dst"])
            for alert in active
        }

        rows.append({
            "condition": "unchanged_baseline",
            "trial": seed + 1,
            "seed": seed,
            "duration": ec.DURATION,
            "total_active": len(active),
            "total_resolved": len(resolved),
            "new_transition_active": sum(
                alert["alert_type"] == "new_transition" for alert in active
            ),
            "removed_transition_active": sum(
                alert["alert_type"] == "removed_transition" for alert in active
            ),
            "routing_policy_change_active": sum(
                alert["alert_type"] == "routing_policy_change" for alert in active
            ),
            "unique_active_conditions": len(unique),
            "first_alert_time": (
                round(min(alert["sim_time"] for alert in active), 2) if active else ""
            ),
        })

    path = ec.write_csv(output, rows, FIELDS)
    total_active = sum(row["total_active"] for row in rows)
    trials_with_alerts = sum(row["total_active"] > 0 for row in rows)
    print(
        f"E0 unchanged baseline: {trials_with_alerts}/{len(rows)} trials with alerts; "
        f"{total_active} active alerts across {len(rows) * ec.DURATION:.0f} "
        f"simulation-time units"
    )
    print(f"saved -> {path}")
    return rows


if __name__ == "__main__":
    run()
