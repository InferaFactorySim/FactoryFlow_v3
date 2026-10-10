"""Smoke test for BatchSource.

Checks the three things the class exists to provide:
  A. a whole batch is created at one time instant and shares one creation timestamp,
     and its items are released one at a time in creation order;
  B. `batches_ahead` controls how many batches may be outstanding at once;
  C. the parameter validation rejects meaningless configurations.

Run:  python test_batch_source.py
"""

import io
import os
import sys
import contextlib
from collections import Counter

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import simpy

from factorysimpy.nodes.node import Node
from factorysimpy.nodes.machine import Machine
from factorysimpy.nodes.sink import Sink
from factorysimpy.edges.buffer import Buffer
from factorysimpy.nodes.batch_source import BatchSource

SLOW = 10 ** 6  # a machine delay long enough that it never releases during the test


class Line(Node):
    """source -> b_hold -> slow machine -> b_out -> sink"""

    def __init__(self, env, batch_size, batches_ahead, hold_capacity, inter_arrival):
        super().__init__(env, "TOP")
        self.source = BatchSource(
            env, id="src", batch_size=batch_size, batches_ahead=batches_ahead,
            inter_arrival_time=inter_arrival, blocking=True,
        )
        self.machine = Machine(env, id="m", processing_delay=SLOW)
        self.sink = Sink(env, id="sink")
        self.add_child_node([self.source, self.machine, self.sink])

        self.b_hold = Buffer(env, id="b_hold", capacity=hold_capacity)
        self.b_out = Buffer(env, id="b_out", capacity=1)
        self.add_child_edge([self.b_hold, self.b_out])

        self.b_hold.connect(self.source, self.machine)
        self.b_out.connect(self.machine, self.sink)


def build_and_run(batch_size, batches_ahead, hold_capacity, inter_arrival, until):
    env = simpy.Environment()
    top = Line(env, batch_size, batches_ahead, hold_capacity, inter_arrival)
    top.fill_hierarchical_id()
    with contextlib.redirect_stdout(io.StringIO()):
        top.run_simulation(until)
    return top


def main():
    checks = []

    # --- A. batch creation and release order ------------------------------
    # A roomy holding buffer, so releases never stall and each batch lands intact.
    top = build_and_run(
        batch_size=4, batches_ahead=0, hold_capacity=100, inter_arrival=10, until=35
    )
    # BufferStore keeps collectable items in `ready_items`; `items` holds any still
    # inside the buffer's release delay.
    store = top.b_hold.inbuiltstore
    held = list(store.ready_items) + list(store.items)
    stamps = [item.timestamp_creation for item in held]
    grouped = Counter(stamps)

    print("=" * 62)
    print("BatchSource smoke test")
    print("=" * 62)
    print("A. batch creation")
    print(f"  batches generated : {top.source.stats['num_batches_generated']}")
    print(f"  items generated   : {top.source.stats['num_item_generated']}")
    print(f"  items held        : {len(held)}")
    print(f"  stamps            : {sorted(grouped.items())}")

    checks.append((
        "three batches created by t=35 with inter_arrival 10",
        top.source.stats["num_batches_generated"] == 3,
    ))
    checks.append((
        "each batch contributes exactly one distinct creation stamp",
        len(grouped) == top.source.stats["num_batches_generated"]
        and max(grouped.values()) <= 4,
    ))
    checks.append((
        "all generated items accounted for (one is inside the machine)",
        top.source.stats["num_item_generated"] - len(held) == 1,
    ))
    checks.append((
        "batches stamped one inter_arrival apart, not one per item",
        sorted(grouped.keys()) == [10, 20, 30],
    ))
    # The machine pulled the first item, so the buffer holds the rest in order.
    ids = [item.id for item in held]
    checks.append((
        "items released in creation order",
        ids == sorted(ids, key=lambda name: int(name.rsplit("_", 1)[1])),
    ))

    # --- B. batches_ahead -------------------------------------------------
    # A capacity-1 holding buffer and a machine that never finishes, so the source
    # stalls after two items and the rest of the batch stays pending.
    print("\nB. batches_ahead")
    results = {}
    for ahead in (0, 1, 2):
        top_b = build_and_run(
            batch_size=4, batches_ahead=ahead, hold_capacity=1,
            inter_arrival=1, until=500,
        )
        results[ahead] = (
            top_b.source.stats["num_item_generated"],
            top_b.source.stats["num_batches_generated"],
        )
        print(f"  batches_ahead={ahead}: items={results[ahead][0]}, "
              f"batches={results[ahead][1]}")

    checks.append((
        "batches_ahead=0 keeps exactly one batch outstanding",
        results[0] == (4, 1),
    ))
    checks.append((
        "batches_ahead=1 prepares one further batch",
        results[1] == (8, 2),
    ))
    checks.append((
        "batches_ahead=2 prepares two further batches",
        results[2] == (12, 3),
    ))

    # --- C. validation ----------------------------------------------------
    print("\nC. validation")

    def rejects(**kwargs):
        env = simpy.Environment()
        try:
            BatchSource(env, id="bad", blocking=True, inter_arrival_time=1, **kwargs)
        except ValueError:
            return True
        return False

    checks.append(("batch_size=0 rejected", rejects(batch_size=0)))
    checks.append(("batch_size=1.5 rejected", rejects(batch_size=1.5)))
    checks.append(("batches_ahead=-1 rejected", rejects(batch_size=2, batches_ahead=-1)))

    env = simpy.Environment()
    try:
        BatchSource(env, id="bad2", batch_size=2, blocking=False, inter_arrival_time=1)
        blocking_rejected = False
    except ValueError:
        blocking_rejected = True
    checks.append(("blocking=False rejected", blocking_rejected))
    for label, ok in checks[-4:]:
        print(f"  {label}: {'yes' if ok else 'NO'}")

    # --- report -----------------------------------------------------------
    print("\nChecks")
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

    failed = [label for label, ok in checks if not ok]
    print("\n" + ("ALL CHECKS PASSED" if not failed
                  else f"{len(failed)} CHECK(S) FAILED"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
