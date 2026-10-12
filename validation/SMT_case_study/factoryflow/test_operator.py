"""Smoke test for HumanOperator and ServicedMachine.

Two machines share one operator and are tuned so their service requests collide,
which is what the classes exist to model: a machine that requests service while the
operator is busy must queue, and so stall for longer than the task's own duration.

  source -b0- m1 (service every 2 items) -b1- m2 (service every 3 items) -b2- sink

Run:  python test_operator.py
"""

import io
import os
import sys
import contextlib

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import simpy

from factorysimpy.nodes.node import Node
from factorysimpy.nodes.source import Source
from factorysimpy.nodes.sink import Sink
from factorysimpy.edges.buffer import Buffer
from factorysimpy.nodes.human_operator import HumanOperator
from factorysimpy.nodes.serviced_machine import ServicedMachine

SERVICE_TIME = 60
RUN_TIME = 1200


class TestLine(Node):
    def __init__(self, env, id="TOP"):
        super().__init__(env, id)

        self.operator = HumanOperator(env, id="operator_1")

        self.source = Source(env, id="src", inter_arrival_time=0, blocking=True)
        self.m1 = ServicedMachine(
            env, id="m1", processing_delay=20, operator=self.operator,
            services=[{"task": "reel_replacement", "interval": 2}],
        )
        self.m2 = ServicedMachine(
            env, id="m2", processing_delay=25, operator=self.operator,
            services=[{"task": "refill", "interval": 3}],
        )
        self.sink = Sink(env, id="sink")

        self.operator.assign_task(self.m1, "reel_replacement", SERVICE_TIME)
        self.operator.assign_task(self.m2, "refill", SERVICE_TIME)

        self.add_child_node([self.operator, self.source, self.m1, self.m2, self.sink])

        self.b0 = Buffer(env, id="b0", capacity=1)
        self.b1 = Buffer(env, id="b1", capacity=1)
        self.b2 = Buffer(env, id="b2", capacity=1)
        self.add_child_edge([self.b0, self.b1, self.b2])

        self.b0.connect(self.source, self.m1)
        self.b1.connect(self.m1, self.m2)
        self.b2.connect(self.m2, self.sink)


def main():
    env = simpy.Environment()
    top = TestLine(env)
    top.fill_hierarchical_id()

    # The library logs every event; keep only this test's own output.
    with contextlib.redirect_stdout(io.StringIO()):
        top.run_simulation(RUN_TIME)

    op = top.operator
    checks = []

    print("=" * 62)
    print("HumanOperator / ServicedMachine smoke test")
    print("=" * 62)
    print(f"Run time            : {RUN_TIME}")
    print(f"Items finished      : {top.sink.stats['num_item_received']}")

    print("\nOperator")
    print(f"  tasks completed   : {op.stats['num_tasks_completed']}")
    print(f"  per task type     : {op.stats['tasks_completed_per_type']}")
    print(f"  busy time         : {op.stats['total_busy_time']:.1f}")
    print(f"  queued wait total : {op.stats['total_wait_time']:.1f}")
    print(f"  max queue length  : {op.stats['max_queue_length']}")
    util = op.get_utilization(RUN_TIME)
    print(f"  utilisation       : "
          + ", ".join(f"{k}={v:.1f}%" for k, v in util.items()))

    print("\nMachines")
    for machine in (top.m1, top.m2):
        print(f"  {machine.id}: processed={machine.stats['num_item_processed']}, "
              f"service_requests={machine.stats['num_service_requests']}, "
              f"waiting_for_operator="
              f"{machine.stats['total_time_waiting_for_operator']:.1f}")

    # --- assertions -------------------------------------------------------
    checks.append((
        "items flowed through the line",
        top.sink.stats["num_item_received"] > 0,
    ))
    checks.append((
        "operator completed tasks for both machines",
        len(op.stats["tasks_completed_per_type"]) == 2,
    ))
    checks.append((
        "busy time equals tasks x service time",
        abs(op.stats["total_busy_time"]
            - op.stats["num_tasks_completed"] * SERVICE_TIME) < 1e-9,
    ))
    requested = top.m1.stats["num_service_requests"] + top.m2.stats["num_service_requests"]
    checks.append((
        "every completed task came from a request",
        op.stats["num_tasks_completed"] <= requested,
    ))
    # Contention: at least one request had to queue behind another.
    checks.append((
        "requests queued (contention actually occurred)",
        op.stats["max_queue_length"] > 1 or op.stats["total_wait_time"] > 0,
    ))
    # A machine that queued must have stalled longer than the bare task time.
    worst = max(
        machine.stats["total_time_waiting_for_operator"]
        - machine.stats["num_service_requests"] * SERVICE_TIME
        for machine in (top.m1, top.m2)
    )
    checks.append((
        "a machine stalled longer than the task itself (queueing modelled)",
        worst > 0,
    ))
    checks.append((
        "idle + busy accounts for the whole run",
        abs(sum(util.values()) - 100.0) < 1e-6,
    ))

    print("\nChecks")
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

    failed = [label for label, ok in checks if not ok]
    print("\n" + ("ALL CHECKS PASSED" if not failed else f"{len(failed)} CHECK(S) FAILED"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
