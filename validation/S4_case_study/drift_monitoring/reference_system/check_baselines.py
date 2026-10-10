"""Compare DataFITR's fitted baselines against S4's ground-truth distributions.

Run once the baselines exist (about 750 time units into a run at N=100, or
1,500 at N=200). Before the parameter change at t=2,500 every machine is still
on its phase A distribution, so each column has one correct answer.

    python check_baselines.py

Reports, per column: how often the family was recovered, and the error in the
fitted mean and standard deviation against the true ones.
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from S4_GT import COLUMN_DISTRIBUTIONS, phase_distribution  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
BASELINES = os.path.join(ROOT, "parameter_drift", "data", "parameter_baselines.json")
CONFIGS = os.path.join(ROOT, "parameter_drift", "data", "monitoring_configurations.json")

# DataFITR's family names against scipy's, where they differ.
ALIASES = {
    "norm": "normal",
    "lognorm": "lognormal",
    "weibull_min": "weibull",
    "expon": "exponential",
    "triang": "triangular",
}


def true_moments(column, phase=1):
    _, dist = phase_distribution(phase, column)
    m, v = dist.stats("mv")
    return float(m), math.sqrt(float(v))


def main():
    if not os.path.exists(BASELINES):
        raise SystemExit(f"no baselines yet: {BASELINES}")
    baselines = json.load(open(BASELINES, encoding="utf-8"))
    configs = {c["id"]: c for c in json.load(open(CONFIGS, encoding="utf-8"))}
    if not baselines:
        raise SystemExit("baselines file is empty - the window has not filled yet")

    window = sorted({c["check_points"] for c in configs.values()})
    print(f"{len(baselines)} baselines, fitting window N={window[0] if len(window)==1 else window}\n")

    rows = {}
    for cfg_id, fit in baselines.items():
        cfg = configs.get(cfg_id)
        if not cfg:
            continue
        entity = cfg["entity_id"]                      # TOP.Line_LL.MCC
        column = int(entity.rsplit(".M", 1)[1])
        rows.setdefault(column, []).append(fit)

    print(f"{'col':<5}{'true family':<13}{'true mean':>10}{'true sd':>9}"
          f"{'  family OK':>12}{'fitted mean err':>17}{'fitted sd err':>15}")
    fam_total = fam_ok = 0
    for column in sorted(rows):
        true_fam = COLUMN_DISTRIBUTIONS[column][0]
        tm, tsd = true_moments(column)
        hits, merrs, serrs, fitted_names = 0, [], [], {}
        for fit in rows[column]:
            name = ALIASES.get(fit.get("best_distribution"), fit.get("best_distribution"))
            fitted_names[name] = fitted_names.get(name, 0) + 1
            if name == true_fam:
                hits += 1
            if fit.get("mean") is not None:
                merrs.append(abs(fit["mean"] - tm) / tm * 100)
            if fit.get("std") is not None and tsd:
                serrs.append(abs(fit["std"] - tsd) / tsd * 100)
        n = len(rows[column])
        fam_total += n
        fam_ok += hits
        mavg = sum(merrs) / len(merrs) if merrs else float("nan")
        savg = sum(serrs) / len(serrs) if serrs else float("nan")
        print(f"C{column:<4}{true_fam:<13}{tm:>10.3f}{tsd:>9.3f}"
              f"{hits:>8}/{n:<3}{mavg:>16.2f}%{savg:>14.2f}%")
        if hits < n:
            other = ", ".join(f"{k}x{v}" for k, v in sorted(fitted_names.items())
                              if k != true_fam)
            print(f"{'':<5}fitted instead: {other}")

    print()
    print(f"family recovered: {fam_ok}/{fam_total} ({100*fam_ok/fam_total:.0f}%)")
    print()
    print("A family miss is only meaningful where the families are separable at this")
    print("sample size. C2 gamma and C8 lognormal differ by KS 0.052, against a critical")
    print("value of 0.163 at N=100 - no test can tell them apart from 100 values, so")
    print("confusion between those two is expected rather than a fitting failure.")


if __name__ == "__main__":
    main()
