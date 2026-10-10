# Description
#
# S4 ground truth: ten parallel conveyor-linked production lines sharing one
# source and one sink. Each line has ten machines joined by four conveyor belts
# and five buffers; the source feeds the lines round-robin through ten entry
# buffers and the sink collects them through ten exit buffers.
#
# Every processing delay is drawn from a strictly positive distribution family,
# so no sample is ever negative and no clipping is applied anywhere. Clipping
# truncates a distribution and makes it fit as a different family, which is the
# failure this system is designed to avoid.
#
# The family is fixed per column and identical across all ten lines, so the
# system contains ten distinct distributions with ten independent replicates
# each. That makes fit repeatability measurable from a single run.

import csv
import os
import sys

import numpy as np
import simpy
from scipy import stats

_src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if _src_path not in sys.path:
    sys.path.insert(0, _src_path)

from factorysimpy.edges.buffer import Buffer
from factorysimpy.edges.converyorbelt import ConveyorBelt
from factorysimpy.nodes.machine import Machine
from factorysimpy.nodes.node import Node
from factorysimpy.nodes.sink import Sink
from factorysimpy.nodes.source import Source


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

# Processing-delay family per column. loc is fixed at 0 for every skewed family
# so the fitter cannot mimic one family with another by floating the shift, and
# the normal is held to CV = 0.025 so P(X < 0) is negligible.
COLUMN_DISTRIBUTIONS = [
    ("normal", stats.norm(loc=4.0, scale=0.10)),
    ("lognormal", stats.lognorm(0.45, loc=0.0, scale=4.5157)),
    ("gamma", stats.gamma(9.0, loc=0.0, scale=0.5000)),
    ("weibull", stats.weibull_min(5.0, loc=0.0, scale=5.6627)),
    ("uniform", stats.uniform(loc=1.0, scale=2.0)),
    ("triangular", stats.triang(0.25, loc=3.0, scale=4.0)),
    ("uniform", stats.uniform(loc=2.0, scale=1.0)),
    ("gamma", stats.gamma(16.0, loc=0.0, scale=0.3000)),
    ("lognormal", stats.lognorm(0.30, loc=0.0, scale=4.3989)),
    ("weibull", stats.weibull_min(2.5, loc=0.0, scale=5.6350)),
    # C10 exists only in stage 3, on the machine appended to line 05.
    ("gamma", stats.gamma(12.0, loc=0.0, scale=0.4000)),
]

# ---- Stage 2: parameter drift -------------------------------------------
# Three kinds of change, each applied to one column across all ten lines, so
# the parameter path must handle a shifted mean, a changed family at an
# unchanged mean, and a changed family together with a shifted mean.
STAGE2_DISTRIBUTIONS = {
    # mean 2.0 -> 4.0, same family: the two supports [1, 3] and [3, 5] do not
    # overlap, so the shift is unmistakable in any 300-value window.
    4: ("uniform", stats.uniform(loc=3.0, scale=2.0)),
    # mean 4.0 unchanged, normal (SD 0.1) -> lognormal (SD 1.43): family change
    # without a mean shift; scale = 4 / exp(0.35**2 / 2) keeps the mean at 4.0
    0: ("lognormal", stats.lognorm(0.35, loc=0.0, scale=3.7623523)),
    # uniform [2, 3] -> normal(5, 0.4): family change and mean 2.5 -> 5.0
    6: ("normal", stats.norm(loc=5.0, scale=0.40)),
}

# ---- Phase C: structural change ------------------------------------------
# The source stops feeding line 00. No machine is removed: line 00 simply goes
# dark, so every one of its transitions disappears from the observed graph
# while the components still exist. The change is a routing policy, not a
# topology edit, and it is what the structural monitor has to detect.

T_PARAM_CHANGE = 4_000.0     # phase A -> B
T_ROUTE_CHANGE = 8_000.0     # phase B -> C
T_END = 12_000.0

DARK_LINE = 0                # the line the source stops feeding in phase C


def phase_distribution(phase, column):
    """Return (family, distribution) for a column in phase A (1) or B/C (2)."""
    if phase >= 2 and column in STAGE2_DISTRIBUTIONS:
        return STAGE2_DISTRIBUTIONS[column]
    return COLUMN_DISTRIBUTIONS[column]

