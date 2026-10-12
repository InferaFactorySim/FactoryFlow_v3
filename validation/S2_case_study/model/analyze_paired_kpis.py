"""Create a paired Model A KPI precision analysis without modifying run files."""

import argparse
import csv
import math
from pathlib import Path

from scipy.stats import t


METRICS = (
    "Throughput (items/time unit)",
    "Mean cycle time (time units)",
)


def read_rows(path):
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def paired_statistics(reference, matched, metric, n):
    reference_values = [float(row[metric]) for row in reference[:n]]
    matched_values = [float(row[metric]) for row in matched[:n]]
    differences = [
        matched_value - reference_value
        for reference_value, matched_value in zip(reference_values, matched_values)
    ]
    reference_mean = sum(reference_values) / n
    matched_mean = sum(matched_values) / n
    difference_mean = sum(differences) / n
    difference_sd = math.sqrt(
        sum((value - difference_mean) ** 2 for value in differences) / (n - 1)
    )
    t_critical = float(t.ppf(0.975, n - 1))
    half_width = t_critical * difference_sd / math.sqrt(n)
    relative_half_width = 100.0 * half_width / abs(reference_mean)
    return {
        "metric": metric,
        "n": n,
        "reference_mean": reference_mean,
        "matched_mean": matched_mean,
        "mean_paired_difference": difference_mean,
        "paired_difference_sd": difference_sd,
        "t_critical_95pct": t_critical,
        "ci_lower": difference_mean - half_width,
        "ci_upper": difference_mean + half_width,
        "ci_half_width": half_width,
        "relative_half_width_pct": relative_half_width,
    }


def first_passing_n(reference, matched, metric, tolerance_pct, minimum_n=21):
    for n in range(minimum_n, len(reference) + 1):
        stats = paired_statistics(reference, matched, metric, n)
        if stats["relative_half_width_pct"] < tolerance_pct:
            return n
    return None


def write_csv(path, rows):
    with path.open("x", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reference",
        type=Path,
        default=script_dir / "modelA_1000_runs_kpis.csv",
    )
    parser.add_argument(
        "--matched",
        type=Path,
        default=script_dir / "modelA_1000_runs_kpis_matched.csv",
    )
    parser.add_argument(
        "--differences-output",
        type=Path,
        default=script_dir / "modelA_1000_runs_paired_differences.csv",
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=script_dir / "modelA_paired_precision_summary.csv",
    )
    args = parser.parse_args()

    reference = read_rows(args.reference)
    matched = read_rows(args.matched)
    if len(reference) != len(matched):
        raise ValueError("Reference and matched files have different row counts")
    if len(reference) < 21:
        raise ValueError("At least 21 paired replications are required")
    for reference_row, matched_row in zip(reference, matched):
        if (
            reference_row["Run"] != matched_row["Run"]
            or reference_row["Seed"] != matched_row["Seed"]
        ):
            raise ValueError("Run/seed pairing differs between the two files")

    difference_rows = []
    for reference_row, matched_row in zip(reference, matched):
        output_row = {
            "Run": reference_row["Run"],
            "Seed": reference_row["Seed"],
        }
        for metric in METRICS:
            short_name = (
                "throughput"
                if metric == "Throughput (items/time unit)"
                else "mean_cycle_time"
            )
            reference_value = float(reference_row[metric])
            matched_value = float(matched_row[metric])
            output_row[f"{short_name}_reference"] = reference_value
            output_row[f"{short_name}_matched"] = matched_value
            output_row[f"{short_name}_difference_matched_minus_reference"] = (
                matched_value - reference_value
            )
        difference_rows.append(output_row)

    summary_rows = []
    for metric in METRICS:
        minimum_n_2pct = first_passing_n(reference, matched, metric, 2.0)
        minimum_n_5pct = first_passing_n(reference, matched, metric, 5.0)
        for n in (21, len(reference)):
            row = paired_statistics(reference, matched, metric, n)
            row["passes_2pct"] = row["relative_half_width_pct"] < 2.0
            row["passes_5pct"] = row["relative_half_width_pct"] < 5.0
            row["first_n_passing_2pct"] = minimum_n_2pct
            row["first_n_passing_5pct"] = minimum_n_5pct
            summary_rows.append(row)

    write_csv(args.differences_output, difference_rows)
    write_csv(args.summary_output, summary_rows)
    print(f"Saved paired differences to {args.differences_output}")
    print(f"Saved paired precision summary to {args.summary_output}")


if __name__ == "__main__":
    main()
