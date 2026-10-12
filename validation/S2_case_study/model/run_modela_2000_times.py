"""Run telemetry-free Model A repeatedly and export KPI CSV files."""

import argparse
import csv
import os
import random
import statistics
from concurrent.futures import ProcessPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np

from modelA_10_machine_series import build_model


SCRIPT_DIR = Path(__file__).resolve().parent
RVG_DIR = SCRIPT_DIR / "gencodeforAMG_10"


def machine_utilization(machine, simulation_time):
    """Return the percentage of simulated time spent processing."""
    state_times = machine.stats["total_time_spent_in_states"]
    processing_time = (
        state_times.get("ATLEAST_ONE_PROCESSING_STATE", 0.0)
        + state_times.get("ALL_ACTIVE_PROCESSING_STATE", 0.0)
    )
    return 100.0 * processing_time / simulation_time


def collect_kpis(top, simulation_time):
    received = top.sink.stats["num_item_received"]
    total_cycle_time = top.sink.stats["total_cycle_time"]
    result = {
        "Items generated": top.source.stats["num_item_generated"],
        "Items received": received,
        "Throughput (items/time unit)": received / simulation_time,
        "Mean cycle time (time units)": (
            total_cycle_time / received if received else 0.0
        ),
    }
    utilizations = {
        machine.id: machine_utilization(machine, simulation_time)
        for machine in top.M
    }
    result["Mean machine utilization (%)"] = statistics.mean(
        utilizations.values()
    )
    for machine_id, utilization in utilizations.items():
        result[f"{machine_id} utilization (%)"] = utilization
    return result


def run_one_experiment(task):
    run_number, simulation_time, seed, plugin_dir = task
    random.seed(seed)
    np.random.seed(seed)

    # FactorySimPy is highly verbose at node-event level. Suppress those
    # messages for batch runs while retaining parent-process progress output.
    with open(os.devnull, "w", encoding="utf-8") as null_output:
        with redirect_stdout(null_output):
            _, top = build_model(
                telemetry=None,
                delay_plugin_dir=plugin_dir,
            )
            top.run_simulation(simulation_time)

    return {
        "Run": run_number,
        "Seed": seed,
        **collect_kpis(top, simulation_time),
    }


def run_experiments(num_runs, simulation_time, base_seed, workers, plugin_dir):
    tasks = [
        (run_number, simulation_time, base_seed + run_number - 1, plugin_dir)
        for run_number in range(1, num_runs + 1)
    ]
    results = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        for completed_count, row in enumerate(
            executor.map(run_one_experiment, tasks, chunksize=5),
            start=1,
        ):
            results.append(row)
            if (
                completed_count == 1
                or completed_count % 100 == 0
                or completed_count == num_runs
            ):
                print(
                    f"Completed {completed_count}/{num_runs}: "
                    f"throughput={row['Throughput (items/time unit)']:.6f}, "
                    f"cycle time={row['Mean cycle time (time units)']:.6f}",
                    flush=True,
                )
    return results


def write_csv(path, fieldnames, rows):
    with path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def summarize(results):
    excluded = {"Run", "Seed", "Items generated", "Items received"}
    metric_names = [
        name for name in results[0] if name not in excluded
    ]
    rows = []
    for metric in metric_names:
        values = [float(result[metric]) for result in results]
        rows.append(
            {
                "Metric": metric,
                "Mean": statistics.mean(values),
                "Standard deviation": (
                    statistics.stdev(values) if len(values) > 1 else 0.0
                ),
                "Minimum": min(values),
                "Maximum": max(values),
                "Runs": len(values),
            }
        )
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs",
        type=int,
        default=2000,
        help="Number of independent runs (default: 2000)",
    )
    parser.add_argument(
        "--sim-time",
        type=float,
        default=1000.0,
        help="Simulation time per run (default: 1000)",
    )
    parser.add_argument(
        "--base-seed",
        type=int,
        default=1000,
        help="Seed used for the first run (default: 1000)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(os.cpu_count() or 1, 8),
        help="Parallel worker processes (default: up to 8)",
    )
    parser.add_argument(
        "--plugin-dir",
        default=str(RVG_DIR),
        help="Folder of processing-delay plugins: gencodeforAMG_10 (fitted, the "
        "Digital Twin) or gencodeforAMG_10_ref (generating, the reference system)",
    )
    parser.add_argument(
        "--out-dir",
        default=str(SCRIPT_DIR),
        help="Folder for the output CSV files (default: this folder)",
    )
    parser.add_argument(
        "--output-suffix",
        default="",
        help="Suffix appended to output file stems, for example: _matched",
    )
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.sim_time <= 0:
        parser.error("--sim-time must be positive")
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    plugins = [f for f in os.listdir(args.plugin_dir) if "delay" in f and f.endswith(".py")]
    if len(plugins) != 10:
        parser.error(f"--plugin-dir must hold ten delay plugins, found {len(plugins)}")
    if args.output_suffix and not args.output_suffix.replace("_", "").isalnum():
        parser.error("--output-suffix may contain only letters, numbers, and underscores")

    results = run_experiments(
        args.runs,
        args.sim_time,
        args.base_seed,
        args.workers,
        str(Path(args.plugin_dir).resolve()),
    )
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    run_path = out_dir / f"modelA_{args.runs}_runs_kpis{args.output_suffix}.csv"
    summary_path = (
        out_dir / f"modelA_{args.runs}_runs_summary{args.output_suffix}.csv"
    )

    write_csv(run_path, list(results[0]), results)
    summary = summarize(results)
    write_csv(
        summary_path,
        ["Metric", "Mean", "Standard deviation", "Minimum", "Maximum", "Runs"],
        summary,
    )
    print(f"Saved per-run KPIs to {run_path}")
    print(f"Saved KPI summary to {summary_path}")


if __name__ == "__main__":
    main()
