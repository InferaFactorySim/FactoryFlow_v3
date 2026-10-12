"""Print Tables 10 and 12 (S3 reference and Digital Twin KPIs) from the run results.

Usage, from this folder:
    python make_tables_10_12.py           # the published runs in this folder
    python make_tables_10_12.py rerun     # a rerun, written to rerun/ by
                                          # s3_twin_kpis.py, s3_stale_twin.py and
                                          # s3_twin_kpis_phaseC.py
"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "rerun") if sys.argv[1:] == ["rerun"] else HERE

ROWS = [  # (label, KPI key, decimals of the reference mean, decimals of its SD)
    ("Throughput", "throughput_items_per_unit", 4, 4),
    ("Mean cycle time", "mean_cycle_time", 3, 3),
    ("Queue occupancy", "mean_queue_occupancy_items", 3, 3),
    ("Energy/item (kJ)", "energy_per_item_kJ", 2, 2),
]


def load(name):
    with open(os.path.join(SOURCE, name), encoding="utf-8") as fh:
        return json.load(fh)


ab = load("s3_twin_kpis_phaseAB.json")
c = load("s3_twin_kpis_phaseC.json")
runs = {
    ("A", "ref"): [res["A"] for kind, rep, res in ab["previous"] if kind == "ref"],
    ("A", "DT0"): [res["A"] for kind, rep, res in ab["previous"] if kind == "dt0"],
    ("B", "ref"): [res["B"] for kind, rep, res in ab["previous"] if kind == "ref"],
    ("B", "Stale"): ab["stale"],
    ("B", "DT1"): [res["B"] for kind, rep, res in ab["previous"] if kind == "dt1"],
    ("C", "ref"): [res for kind, rep, res in c if kind == "ref"],
    ("C", "Stale"): [res for kind, rep, res in c if kind == "stale"],
    ("C", "DT2"): [res for kind, rep, res in c if kind == "dt2"],
}
counts = {key: len(value) for key, value in runs.items()}
print(f"source: {os.path.relpath(SOURCE, HERE)}; runs per column: "
      + ", ".join(f"{p} {k} {n}" for (p, k), n in counts.items()))


def ref(phase, key):
    values = np.array([r[key] for r in runs[(phase, "ref")]])
    return values.mean(), values.std(ddof=1)


def rel(phase, twin, key):
    twin_mean = np.mean([r[key] for r in runs[(phase, twin)]])
    return 100 * (twin_mean - ref(phase, key)[0]) / ref(phase, key)[0]


print("\nTable 10: S3 reference system in each phase (mean of the runs)")
print(f"{'KPI':18s} {'A':>10s} {'B':>10s} {'C':>10s}")
for label, key, d, _ in ROWS:
    print(f"{label:18s} " + " ".join(f"{ref(p, key)[0]:10.{d}f}" for p in "ABC"))

print("\nTable 12: reference mean (SD); twins as relative difference from the reference mean")
columns = [("A", None), ("A", "DT0"), ("B", None), ("B", "Stale"), ("B", "DT1"),
           ("C", None), ("C", "Stale"), ("C", "DT2")]
print(f"{'KPI':18s} " + " ".join(f"{p + ' ' + (t or 'ref'):>17s}" for p, t in columns))
for label, key, d, s in ROWS:
    cells = []
    for phase, twin in columns:
        if twin is None:
            mean, sd = ref(phase, key)
            cells.append(f"{mean:.{d}f} ({sd:.{s}f})")
        else:
            cells.append(f"{rel(phase, twin, key):+.2f}%")
    print(f"{label:18s} " + " ".join(f"{cell:>17s}" for cell in cells))
