"""Generate parameter-drift monitoring configurations for S4.

Writes one file per fitting window, 100 records each, to this folder. Nothing
under ``parameter_drift/`` is touched: copy the file you want into
``parameter_drift/data/monitoring_configurations.json`` before each run.

**The two windows are separate runs, not one.** Three reasons they cannot share
a run:

* ``write_rvg_file`` names a plugin ``<entity>_<parameter>.py`` with no window
  in the name, so two configurations for the same machine overwrite each other
  and the twin silently consumes whichever fitted last.
* Twice the configurations means twice the InfluxDB queries per worker cycle,
  which perturbs detection latency - one of the quantities being measured.
* One variable per run is simply cleaner.

Nothing is lost by separating them: the model is seeded, so two runs at the same
``S4_SEED`` produce byte-identical telemetry (verified). Between runs, clear
``parameter_baselines.json``, ``parameter_drift_results.json`` and ``rvg/`` so
the second arm starts from a clean baseline.

    N = 100   the framework's current default; the 0.25 Wasserstein threshold
              sits inside this window's null distribution (99th percentile
              0.288), so false alarms run above 1%
    N = 200   null 99th percentile 0.222, below the threshold, so false alarms
              fall under 1% while the signal is unchanged

The schema matches what the drift UI writes today. ``wasserstein_threshold`` is
included for consistency but has no effect: core.py compares against the module
constant NORMALIZED_WASSERSTEIN_THRESHOLD rather than reading the key.

    python make_monitoring_configs.py
"""

import json
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_TEMPLATE = os.path.join(HERE, "s4_monitoring_configs_n{points}.json")

LINES = 10
MACHINES_PER_LINE = 10
WINDOWS = (100, 200)

# Deterministic ids so regenerating the file does not orphan baselines.
NAMESPACE = uuid.UUID("5f1b9a2e-3c4d-4e5f-8a9b-0c1d2e3f4a5b")


def machine_ids():
    for line in range(LINES):
        for col in range(MACHINES_PER_LINE):
            yield f"TOP.Line_{line:02d}.M{col:02d}"


def config_for(entity_id, points):
    return {
        "id": str(uuid.uuid5(NAMESPACE, f"{entity_id}|process_delay|{points}")),
        "enabled": True,
        "entity_id": entity_id,
        "entity_kind": "Machine",
        "entity_type": "Machine",
        "entity_tag": "entity_id",
        "entity_type_tag": "entity_type",
        "measurement": "item_events",
        "parameter": "process_delay",
        "data_type": "auto",
        # Sample-count windows: the query sorts by time descending and limits,
        # so each check fits the last `points` observations for this machine.
        "baseline_mode": "points",
        "baseline_points": points,
        "baseline_window": "-7d",
        "check_mode": "points",
        "check_points": points,
        "check_window": "-7d",
        "min_samples": points,
        "confirmations": 2,
        "interval_seconds": 30,
        "fit_method": "IM.modular_IM",
        "distribution_set": "popular",
        "selected_distribution": None,
        "selection_policy": None,
        "policy_assignment": None,
        "bin_size": 20,
        "kde_bins": 30,
        "gof_method": "ks",
        "gof_alpha": 0.01,
        "ks_alpha": 0.01,
        "mean_shift_threshold_pct": 12.0,
        "std_shift_threshold_pct": 25.0,
        "parameter_threshold": 12,
        "wasserstein_threshold": 0.25,
    }


def main():
    for points in WINDOWS:
        configs = [config_for(entity, points) for entity in machine_ids()]
        output = OUTPUT_TEMPLATE.format(points=points)
        if os.path.exists(output) and "--force" not in sys.argv:
            print(f"{os.path.basename(output)} exists; pass --force to overwrite")
            continue
        with open(output, "w", encoding="utf-8") as fh:
            json.dump(configs, fh, indent=2)
        print(f"wrote {os.path.relpath(output, HERE)}  "
              f"{len(configs)} configurations, window N={points}")

    print()
    print("One window per run. Before each arm:")
    print("  cp s4_monitoring_configs_n<N>.json "
          "../../parameter_drift/data/monitoring_configurations.json")
    print("  reset parameter_baselines.json, parameter_drift_results.json and rvg/")
    print("  run S4 with the same S4_SEED so both arms see identical telemetry")


if __name__ == "__main__":
    main()
