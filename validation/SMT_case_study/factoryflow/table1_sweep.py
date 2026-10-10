"""Reproduce Table I of the IOTAIS 2018 paper with the FactorySimPy model.

Runs the three configurations the paper sweeps - no buffering, single buffering and
double buffering, the latter two over buffer capacities N - and prints each KPI
beside the paper's own value.

Paper values come from model_2/Results/Results_{no,single,double}_buffering.csv.

Run:  python table1_sweep.py            # N = 4..128
      python table1_sweep.py --full     # N = 4..1024 (slow)
"""

import io
import os
import sys
import contextlib

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import smt_line_factorysimpy as smt

# Paper Table I, transcribed from the reference result CSVs.
# N -> (throughput, avg_cycle_h, max_cycle_h, energy_kj, off%, setup%, empty%, occupied%)
PAPER_NONE = {
    2: (40.3549, 0.7419, 1.6193, 2427.2947, 0.0000, 3.9409, 45.7966, 50.2625),
}
PAPER_SINGLE = {
    4: (40.1654, 0.8071, 1.7390, 1411.6531, 53.2935, 29.9715, 0.0, 16.7350),
    8: (40.0990, 0.9016, 1.8818, 1328.5770, 57.4324, 31.4296, 0.0, 11.1381),
    16: (34.0969, 1.2165, 2.0513, 1438.5203, 61.5351, 31.3618, 0.0, 7.1031),
    32: (30.8755, 1.6727, 2.5760, 1463.2673, 65.0024, 29.6376, 0.0, 5.3599),
    64: (30.6795, 2.3121, 3.6613, 1316.2213, 69.1861, 26.0206, 0.0, 4.7933),
    128: (32.2118, 3.3209, 5.6296, 1042.1340, 75.0700, 20.1769, 0.0, 4.7530),
    256: (34.6351, 5.0268, 9.2343, 736.7601, 82.0065, 13.0332, 0.0, 4.9603),
    512: (36.7312, 8.2388, 16.1199, 515.9988, 87.6531, 7.1661, 0.0, 5.1808),
    1024: (37.9757, 14.6724, 26.9646, 396.8215, 90.9763, 3.7086, 0.0, 5.3151),
}
PAPER_DOUBLE = {
    4: (40.3143, 0.8177, 1.7479, 1384.9001, 53.2723, 29.9744, 0.0, 16.7533),
    8: (40.3974, 0.8945, 1.6951, 1298.0702, 57.3885, 31.4344, 0.0, 11.1771),
    16: (40.5824, 1.0557, 1.6015, 1232.6166, 60.0611, 31.5288, 0.0, 8.4101),
    32: (40.9639, 1.3578, 1.8313, 1157.4086, 62.5948, 30.3384, 0.0, 7.0668),
    64: (40.9089, 1.9415, 2.6479, 1060.6819, 66.1495, 27.4590, 0.0, 6.3915),
    128: (40.3306, 3.0259, 4.4382, 910.0524, 71.8827, 22.1663, 0.0, 5.9510),
    256: (39.6756, 4.8800, 7.9235, 688.5378, 79.8635, 14.4543, 0.0, 5.6822),
    512: (39.0122, 8.1864, 14.4899, 487.6099, 86.8980, 7.5995, 0.0, 5.5025),
    1024: (37.9757, 14.6724, 26.9646, 373.1363, 90.9763, 3.7086, 0.0, 5.3151),
}

DEFAULT_N = [4, 8, 16, 32, 64, 128]
FULL_N = [4, 8, 16, 32, 64, 128, 256, 512, 1024]


def run_quiet(buffering, capacity):
    with contextlib.redirect_stdout(io.StringIO()):
        return smt.evaluate(buffering=buffering, buffer_capacity=capacity)


def row(label, paper, model):
    """One comparison line: paper value, model value, deviation."""
    keys = ("throughput", "avg_cycle_h", "max_cycle_h", "energy_kj",
            "rfo_off_pct", "rfo_setup_pct", "rfo_on_empty_pct",
            "rfo_on_occupied_pct")
    cells = []
    for paper_value, key in zip(paper, keys):
        model_value = model[key]
        cells.append(f"{paper_value:9.2f}/{model_value:<9.2f}")
    print(f"  {label:10s}" + "".join(cells))


def header():
    names = ("throughput", "cycle h", "max cyc h", "energy kJ",
             "off %", "setup %", "empty %", "occup %")
    print(f"  {'N':10s}" + "".join(f"{n:^19s}" for n in names))
    print(f"  {'':10s}" + "".join(f"{'paper/model':^19s}" for _ in names))
    print("  " + "-" * (10 + 19 * len(names)))


def main():
    capacities = FULL_N if "--full" in sys.argv else DEFAULT_N

    print("=" * 165)
    print("Table I: paper (IOTAIS 2018) / FactorySimPy model")
    print("=" * 165)

    print("\nNo buffering")
    header()
    base = run_quiet(None, None)
    row("-", PAPER_NONE[2], base)
    print(f"  energy excluding the bypass module: "
          f"{base['energy_kj_excl_module']:.2f} kJ vs paper {PAPER_NONE[2][3]:.2f}")

    for label, paper_table, buffering in (
        ("Single buffering", PAPER_SINGLE, "single"),
        ("Double buffering", PAPER_DOUBLE, "double"),
    ):
        print(f"\n{label}")
        header()
        for capacity in capacities:
            try:
                result = run_quiet(buffering, capacity)
            except Exception as exc:  # keep the sweep going and report the failure
                print(f"  N={capacity:<8d} FAILED: {type(exc).__name__}: {exc}")
                continue
            row(f"N={capacity}", paper_table[capacity], result)
            if result["finalisation_errors"]:
                print(f"{'':12s}(finalisation incomplete: "
                      f"{', '.join(result['finalisation_errors'])})")

    print("\n" + "=" * 165)
    print("Paper values: model_2/Results/Results_{no,single,double}_buffering.csv")
    print("Not swept: the reflow-oven turn-on margin k (Figure 6). That is a control")
    print("policy rather than model structure, so it is deliberately out of scope.")


if __name__ == "__main__":
    main()
