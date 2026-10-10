"""Where does a board's cycle time actually go?

The model's mean cycle time sits above the reference's. This script measures the
breakdown instead of arguing about it: every flow item records the time it spends in
each node (BaseFlowItem.stats, filled by update_node_event), so stepping the
simulation and capturing each board as it reaches the sink gives a per-component
residence profile, plus the time a board spends stamped but not yet in the line.

Run:  python diagnose_cycle_time.py
"""

import io
import os
import sys
import contextlib
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import simpy

import smt_line_factorysimpy as smt
from factorysimpy.helper.baseflowitem import BaseFlowItem

REFERENCE_MEAN_CYCLE_S = smt.REFERENCE["avg_cycle_time_hours"] * 3600.0


def collect(batches_ahead):
    """Run the line, capturing each board as it is handed to the sink.

    Sink clears `item_in_process` within the same simulation step, so the capture
    hooks the item's own entry event instead. At sink entry an item's `stats` already
    holds its residence time in every upstream node.
    """
    records = []
    original = BaseFlowItem.update_node_event

    def capture(self, hierarchical_id, env, event_type="entry"):
        original(self, hierarchical_id, env, event_type)
        if event_type == "entry" and hierarchical_id.endswith("sink_1"):
            # path_events[0] is the source itself, recorded at creation, and the
            # source never records an exit, so the time a board waits its turn inside
            # the batch has to be read as the gap to the first real node it enters.
            entered_line = (
                self.path_events[1]["sim_time"] if len(self.path_events) > 1 else env.now
            )
            records.append({
                "cycle": env.now - self.timestamp_creation,
                "pre_line": entered_line - self.timestamp_creation,
                "nodes": {k: v for k, v in self.stats.items()
                          if not k.endswith("sink_1")},
            })

    env = simpy.Environment()
    BaseFlowItem.update_node_event = capture
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            top = smt.SMTLine(env, "TOP")
            top.source.batches_ahead = batches_ahead
            top.fill_hierarchical_id()
            top.validate(verbose=False)

            while top.sink.stats["num_item_received"] < smt.BATCH_SIZE:
                nxt = env.peek()
                if nxt == float("inf") or nxt > smt.MAX_SIM_TIME:
                    break
                env.step()
    finally:
        BaseFlowItem.update_node_event = original
    return env.now, records


def summarise(label, batches_ahead):
    end_time, records = collect(batches_ahead)
    n = len(records)
    mean_cycle = sum(r["cycle"] for r in records) / n
    mean_pre = sum(r["pre_line"] for r in records) / n

    node_totals = defaultdict(float)
    for record in records:
        for node, seconds in record["nodes"].items():
            node_totals[node] += seconds
    node_means = {node: total / n for node, total in node_totals.items()}
    in_nodes = sum(node_means.values())

    print(f"\n{'=' * 68}\n{label}\n{'=' * 68}")
    print(f"boards measured            : {n}")
    print(f"mean cycle time            : {mean_cycle:8.1f} s "
          f"({mean_cycle / 3600.0:.4f} h)")
    print(f"reference mean cycle time  : {REFERENCE_MEAN_CYCLE_S:8.1f} s "
          f"({REFERENCE_MEAN_CYCLE_S / 3600.0:.4f} h)")
    print(f"difference                 : {mean_cycle - REFERENCE_MEAN_CYCLE_S:+8.1f} s")
    print("\nbreakdown of the mean:")
    print(f"  stamped, before the line : {mean_pre:8.1f} s   "
          f"({mean_pre / mean_cycle * 100:5.1f}%)")
    for node, seconds in sorted(node_means.items(), key=lambda kv: -kv[1]):
        print(f"  in {node:24s}{seconds:8.1f} s   "
              f"({seconds / mean_cycle * 100:5.1f}%)")
    edges = mean_cycle - mean_pre - in_nodes
    print(f"  in edges (buffers/belts) : {edges:8.1f} s   "
          f"({edges / mean_cycle * 100:5.1f}%)")
    return mean_cycle, mean_pre, in_nodes, edges


def main():
    ahead1 = summarise("batches_ahead=1 (the shipped configuration)", 1)
    ahead0 = summarise("batches_ahead=0 (one stack outstanding)", 0)

    print(f"\n{'=' * 68}\nWhat the two configurations change\n{'=' * 68}")
    labels = ("mean cycle", "pre-line wait", "time in nodes", "time in edges")
    for label, a1, a0 in zip(labels, ahead1, ahead0):
        print(f"  {label:16s} ahead=1 {a1:8.1f} s   ahead=0 {a0:8.1f} s   "
              f"delta {a1 - a0:+8.1f} s")
    print("\nThe reference's own mean is "
          f"{REFERENCE_MEAN_CYCLE_S:.1f} s. Its line holds the draining stack inside")
    print("buff_0 and the waiting stack inside the blocked source, so its pre-line")
    print("wait is one full stack period plus the mean wait within a stack.")


if __name__ == "__main__":
    main()
