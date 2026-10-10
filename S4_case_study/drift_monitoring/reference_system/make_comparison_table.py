"""Build the reference-vs-digital-twin KPI table for the S4 experiment.

Reads the reference windows produced by ``run_s4_experiment.py`` and, when it
exists, the twin's windows in the same format, then writes a CSV and the LaTeX
table used in the paper. Regenerate it whenever either side changes so the
manuscript numbers cannot drift away from the recorded results.

    python make_comparison_table.py [window_definitions.csv]

Reference : results/s4_window_kpis.csv
Twin      : results/s4_dt_window_kpis.csv   (optional, same columns)
Outputs   : results/s4_comparison.csv
            results/s4_comparison.tex

Windows default to the three phases. To compare over different windows - for
instance the divergence gap between a change and the twin's redeployment - pass
a CSV of ``window,from,to`` and the reference side is recomputed from
``s4_timeseries.csv`` for exactly those intervals.
"""

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")

REFERENCE = os.path.join(RESULTS, "s4_window_kpis.csv")
TIMESERIES = os.path.join(RESULTS, "s4_timeseries.csv")
TWIN = os.path.join(RESULTS, "s4_dt_window_kpis.csv")

KPIS = [
    ("throughput_items_per_unit", "Throughput", "{:.4f}"),
    ("mean_cycle_time", "Mean cycle time", "{:.3f}"),
    ("max_cycle_time", "Maximum cycle time", "{:.2f}"),
    ("mean_machine_utilisation_pct", "Mean utilisation (\\%)", "{:.2f}"),
    ("mean_queue_occupancy_items", "Queue occupancy (items)", "{:.3f}"),
    ("energy_per_item_kJ", "Energy per item (kJ)", "{:.2f}"),
]

LABELS = {
    "phase_A_baseline": "A: baseline",
    "phase_B_parameter_change": "B: parameter change",
    "phase_C_routing_change": "C: routing change",
}


def load_windows(path):
    if not os.path.exists(path):
        return None
    return {r["window"]: r for r in csv.DictReader(open(path, encoding="utf-8"))}


def recompute(window_defs):
    """Recompute reference KPIs over arbitrary windows from the timeseries."""
    from run_s4_experiment import window_kpis
    rows = []
    for r in csv.DictReader(open(TIMESERIES, encoding="utf-8")):
        rows.append({k: float(v) for k, v in r.items()})
    out = {}
    for name, t0, t1 in window_defs:
        kpi = window_kpis(rows, t0, t1, name)
        if kpi:
            out[name] = {k: str(v) for k, v in kpi.items()}
    return out


def main():
    if len(sys.argv) > 1:
        defs = [(r["window"], float(r["from"]), float(r["to"]))
                for r in csv.DictReader(open(sys.argv[1], encoding="utf-8"))]
        ref = recompute(defs)
        order = [name for name, _, _ in defs]
        print(f"recomputed reference over {len(order)} custom windows")
    else:
        ref = load_windows(REFERENCE)
        if ref is None:
            raise SystemExit(f"reference windows not found: {REFERENCE}")
        order = list(ref)

    twin = load_windows(TWIN)
    if twin is None:
        print(f"note: {os.path.basename(TWIN)} not present - "
              "reference columns only, twin columns left pending")

    def val(table, window, key):
        row = (table or {}).get(window)
        if not row or row.get(key) in (None, ""):
            return None
        return float(row[key])

    csv_path = os.path.join(RESULTS, "s4_comparison.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["window", "kpi", "reference", "digital_twin", "relative_difference_pct"])
        for window in order:
            for key, _, _ in KPIS:
                r, d = val(ref, window, key), val(twin, window, key)
                rel = 100.0 * abs(d - r) / abs(r) if (d is not None and r) else ""
                w.writerow([window, key, r, "" if d is None else d, rel])
    print(f"wrote {os.path.relpath(csv_path, HERE)}")

    def cell(v, fmt):
        return fmt.format(v) if v is not None else "---"

    lines = [
        "\\begin{table*}[!t]",
        "\\centering",
        "\\caption{S4 across the three phases of one continuous run: reference system "
        "against the Digital Twin. Phase~A is the baseline, phase~B follows a change to "
        "three processing-delay distributions, and phase~C follows a routing change that "
        "stops work reaching line~00. Neither change is announced to the framework.}",
        "\\label{tab:s4_three_phase_comparison}",
        "\\footnotesize",
        "\\setlength{\\tabcolsep}{4pt}",
        "\\begin{tabular}{@{}l" + "rrr" * len(order) + "@{}}",
        "\\toprule",
        " & " + " & ".join(
            f"\\multicolumn{{3}}{{c}}{{{LABELS.get(w, w)}}}" for w in order) + " \\\\",
        " ".join(f"\\cmidrule(lr){{{2 + 3 * i}-{4 + 3 * i}}}" for i in range(len(order))),
        "KPI & " + " & ".join("Ref. & DT & Diff.\\ (\\%)" for _ in order) + " \\\\",
        "\\midrule",
    ]
    for key, label, fmt in KPIS:
        cells = []
        for window in order:
            r, d = val(ref, window, key), val(twin, window, key)
            rel = 100.0 * abs(d - r) / abs(r) if (d is not None and r) else None
            cells += [cell(r, fmt), cell(d, fmt), cell(rel, "{:.2f}")]
        lines.append(f"{label} & " + " & ".join(cells) + " \\\\")
    lines += ["\\bottomrule", "\\end{tabular}", "\\end{table*}", ""]

    tex_path = os.path.join(RESULTS, "s4_comparison.tex")
    open(tex_path, "w", encoding="utf-8").write("\n".join(lines))
    print(f"wrote {os.path.relpath(tex_path, HERE)}")

    print()
    for window in order:
        print(LABELS.get(window, window))
        for key, label, fmt in KPIS:
            r, d = val(ref, window, key), val(twin, window, key)
            rel = f"{100.0 * abs(d - r) / abs(r):7.2f}%" if (d is not None and r) else "      --"
            print(f"   {label:<26}{cell(r, fmt):>12}{cell(d, fmt):>12}{rel}")
        print()


if __name__ == "__main__":
    main()
