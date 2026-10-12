"""Run the S4 reference system once, continuously, through its three phases.

Phase A  0 - 4,000        baseline; fills the fitting windows
Phase B  4,000 - 8,000    three processing-delay distributions change at 4,000
Phase C  8,000 - 12,000   the source stops feeding line 00 at 8,000

Measurement windows exclude each change's settling period: phase B is
measured over 4,200-8,000 and phase C over 9,000-12,000.

Nothing announces either change. The run records enough to compute any KPI over
any window after the fact, so comparison windows can be chosen once the
framework reports when it detected each change and when it redeployed.

Outputs under results/:
    s3_timeseries.csv   one row per sample interval, cumulative counters
    s3_window_kpis.csv  KPIs over each phase and over arbitrary named windows
    s3_route_share.csv  items delivered per line per sample interval

Usage:  python run_s3_experiment.py [sample_interval]
"""

import csv
import io
import os
import re
import sys
import time
from contextlib import redirect_stdout

import simpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from S3_GT import (  # noqa: E402
    DARK_LINE, T_END, T_PARAM_CHANGE, T_ROUTE_CHANGE, build, sampler, snapshot,
)
from factorysimpy.edges.buffer import Buffer  # noqa: E402

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
SAMPLE_INTERVAL = 250.0

MACHINE_RE = re.compile(r"Line_(\d+)\.M(\d+)")


class SinkRecorder:
    """Duck-types the Telemetry interface the Sink calls.

    The Sink emits one event per completed item carrying its ordered component
    path, so the line that produced each item is recoverable exactly. Counts are
    bucketed by sample interval rather than stored per item.
    """

    def __init__(self, interval=SAMPLE_INTERVAL):
        self.interval = interval
        self.counts = {}        # bucket index -> {line: count}

    def event(self, event_type=None, entity_type=None, entity_id=None,
              sim_time=None, attributes=None, **kwargs):
        if not attributes:
            return
        line = None
        for component in attributes.get("item_path") or ():
            found = MACHINE_RE.search(str(component))
            if found:
                line = int(found.group(1))
                break
        if line is None:
            return
        bucket = int((sim_time or 0.0) // self.interval)
        self.counts.setdefault(bucket, {})[line] = \
            self.counts.setdefault(bucket, {}).get(line, 0) + 1

    # the Sink only calls event(); the rest are here so the duck type is complete
    def metric_old(self, *a, **k):
        pass

    def publish_kpi(self, *a, **k):
        pass

    def close(self):
        pass


def window_kpis(rows, t0, t1, label):
    """KPIs over (t0, t1], from the bracketing snapshots."""
    inside = [r for r in rows if t0 < r["sim_time"] <= t1]
    before = [r for r in rows if r["sim_time"] <= t0]
    if not inside:
        return None
    a = before[-1] if before else {k: 0.0 for k in inside[0]}
    b = inside[-1]
    dt = b["sim_time"] - (a.get("sim_time") or 0.0)
    d = lambda k: b[k] - (a.get(k) or 0.0)  # noqa: E731

    items = d("items_received")
    n_machines = 100
    energy = d("machine_energy_J") + d("conveyor_energy_J")
    return {
        "window": label,
        "from": a.get("sim_time") or 0.0,
        "to": b["sim_time"],
        "items_produced": items,
        "throughput_items_per_unit": items / dt if dt else 0.0,
        "mean_cycle_time": d("total_cycle_time") / items if items else 0.0,
        "max_cycle_time": max(r["interval_max_cycle_time"] for r in inside),
        "mean_machine_utilisation_pct": 100.0 * d("machine_processing_time") / (dt * n_machines),
        "mean_machine_blocked_pct": 100.0 * d("machine_blocked_time") / (dt * n_machines),
        "mean_queue_occupancy_items": (d("buffer_occupancy_integral") + d("belt_occupancy_integral")) / dt,
        "total_energy_MJ": energy / 1e6,
        "energy_per_item_kJ": energy / items / 1e3 if items else 0.0,
        "mean_plant_power_kW": energy / dt / 1e3,
    }


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"  {os.path.relpath(path):<44} {len(rows):>6} rows")


def main():
    interval = float(sys.argv[1]) if len(sys.argv) > 1 else SAMPLE_INTERVAL
    os.makedirs(RESULTS, exist_ok=True)

    recorder = SinkRecorder(interval)
    env = simpy.Environment()
    rows = []
    print(f"S4 continuous run to {T_END:g}, sampling every {interval:g}")
    print(f"  parameter change at {T_PARAM_CHANGE:g}, routing change at {T_ROUTE_CHANGE:g} "
          f"(line {DARK_LINE:02d} goes dark)")
    t0 = time.time()
    with redirect_stdout(io.StringIO()):
        model = build(env, sink_telemetry=recorder)
        env.process(sampler(env, model, rows, interval))
        env.run(until=T_END)
        if not rows or rows[-1]["sim_time"] < T_END:
            rows.append(snapshot(model, env.now))
    print(f"  completed in {time.time() - t0:.0f}s\n")

    write_csv(os.path.join(RESULTS, "s3_timeseries.csv"), rows)

    # These close where the design says to measure. Once the framework reports
    # when it redeployed, re-run make_comparison_table.py with windows that open
    # after redeployment so the stale-twin period is reported separately rather
    # than averaged into the agreement.
    windows = [
        ("phase_A_baseline", 0.0, T_PARAM_CHANGE),
        # Open a little after each change so the transient is not averaged in.
        ("phase_B_parameter_change", T_PARAM_CHANGE + 200.0, T_ROUTE_CHANGE),
        ("phase_C_routing_change", T_ROUTE_CHANGE + 1_000.0, T_END),
    ]
    kpis = [k for k in (window_kpis(rows, a, b, n) for n, a, b in windows) if k]
    write_csv(os.path.join(RESULTS, "s3_window_kpis.csv"), kpis)

    share = []
    for bucket in sorted(recorder.counts):
        counts = recorder.counts[bucket]
        total = sum(counts.values()) or 1
        row = {"sim_time": (bucket + 1) * interval, "total_items": sum(counts.values())}
        for line in range(10):
            row[f"line_{line:02d}_share_pct"] = 100.0 * counts.get(line, 0) / total
        share.append(row)
    write_csv(os.path.join(RESULTS, "s3_route_share.csv"), share)

    print()
    for k in kpis:
        print(f"{k['window']}  ({k['from']:.0f} - {k['to']:.0f})")
        print(f"   throughput        {k['throughput_items_per_unit']:.4f}")
        print(f"   mean cycle time   {k['mean_cycle_time']:.3f}")
        print(f"   max cycle time    {k['max_cycle_time']:.2f}")
        print(f"   utilisation       {k['mean_machine_utilisation_pct']:.2f}%")
        print(f"   queue occupancy   {k['mean_queue_occupancy_items']:.3f}")
        print(f"   energy per item   {k['energy_per_item_kJ']:.2f} kJ")


if __name__ == "__main__":
    main()