# Watts per state. Only the four states that partition a Machine's time are
# rated: ALL_ACTIVE_PROCESSING_STATE and ATLEAST_ONE_BLOCKED_STATE overlap with
# these and would double-count the energy.
COLUMN_POWER = [
    {"ATLEAST_ONE_PROCESSING_STATE": 7000, "IDLE_STATE": 800, "ALL_ACTIVE_BLOCKED_STATE": 900, "SETUP_STATE": 3000},
    {"ATLEAST_ONE_PROCESSING_STATE": 6500, "IDLE_STATE": 750, "ALL_ACTIVE_BLOCKED_STATE": 850, "SETUP_STATE": 2800},
    {"ATLEAST_ONE_PROCESSING_STATE": 5500, "IDLE_STATE": 700, "ALL_ACTIVE_BLOCKED_STATE": 800, "SETUP_STATE": 2500},
    {"ATLEAST_ONE_PROCESSING_STATE": 8000, "IDLE_STATE": 900, "ALL_ACTIVE_BLOCKED_STATE": 1000, "SETUP_STATE": 3500},
    {"ATLEAST_ONE_PROCESSING_STATE": 12000, "IDLE_STATE": 1500, "ALL_ACTIVE_BLOCKED_STATE": 1700, "SETUP_STATE": 5000},
    {"ATLEAST_ONE_PROCESSING_STATE": 5000, "IDLE_STATE": 600, "ALL_ACTIVE_BLOCKED_STATE": 700, "SETUP_STATE": 2200},
    {"ATLEAST_ONE_PROCESSING_STATE": 4000, "IDLE_STATE": 500, "ALL_ACTIVE_BLOCKED_STATE": 600, "SETUP_STATE": 1800},
    {"ATLEAST_ONE_PROCESSING_STATE": 6000, "IDLE_STATE": 700, "ALL_ACTIVE_BLOCKED_STATE": 800, "SETUP_STATE": 2600},
    {"ATLEAST_ONE_PROCESSING_STATE": 5800, "IDLE_STATE": 700, "ALL_ACTIVE_BLOCKED_STATE": 800, "SETUP_STATE": 2500},
    {"ATLEAST_ONE_PROCESSING_STATE": 7500, "IDLE_STATE": 850, "ALL_ACTIVE_BLOCKED_STATE": 950, "SETUP_STATE": 3200},
]

BELT_POWER = {"MOVING_STATE": 400, "STALLED_STATE": 120, "EMPTY_STATE": 50}

# Columns whose outgoing edge is a conveyor belt; every other gap is a buffer.
BELT_AFTER_COLUMN = (0, 3, 5, 8)

BELT_CAPACITY = 2          # slots; transit time is capacity * delay
BELT_DELAY = 0.5           # per slot, deterministic: belt speed is a constant
MID_BUFFER_COLUMN = 4      # decoupling bank between the two line segments
ENTRY_BUFFER_CAPACITY = 5
MID_BUFFER_CAPACITY = 10
EXIT_BUFFER_CAPACITY = 5
INTER_BUFFER_CAPACITY = 3
SOURCE_INTER_ARRIVAL = 0.75   # 69% utilisation at the C3 bottleneck, 77% once line 00 is dark


def make_delay(env, before, after, t_switch, seed):
    """Zero-argument callable that samples ``before`` until ``t_switch`` and
    ``after`` from then on.

    Each machine gets its own generator so a run is reproducible and the ten
    replicates of a column stay statistically independent. The switch is a
    property of the real system; nothing tells the framework it happened.
    """
    rng = np.random.default_rng(seed)

    def sample():
        dist = after if (after is not None and env.now >= t_switch) else before
        return float(dist.rvs(random_state=rng))

    return sample


