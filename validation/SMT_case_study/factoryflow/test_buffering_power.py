"""Smoke test for BatchDrainBuffer and PowerCycledMachine.

The two together are the mechanism the IOTAIS 2018 paper evaluates: accumulate items
while the thermal unit is switched off, then warm up and drain the batch through it.
The test runs the same line twice and compares:

  control  : plain Buffer, oven never switches off
  buffered : BatchDrainBuffer(capacity=N), oven switches off when idle

and checks that buffering buys oven off-time at the cost of throughput.

  source -b_in- m1 -[buffer under test]- oven -b_out- sink

Run:  python test_buffering_power.py
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
from factorysimpy.nodes.machine import Machine
from factorysimpy.nodes.source import Source
from factorysimpy.nodes.sink import Sink
from factorysimpy.edges.buffer import Buffer
from factorysimpy.nodes.power_cycled_machine import PowerCycledMachine

M1_DELAY = 5
OVEN_DELAY = 2
OVEN_SETUP = 10
BUFFER_N = 8
RUN_TIME = 600

# The reference reflow oven's ratings, in watts. The ratio is what matters here:
# holding temperature while idle is expensive, and switching off is only worth it
# when the off periods are long enough to repay the warm-up.
P_OVEN_ON = 25800.0       # temperature maintained, occupied or not
P_OVEN_WARMUP = 33000.0
P_OVEN_OFF = 320.0


class Line(Node):
    def __init__(self, env, buffered):
        super().__init__(env, "TOP")
        self.source = Source(env, id="src", inter_arrival_time=0, blocking=True)
        self.m1 = Machine(env, id="m1", processing_delay=M1_DELAY)
        # The control oven never switches off, so it has no warm-up to serve; giving
        # it a setup_time it could never use is rejected by the class.
        self.oven = PowerCycledMachine(
            env, id="oven", processing_delay=OVEN_DELAY,
            setup_time=OVEN_SETUP if buffered else 0,
            power_down_when_idle=buffered,
        )
        self.sink = Sink(env, id="sink")
        self.add_child_node([self.source, self.m1, self.oven, self.sink])

        self.b_in = Buffer(env, id="b_in", capacity=1)
        self.mid = Buffer(
            env, id="mid", capacity=BUFFER_N,
            admission="BATCH_DRAIN" if buffered else "CONTINUOUS",
        )
        self.b_out = Buffer(env, id="b_out", capacity=1)
        self.add_child_edge([self.b_in, self.mid, self.b_out])

        self.b_in.connect(self.source, self.m1)
        self.mid.connect(self.m1, self.oven)
        self.b_out.connect(self.oven, self.sink)


def run(buffered):
    env = simpy.Environment()
    top = Line(env, buffered)
    top.fill_hierarchical_id()
    with contextlib.redirect_stdout(io.StringIO()):
        top.run_simulation(RUN_TIME)
    states = top.oven.stats["total_time_spent_in_states"]
    off = states.get("OFF_STATE", 0.0)
    warmup = states.get("WARMUP_STATE", 0.0)
    on = max(0.0, RUN_TIME - off - warmup)
    energy = P_OVEN_OFF * off + P_OVEN_WARMUP * warmup + P_OVEN_ON * on
    done = top.sink.stats["num_item_received"]
    return {
        "top": top,
        "done": done,
        "throughput": done / RUN_TIME,
        "off": off,
        "warmup": warmup,
        "on": on,
        "energy_per_item": energy / done if done else float("inf"),
        "power_downs": top.oven.stats["num_power_downs"],
        "power_cycles": top.oven.stats["num_power_cycles"],
    }


def main():
    control = run(buffered=False)
    buffered = run(buffered=True)
    mid = buffered["top"].mid
    checks = []

    print("=" * 70)
    print("BatchDrainBuffer / PowerCycledMachine smoke test")
    print("=" * 70)
    print(f"{'':22s}{'control':>12s}{'buffered':>12s}")
    for label, key in (
        ("items finished", "done"),
        ("throughput (items/s)", "throughput"),
        ("oven off (s)", "off"),
        ("oven warmup (s)", "warmup"),
        ("oven on (s)", "on"),
        ("power downs", "power_downs"),
        ("warm-ups", "power_cycles"),
        ("energy per item", "energy_per_item"),
    ):
        print(f"  {label:20s}{control[key]:12.3f}{buffered[key]:12.3f}")
    print(f"\n  drain cycles: {mid.num_drain_cycles}, "
          f"final state: {mid.drain_state}")

    checks.append(("items flow in both configurations",
                   control["done"] > 0 and buffered["done"] > 0))
    checks.append(("control oven never switches off",
                   control["off"] == 0.0 and control["power_downs"] == 0))
    checks.append(("buffered oven switches off and warms up",
                   buffered["power_downs"] > 0 and buffered["power_cycles"] > 0))
    checks.append(("buffered oven accumulates off time",
                   buffered["off"] > 0.0))
    checks.append(("warm-up time equals warm-ups x setup_time",
                   abs(buffered["warmup"]
                       - buffered["power_cycles"] * OVEN_SETUP) < 1e-9))
    checks.append(("buffer filled and drained repeatedly",
                   mid.num_drain_cycles > 1))
    checks.append(("buffering costs throughput",
                   buffered["throughput"] < control["throughput"]))
    checks.append(("buffering cuts oven on-time",
                   buffered["on"] < control["on"]))
    checks.append(("buffering saves energy per item",
                   buffered["energy_per_item"] < control["energy_per_item"]))

    print("\nChecks")
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

    failed = [label for label, ok in checks if not ok]
    print("\n" + ("ALL CHECKS PASSED" if not failed
                  else f"{len(failed)} CHECK(S) FAILED"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
