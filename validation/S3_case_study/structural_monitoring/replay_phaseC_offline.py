"""Phase C structural detection without Docker: an offline replay of the live runs.

The published runs (``reference_system/run_phaseC_repeats.py``) ran the phase C
plant in real time inside Docker and fed its telemetry, through RabbitMQ, to the
structural drift monitor, which reconciled once per second. This script runs the
same plant (``S3_GT`` with the phase B delays from t = 0 and line 00 withdrawn
at t = 150, same seeds) in simulated time, captures the completed-item events at
the sink, and feeds them to the same monitor code (``drift_detection.py``) with
the same settings and reference graph, reconciling once per time unit. The
monitor's alerts are then scored with the scoring code of the live runs.

For seeds 0-9 the replay reproduces the live runs (``phaseC_repeats/summary.json``)
exactly: transitions detected, first detection time, start-up and false alerts.
Because the live reconciliation ticks were not aligned with whole time units, a
detection time could in principle differ by up to one reconciliation interval.

Usage, from this folder:
    python replay_phaseC_offline.py                 # seeds 0-9, to t = 300
    python replay_phaseC_offline.py --seeds 0 1 --sim-time 400
"""
import argparse
import contextlib
import io
import json
import logging
import os
import statistics
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "reference_system"), HERE]

# drift_detection imports pika for its RabbitMQ consumer, which is not used here.
try:
    import pika  # noqa: F401
except ImportError:
    sys.modules["pika"] = types.ModuleType("pika")

import simpy  # noqa: E402

import drift_detection as dd  # noqa: E402
import S3_GT  # noqa: E402
from factorysimpy.utils.telemetry import Telemetry  # noqa: E402
from run_phaseC_repeats import N_TARGET, T_CHANGE, score  # noqa: E402

SETTINGS = os.path.join(HERE, "structural_drift_settings.json")
GRAPH = os.path.join(HERE, "dt_graph_s3.json")
OUT = os.path.join(HERE, "phaseC_offline_replay.json")


class CaptureSender:
    def __init__(self):
        self.events = []

    def send(self, msg):
        self.events.append(msg)

    def close(self):
        pass


def sink_events(seed, sim_time):
    """Run phase C (as S3_GT_phaseC.py) in simulated time; return sink entry events."""
    S3_GT.T_PARAM_CHANGE, S3_GT.T_ROUTE_CHANGE = 0.0, T_CHANGE
    sender = CaptureSender()
    env = simpy.Environment()
    with contextlib.redirect_stdout(io.StringIO()):
        model = S3_GT.build(env, seed_base=seed, sink_telemetry=Telemetry(sender, system_name="S4"))
        env.run(until=sim_time)
    events = [e for e in sender.events
              if e.get("entity_type") == "Sink" and e.get("event_type") == "entry"]
    return sorted(events, key=lambda e: float(e["sim_time"]))


def replay(events, sim_time):
    """Feed events to a fresh monitor, reconciling once per time unit; return its log."""
    settings = json.load(open(SETTINGS, encoding="utf-8"))
    graph = dd.DigitalTwinGraph(path=GRAPH)
    assert graph._try_load() and len(graph.all_transitions()) == 220
    detector = dd.MultiSinkDriftDetector(
        graph, **{k: settings[k] for k in (
            "routing_threshold", "min_routing_samples", "routing_window_size", "removal_window",
            "activity_timeout", "require_recent_activity", "path_failure_enabled",
            "resolved_retention_window", "resolved_max_items")})
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(logging.Formatter("%(message)s"))
    dd.log.addHandler(handler)
    dd.log.setLevel(logging.INFO)
    try:
        i, tick = 0, 1.0
        while tick <= sim_time:
            while i < len(events) and float(events[i]["sim_time"]) <= tick:
                detector.ingest_event(events[i])
                i += 1
            detector.reconcile_periodic()
            tick += settings["reconciliation_interval"]
    finally:
        dd.log.removeHandler(handler)
    return buffer.getvalue()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, nargs="+", default=list(range(10)))
    ap.add_argument("--sim-time", type=float, default=300.0)
    args = ap.parse_args()
    results = []
    for seed in args.seeds:
        log_text = replay(sink_events(seed, args.sim_time), args.sim_time)
        result = {"seed": seed, "sim_time": args.sim_time, **score(log_text)}
        results.append(result)
        print(f"seed {seed}: detected {result['target_detected']}/{N_TARGET}, "
              f"first at {result['first_detection']}, latency {result['latency']}, "
              f"start-up {result['startup_alerts']}, false {result['false_alerts']}", flush=True)
    json.dump(results, open(OUT, "w", encoding="utf-8"), indent=1)
    lat = [r["latency"] for r in results if r["latency"] is not None]
    full = sum(r["target_detected"] == N_TARGET for r in results)
    print(f"\nall {N_TARGET} transitions detected in {full}/{len(results)} runs"
          + (f"; latency mean {statistics.mean(lat):.1f}, min {min(lat):.1f}, max {max(lat):.1f}" if lat else ""))
    print(f"false alerts after start-up: {sum(r['false_alerts'] for r in results)}; written to {OUT}")


if __name__ == "__main__":
    sys.exit(main())