def make_routing(env, num_lines, t_switch, dark_line=DARK_LINE):
    """Generator of out-edge indices for the source.

    Round-robin over every line until ``t_switch``, then round-robin over all
    lines except ``dark_line``. Source accepts a generator directly.
    """
    live = [i for i in range(num_lines) if i != dark_line]
    i = 0
    while True:
        if env.now >= t_switch:
            yield live[i % len(live)]
        else:
            yield i % num_lines
        i += 1


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class Line(Node):
    """One production line: ten machines joined by conveyors and buffers."""

    def __init__(self, env, id, line_index, num_machines=10, seed_base=0, telemetry=None):
        super().__init__(env, id)
        line_str = f"{line_index:02d}"

        self.M = []
        for col in range(num_machines):
            _, before = phase_distribution(1, col)
            after = STAGE2_DISTRIBUTIONS[col][1] if col in STAGE2_DISTRIBUTIONS else None
            machine = Machine(
                env,
                id=f"M{col:02d}",
                processing_delay=make_delay(
                    env, before, after, T_PARAM_CHANGE,
                    seed_base + line_index * 100 + col),
                telemetry=telemetry,
            )
            machine.set_power_ratings(COLUMN_POWER[col % len(COLUMN_POWER)])
            self.M.append(machine)
        self.add_child_node(self.M)

        # Intermediate edges belong to the line. Entry and exit buffers are
        # created by the parent SystemModel, as they join it to source and sink.
        self.edges = []
        for col in range(num_machines - 1):
            tag = f"{col:02d}_{col + 1:02d}"
            if col in BELT_AFTER_COLUMN:
                edge = ConveyorBelt(
                    env,
                    id=f"CV{tag}",
                    capacity=BELT_CAPACITY,
                    delay=BELT_DELAY,
                    telemetry=telemetry,
                )
                edge.set_power_ratings(BELT_POWER)
            elif col == MID_BUFFER_COLUMN:
                edge = Buffer(
                    env,
                    id=f"BMID{tag}",
                    capacity=MID_BUFFER_CAPACITY,
                    telemetry=telemetry,
                )
            else:
                edge = Buffer(
                    env,
                    id=f"B{tag}",
                    capacity=INTER_BUFFER_CAPACITY,
                    telemetry=telemetry,
                )
            self.edges.append(edge)
        self.add_child_edge(self.edges)

        for col, edge in enumerate(self.edges):
            edge.connect(self.M[col], self.M[col + 1])

        self.entry = self.M[0]
        self.exit = self.M[-1]


class SystemModel(Node):
    """Ten parallel lines sharing one source and one sink."""

    def __init__(self, env, id, num_lines=10, num_cols=10, seed_base=0, telemetry=None,
                 sink_telemetry=None):
        super().__init__(env, id)
        self.num_lines = num_lines
        self.num_cols = num_cols

        self.lines = [
            Line(
                env,
                id=f"Line_{i:02d}",
                line_index=i,
                num_machines=num_cols,
                seed_base=seed_base,
                telemetry=telemetry,
            )
            for i in range(num_lines)
        ]
        self.add_child_node(self.lines)

        self.source = Source(
            env,
            id="src",
            inter_arrival_time=SOURCE_INTER_ARRIVAL,
            blocking=True,
            out_edge_selection=make_routing(env, num_lines, T_ROUTE_CHANGE),
            telemetry=telemetry,
        )
        self.sink = Sink(env, id="sink", telemetry=sink_telemetry)
        self.add_child_node([self.source, self.sink])

        # One source and one sink serve every line. Edge.connect registers the
        # edge on both endpoints directly, so the shared nodes carry one edge
        # per line.
        self.entry_buffers, self.exit_buffers = [], []
        for i in range(num_lines):
            line_str = f"{i:02d}"
            self.entry_buffers.append(
                Buffer(env, id=f"B_IN_{line_str}", capacity=ENTRY_BUFFER_CAPACITY, telemetry=telemetry)
            )
            self.exit_buffers.append(
                Buffer(env, id=f"B_OUT_{line_str}", capacity=EXIT_BUFFER_CAPACITY, telemetry=telemetry)
            )
        self.add_child_edge(self.entry_buffers)
        self.add_child_edge(self.exit_buffers)

        for i in range(num_lines):
            self.entry_buffers[i].connect(self.source, self.lines[i].entry)
            self.exit_buffers[i].connect(self.lines[i].exit, self.sink)

    def machines(self):
        return [m for line in self.lines for m in line.M]

    def belts(self):
        return [e for line in self.lines for e in line.edges if isinstance(e, ConveyorBelt)]

    def total_energy(self):
        """Joules over machines and belts."""
        return sum(c.get_total_energy() for c in self.machines() + self.belts())


