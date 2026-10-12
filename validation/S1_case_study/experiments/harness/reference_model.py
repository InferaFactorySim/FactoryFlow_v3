"""6-machine multi-branch reference manufacturing model (shared testbed).

Topology (baseline, what the Digital Twin encodes):

    src -> M0 -> M1 --(70%)-> M2 -> M3 -> M5 -> sink
                    \\-(30%)-> M4 --------> M5
              M3 --(5% QC)--> M4          (low-frequency edge)

Dormant, pre-wired but NOT routed in the baseline (so absent from the DT graph):
    - new machine M6 on  M2 -> M6 -> M3   (add-machine experiments)
    - bypass edge        M0 -> M5         (add-bypass experiments)
    - independent line   src_new -> b_new1 -> M_new -> b_new2 -> sink_new

Every graph edge is a Buffer, so the DT graph vertices include buffers (the item
path records every entity entered). Structural change is induced ONLY by changing
``out_edge_selection`` on the branch machines (the topology itself is static).

Branch machines and their out-edge index map (index = connect() order):
    M0:  0 -> M1,   1 -> M5 (bypass, dormant)
    M1:  0 -> M2,   1 -> M4
    M2:  0 -> M3,   1 -> M6 (dormant)
    M3:  0 -> M5,   1 -> M4 (QC, low-frequency)

A selection is a no-argument callable returning the out-edge index.
"""
import contextlib
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (_HERE, os.path.join(_ROOT, "src"), os.path.join(_ROOT, "structural_drift_detection")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import simpy

from factorysimpy.nodes.node import Node
from factorysimpy.nodes.machine import Machine
from factorysimpy.nodes.source import Source
from factorysimpy.nodes.sink import Sink
from factorysimpy.edges.buffer import Buffer
from factorysimpy.utils.telemetry import Telemetry
from factorysimpy.utils.utils import draw_blockdiagram

from mock_sender import MockSender

# Branch machines that must always carry an explicit selection (their default
# ROUND_ROBIN would otherwise leak flow down the dormant edges into the DT).
BRANCH_MACHINES = (0, 1, 2, 3)

DEFAULT_DELAY = 0.5
DEFAULT_INTER_ARRIVAL = 1.0
DEFAULT_BUFFER_CAPACITY = 5
INACTIVE_INTER_ARRIVAL = 1_000_000_000.0

# Baseline routing split at M1 and QC rate at M3.
# The QC rate is kept high enough that the 5%-style edge does not false-fire a
# removal under the default 60-unit window; E2c (low-frequency removal) lowers it
# deliberately to characterise that regime.
BASELINE_M1_MAIN_P = 0.70   # P(M1 -> M2)
BASELINE_M3_QC_P = 0.15     # P(M3 -> M4)


class ReferenceModel(Node):
    """Builds the static topology; routing is installed separately."""

    def __init__(self, env, id="TOP", telemetry=None,
                 buffer_capacity=DEFAULT_BUFFER_CAPACITY,
                 independent_inter_arrival=INACTIVE_INTER_ARRIVAL):
        super().__init__(env, id)
        self.telemetry = telemetry
        t = telemetry

        self.src = Source(env, id="src", inter_arrival_time=DEFAULT_INTER_ARRIVAL, telemetry=t)
        self.M = {i: Machine(env, id=f"M{i}", telemetry=t) for i in range(7)}  # M0..M6
        self.sink = Sink(env, id="sink", telemetry=t)

        # Fully independent one-machine line. Its source is dormant in the DT
        # baseline, so none of this line's components appear in completed paths.
        # Experiments can activate it by supplying a finite/gated inter-arrival
        # callable through run_capture(independent_arrival_factory=...).
        self.src_new = Source(
            env,
            id="src_new",
            inter_arrival_time=independent_inter_arrival,
            telemetry=t,
        )
        self.M_new = Machine(env, id="M_new", telemetry=t)
        self.sink_new = Sink(env, id="sink_new", telemetry=t)

        self.add_child_node(self.src)
        self.add_child_node([self.M[i] for i in range(7)])
        self.add_child_node(self.sink)
        self.add_child_node([self.src_new, self.M_new, self.sink_new])

        self.b = {}

        def link(name, src_node, dst_node):
            e = Buffer(env, id=name, capacity=buffer_capacity, telemetry=t)
            self.b[name] = e
            self.add_child_edge(e)
            e.connect(src_node, dst_node)
            return e

        M = self.M
        link("b_src_M0", self.src, M[0])
        # M0 out-edges in index order
        link("b_M0_M1", M[0], M[1])
        link("b_M0_M5", M[0], M[5])   # bypass (dormant)
        # M1 out-edges
        link("b_M1_M2", M[1], M[2])
        link("b_M1_M4", M[1], M[4])
        # M2 out-edges
        link("b_M2_M3", M[2], M[3])
        link("b_M2_M6", M[2], M[6])   # to new machine (dormant)
        link("b_M6_M3", M[6], M[3])   # new machine out (dormant)
        # M3 out-edges
        link("b_M3_M5", M[3], M[5])
        link("b_M3_M4", M[3], M[4])   # QC (low-frequency)
        # tails
        link("b_M4_M5", M[4], M[5])
        link("b_M5_sink", M[5], self.sink)

        # Independent path: src_new -> b_new1 -> M_new -> b_new2 -> sink_new.
        link("b_new1", self.src_new, self.M_new)
        link("b_new2", self.M_new, self.sink_new)

        for i in range(7):
            M[i].processing_delay = DEFAULT_DELAY
        self.M_new.processing_delay = DEFAULT_DELAY

    def install_routing(self, selections):
        """selections: dict {machine_index: no-arg callable -> out-edge index}."""
        for idx, fn in selections.items():
            self.M[idx].out_edge_selection = fn

    def set_delays(self, delays):
        for idx, d in delays.items():
            self.M[idx].processing_delay = d


def baseline_selections(rng, m1_main_p=BASELINE_M1_MAIN_P, qc_p=BASELINE_M3_QC_P):
    """The baseline routing program (installed on every run, possibly overridden)."""
    return {
        0: lambda: 0,                                    # M0 -> M1 only
        1: lambda: 0 if rng.random() < m1_main_p else 1,  # M1 split
        2: lambda: 0,                                    # M2 -> M3 only
        3: lambda: 1 if rng.random() < qc_p else 0,       # M3 QC branch
    }


@contextlib.contextmanager
def _quiet():
    """Silence the verbose factorysimpy print() tracing during a run."""
    devnull = open(os.devnull, "w")
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = devnull
    try:
        yield
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        devnull.close()


def run_capture(selection_factory=None, duration=300.0, seed=0,
                delays=None, inter_arrival=DEFAULT_INTER_ARRIVAL,
                buffer_capacity=DEFAULT_BUFFER_CAPACITY,
                independent_arrival_factory=None, draw_diagram=True):
    """Run one simulation and return the captured telemetry events.

    ``selection_factory(env, rng)`` returns the routing dict; it receives the
    freshly-created ``env`` (so callables can gate on ``env.now``) and a seeded
    ``random.Random``. If None, the baseline routing is used.
    """
    with _quiet():
        env = simpy.Environment()
        rng = random.Random(seed)
        mock = MockSender()
        tel = Telemetry(mock)

        independent_inter_arrival = (
            independent_arrival_factory(env, rng)
            if independent_arrival_factory is not None
            else INACTIVE_INTER_ARRIVAL
        )

        model = ReferenceModel(
            env,
            telemetry=tel,
            buffer_capacity=buffer_capacity,
            independent_inter_arrival=independent_inter_arrival,
        )
        model.src.inter_arrival_time = inter_arrival
        if delays:
            model.set_delays(delays)

        if selection_factory is None:
            selections = baseline_selections(rng)
        else:
            selections = selection_factory(env, rng)
        model.install_routing(selections)

        model.fill_hierarchical_id()
       
        if draw_diagram:
            fig = draw_blockdiagram(model)
            fig.render(filename="reference_human_GT", directory=".", format="png", cleanup=True)
            print("Reference model block diagram saved as reference_human_GT.png")
        model.run_simulation(duration)
    return mock.events


def baseline_factory(env, rng):
    """Convenience selection factory for a pure-baseline run."""
    return baseline_selections(rng)


def temporary_independent_arrival(env, t_change, t_restore,
                                  interval=DEFAULT_INTER_ARRIVAL):
    """Return a Source delay callable that activates the independent line briefly.

    The first item is generated at ``t_change``. No new generation is scheduled
    at or beyond ``t_restore``; already-created items may still finish normally.
    """
    def next_delay():
        if env.now < t_change:
            return t_change - env.now
        if env.now + interval >= t_restore:
            return INACTIVE_INTER_ARRIVAL
        return interval
    return next_delay
