"""Recompute every row of Table 7 (structural-change detection on S1) from the
per-trial result files.

Run:  python summarise_table7.py           # the published trials, raw_trials/
      python summarise_table7.py rerun     # a rerun of the experiments, written to
                                           # results/ by experiments/eval_*.py
"""

import csv
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = HERE if sys.argv[1:] == ["rerun"] else os.path.join(HERE, "raw_trials")

# (paper label, result file, condition name in that file)
CASES = [
    ("E1 Addition: add machine", "eval_addition.csv", "add_machine"),
    ("E1 Addition: add bypass", "eval_addition.csv", "add_bypass"),
    ("E1 Addition: temporary addition", "eval_addition.csv", "temporary_addition"),
    ("E2 Removal: remove machine", "eval_removal.csv", "remove_machine"),
    ("E2 Removal: remove branch", "eval_removal.csv", "remove_branch"),
    ("E2 Removal: 3% route", "eval_removal.csv", "remove_low_freq"),
    ("E3 Routing: 70/30 to 50/50", "eval_routing.csv", "moderate_50_50"),
    ("E3 Routing: 70/30 to 20/80", "eval_routing.csv", "large_20_80"),
    ("E3 Routing: gradual to 40/60", "eval_routing.csv", "gradual_40_60"),
    ("E4 Combined: addition plus routing", "eval_combined.csv", "add+routing"),
    ("E4 Combined: addition plus removal", "eval_combined.csv", "add+remove"),
    ("E7 Independent path", "eval_independent_path.csv",
     "independent_path_addition_withdrawal"),
]


def read(name):
    with open(os.path.join(RAW, name), newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def values(rows, key):
    return [float(r[key]) for r in rows if r.get(key) not in (None, "", "nan", "None")]


def main():
    rows = read("eval_no_change.csv")
    print("E0 No change: {}/{} trials raised alerts; {} activations ({} routing), "
          "{} resolved".format(
              sum(int(r["total_active"]) > 0 for r in rows), len(rows),
              sum(int(r["total_active"]) for r in rows),
              sum(int(r["routing_policy_change_active"]) for r in rows),
              sum(int(r["total_resolved"]) for r in rows)))

    for label, name, condition in CASES:
        trials = [r for r in read(name) if r["condition"] == condition]
        tp = sum(values(trials, "tp"))
        target = sum(values(trials, "target_conditions")
                     or values(trials, "expected_transitions"))
        detect = values(trials, "detection_latency")
        resolve_mean = values(trials, "resolution_latency_mean")
        resolve_max = values(trials, "resolution_latency_max")
        print("{:38s} detected {:.0f}/{:.0f}  recall {:.2f}  precision {:.2f}  "
              "latency {:.1f}/{:.1f}  resolved {:.0f}/{:.0f}  resolution {:.1f}/{:.1f}".format(
                  label, tp, target, tp / target,
                  statistics.mean(values(trials, "precision")),
                  statistics.mean(detect), max(detect),
                  sum(values(trials, "resolved_conditions")),
                  sum(values(trials, "activated_conditions")),
                  statistics.mean(resolve_mean), max(resolve_max)))

    rows = read("eval_recovery.csv")
    for label, condition in (("E5 Recovery: restored branch", "permanent_recovery"),
                             ("E5 Recovery: four on/off cycles", "intermittent_recovery")):
        trials = [r for r in rows if r["condition"] == condition]
        latency = values(trials, "resolution_latency")
        text = ("  resolution {:.1f}/{:.1f}".format(statistics.mean(latency), max(latency))
                if latency else "")
        print("{:38s} resolved {}/{}{}".format(
            label, sum(int(r["n_resolved"]) for r in trials),
            sum(int(r["n_active"]) for r in trials), text))


if __name__ == "__main__":
    main()