def build(env, telemetry=None, seed_base=0, sink_telemetry=None):
    """Build the S4 system.

    One continuous model. The processing-delay change fires at
    ``T_PARAM_CHANGE`` and the routing change at ``T_ROUTE_CHANGE``; nothing
    announces either to the framework.
    """
    model = SystemModel(env, id="TOP", seed_base=seed_base, telemetry=telemetry,
                        sink_telemetry=sink_telemetry)
    # Populates every component's hierarchical_id. Without it the ids stay
    # empty, item paths arrive as blanks and no structural evidence exists.
    model.fill_hierarchical_id()
    return model


def build_model():
    """Return the TOP model, unsimulated and without telemetry.

    The Real System UI's diagram builder calls this to draw the hierarchy, so it
    must not open a telemetry connection or advance the clock.
    """
    TOP = build(simpy.Environment())
    return TOP


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

def snapshot(model, now):
    """Cumulative quantities at this instant.

    Every entry is monotonic, so any window's value is the difference between
    its two bracketing snapshots. ``max_cycle_time`` is the exception: it is a
    running extreme, so it is read and then reset, making each sample interval
    carry its own maximum. The maximum over a window is then the largest of the
    sample maxima inside it.
    """
    sink, source = model.sink, model.source
    machines, belts = model.machines(), model.belts()
    buffers = [e for line in model.lines for e in line.edges if isinstance(e, Buffer)]
    buffers += model.entry_buffers + model.exit_buffers

    row = {
        "sim_time": now,
        "items_received": sink.stats["num_item_received"],
        "items_generated": source.stats["num_item_generated"],
        "total_cycle_time": sink.stats["total_cycle_time"],
        "interval_max_cycle_time": sink.stats["max_cycle_time"],
        "interval_min_cycle_time": sink.stats["min_cycle_time"] or 0.0,
        "processing_operations": sum(m.stats["num_item_processed"] for m in machines),
        "machine_processing_time": sum(
            m.stats["total_time_spent_in_states"]["ATLEAST_ONE_PROCESSING_STATE"] for m in machines),
        "machine_blocked_time": sum(
            m.stats["total_time_spent_in_states"]["ALL_ACTIVE_BLOCKED_STATE"] for m in machines),
        "machine_energy_J": sum(m.get_total_energy() for m in machines),
        "conveyor_energy_J": sum(b.get_total_energy() for b in belts),
        # occupancy integrals, not the running averages: these difference cleanly
        "buffer_occupancy_integral": sum(
            getattr(b.inbuiltstore, "_weighted_sum", 0.0) for b in buffers),
        "belt_occupancy_integral": sum(
            getattr(b, "_weighted_occupancy", 0.0) for b in belts),
    }
    # reset the running extremes so the next interval carries its own
    sink.stats["max_cycle_time"] = 0.0
    sink.stats["min_cycle_time"] = None
    return row


def sampler(env, model, rows, interval):
    """Sample every ``interval``.

    The sample at the run end is taken by the caller after ``env.run`` returns:
    SimPy does not execute events scheduled at exactly the stop time, so a
    timeout landing on it would never fire and the last window would close one
    interval short.
    """
    while True:
        yield env.timeout(interval)
        rows.append(snapshot(model, env.now))


def write_samples(rows, path):
    """Write the sampled series so KPIs over any window follow by difference."""
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def make_telemetry(system_name="S4"):
    """RabbitMQ telemetry sink, matching the convention used by D27."""
    from factorysimpy.utils.telemetry import RabbitMQSender, Telemetry

    sender = RabbitMQSender(
        host=os.environ.get("RABBITMQ_HOST", "rabbitmq"),
        queue=os.environ.get("RABBITMQ_QUEUE", "factory_data"),
    )
    return Telemetry(sender, system_name=os.environ.get("TELEMETRY_SYSTEM_NAME", system_name))


def run(duration=T_END, telemetry=None, seed_base=0, realtime=True, factor=1.0,
        sample_interval=None, sample_path=None):
    """Build S4 and run it.

    With ``realtime`` the simulation is paced against the wall clock, so one
    time unit takes ``factor`` seconds and the framework observes the plant at
    the rate a real one would. ``strict=False`` lets a step that overruns its
    slot catch up rather than aborting the run.

    With ``sample_interval`` the run also records its own cumulative series, so
    the reference side has KPIs over any window without depending on a separate
    harness. The twin's KPIs come from the Digital Twin UI.
    """
    env = (simpy.rt.RealtimeEnvironment(factor=factor, strict=False)
           if realtime else simpy.Environment())
    model = build(env, telemetry=telemetry, sink_telemetry=telemetry, seed_base=seed_base)
    rows = []
    if sample_interval:
        env.process(sampler(env, model, rows, sample_interval))
    model.run_simulation(duration)
    if sample_interval:
        # SimPy does not execute events scheduled at exactly the stop time, so
        # the closing sample is taken here rather than by the sampler process.
        if not rows or rows[-1]["sim_time"] < duration:
            rows.append(snapshot(model, env.now))
        if sample_path:
            write_samples(rows, sample_path)
    model.samples = rows
    return model


def main():
    """Entry point used when the real-system UI launches this file.

    The UI runs a model file as its own process, so everything is taken from
    the environment. Telemetry is on unless explicitly disabled; the UI's
    diagram builder executes the file under a different ``__name__`` and never
    reaches here, so no connection is opened just to draw the model.
    """
    # Default: phase A only, as fast as possible. env.run(until=T_PARAM_CHANGE)
    # stops before any event at T_PARAM_CHANGE, so every delay is drawn from the
    # phase A distributions. S4_SIM_TIME=12000 S4_REALTIME=1 gives the full run.
    duration = float(os.environ.get("S4_SIM_TIME", T_PARAM_CHANGE))
    seed_base = int(os.environ.get("S4_SEED", "0"))
    realtime = os.environ.get("S4_REALTIME", "0") == "1"
    factor = float(os.environ.get("S4_REALTIME_FACTOR", "1"))
    telemetry = None if os.environ.get("S4_TELEMETRY", "1") == "0" else make_telemetry()
    interval = float(os.environ.get("S4_SAMPLE_INTERVAL", "50"))
    sample_path = os.environ.get(
        "S4_SAMPLE_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "s4_live_timeseries.csv"))

    def status(line):
        # Status goes to stderr so the component chatter on stdout can be
        # discarded without losing it.
        print(line, file=sys.stderr, flush=True)

    status(f"[S4] {duration:g} time units, "
           f"{'realtime factor ' + str(factor) if realtime else 'as fast as possible'}, "
           f"telemetry {'on' if telemetry else 'off'}")
    status(f"[S4] parameter change at {T_PARAM_CHANGE:g}, "
           f"routing change at {T_ROUTE_CHANGE:g} (line {DARK_LINE:02d} goes dark)")

    # FactorySimPy components print on every event - about 125 lines per time
    # unit, which is ~86 MB of log over a 12,000-unit run and pure overhead for
    # an unattended one. Keep them only if asked.
    import contextlib
    quiet = os.environ.get("S4_QUIET", "1") == "1"
    sink = open(os.devnull, "w") if quiet else sys.stdout
    with contextlib.redirect_stdout(sink):
        model = run(duration, telemetry=telemetry, seed_base=seed_base,
                    realtime=realtime, factor=factor,
                    sample_interval=interval, sample_path=sample_path)
    if quiet:
        sink.close()

    produced = model.sink.stats.get("num_item_received", 0)
    energy = model.total_energy()
    status(f"[S4] samples written to {sample_path}")
    status(f"[S4] finished: {produced} items, "
           f"throughput {produced / duration:.4f}, "
           f"energy {energy / 1e6:.2f} MJ")
    if telemetry is not None:
        telemetry.close()
    return model


if __name__ == "__main__":
    main()
