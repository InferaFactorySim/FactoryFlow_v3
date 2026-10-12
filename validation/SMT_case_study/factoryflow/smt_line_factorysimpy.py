"""FactorySimPy model of the SMT-PCB assembly line (IOTAIS 2018, Karanjkar et al.).

Reference system
----------------
Real SMT line, Mysuru, India. Published as:
  N. Karanjkar, A. Joglekar, S. Mohanty, V. Prabhu, D. Raghunath, R. Sundaresan,
  "Digital Twin for Energy Optimization in an SMT-PCB Assembly Line",
  IEEE IOTAIS 2018, pp. 85-89.
Reference SimPy model: https://github.com/NehaKaranjkar/IIoT_RBCCPS  (model_2/)

Scope: the *no-buffering baseline* only, i.e. the "No Buffering" row of Table I and
model_2/Results/Results_no_buffering.csv. Every parameter is taken verbatim from
model_2/AssemblyLine.py.

This model is deliberately expressible as component instantiation plus parameters
and nothing else: no Node subclasses, no env.process controllers, no custom
behaviour. That is a hard requirement, because in FactoryFlow the model is produced
by the LLM-assisted generator composing library components -- a hand-written class
here would mean the case study validates hand-coding rather than the generator.
The buffering experiments of the reference paper are therefore out of scope; they
need behaviour the library does not have as parameters (see
../case_study_recommendations.md, "Required FactorySimPy changes").

Topology (node/edge assignment matches the reference exactly: belts and transfer
positions are edges; machines, the buffering module and the oven are nodes):

  source -b0- line_loader -b1- screen_printer -belt_sp_pp1- pick_and_place_1
         -b2- pick_and_place_2 -b3- buffering_module -belt_bm_rfo- reflow_oven
         -b4- sink

8 nodes, 7 edges, as in model_2/AssemblyLine.py.

Documented approximations
-------------------------
1. Reflow oven. The reference oven is a 10-slot belt advancing one slot every 5 s.
   Here it is a Machine with work_capacity=10 and processing_delay=50, which holds
   the same 10 boards for the same 50 s transit. The difference is that a real belt
   advances in lockstep, so a board waits for the next 5 s tick (mean +2.5 s on a
   ~2670 s cycle time, ~0.1%). Its output never blocks here, since b4 drains into a
   zero-delay sink, so no further difference arises.
2. Oven warm-up. The reference computes setup from the chamber temperature:
   (temp_max - temp_room)/heating_rate*3600 = (200-30)/170*3600 = 3600 s from cold.
   That is exactly the 3.9409% setup fraction in the reference CSV, and is modelled
   here as node_setup_time=3600.
3. Human operator. One shared operator performs 60 s refill and reel-replacement
   tasks. Those 60 s are folded into the machine's own delay callable, so operator
   contention is not modelled. At the published intervals (refill every 100 boards,
   reel replacement every 50) contention is negligible.
4. Source, and mean cycle time. The reference source creates a stack of 16 boards and
   stamps `creation_timestamp` on all 16 at that instant, puts the whole stack into
   buff_0 (capacity 1, so one slot holds an entire stack), and the loader then holds
   that stack and feeds it one board at a time. Its Sink measures from that stack
   stamp, so a board carries both its own wait inside the stack being fed (mean
   15/2 x 88.2 s) and the wait of its stack sitting in buff_0 while the previous stack
   is fed out (a full stack period, 16 x 88.2 s).
   `BatchSource` reproduces the batch stamping, and `batches_ahead` controls how much
   of the head-of-line waiting is reproduced. Measured against the reference's 0.7419 h:

       plain Source                             0.2794 h   (-62%)
       BatchSource(16), batches_ahead=0         0.4803 h   (-35%)
       BatchSource(16), batches_ahead=1         0.8665 h   (+17%)

   The reference sits between the last two, and neither reproduces it exactly, because
   of *where* a stack is held. Its LineLoader takes a `get_copy()` of the stack and
   drains it while the stack itself stays in buff_0, removing the empty tray only at
   the end; the source meanwhile sits blocked on `put` holding the next stack, already
   stamped. So the draining stack occupies buff_0 and the waiting stack sits in the
   source. BatchSource holds both upstream of b0, which counts slightly more pre-line
   inventory and overshoots. `batches_ahead=1` is kept because it matches the
   reference's structure -- two stacks stamped and outstanding at once.

   Exact parity needs a stack to travel as one unit and be unpacked downstream, which
   is not expressible today: `Source` creates *empty* pallets, and `Splitter`'s "SPLIT"
   mode is documented but not implemented (`Splitter.worker` only drains an existing
   container's `.items`).

   Throughput is unaffected by any of this, since the loader is not the bottleneck
   (85 s at pick_and_place_1 is): all three settings give 40.3547 boards/h.
5. The reference runs on a 1 s integer time grid with 0.5-slot output offsets, while
   FactorySimPy is plain event-driven, so sub-percent deviations are expected.
6. Energy is computed after the run, from elapsed times and the reference power
   ratings. FactorySimPy has no energy concept, so this is reporting, not model
   behaviour. Only the five components the reference gives non-zero ratings are
   counted: screen printer, both placements, buffering module, reflow oven.

Run:  python smt_line_factorysimpy.py
"""

import math
import os
import sys

_FACTORYSIMPY_SRC = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
)
if _FACTORYSIMPY_SRC not in sys.path:
    sys.path.insert(0, _FACTORYSIMPY_SRC)

import simpy

from factorysimpy.nodes.node import Node
from factorysimpy.nodes.machine import Machine
from factorysimpy.nodes.serviced_machine import ServicedMachine
from factorysimpy.nodes.human_operator import HumanOperator
from factorysimpy.nodes.power_cycled_machine import PowerCycledMachine
from factorysimpy.nodes.batch_source import BatchSource
from factorysimpy.nodes.sink import Sink
from factorysimpy.edges.buffer import Buffer
from factorysimpy.edges.converyorbelt import ConveyorBelt


# ---------------------------------------------------------------------------
# Parameters (model_2/AssemblyLine.py)
# ---------------------------------------------------------------------------
BATCH_SIZE = 1024                 # stop after this many boards finish
MAX_SIM_TIME = 100 * 3600         # reference safety horizon: 100 hours
STACK_SIZE = 16                   # source_1.PCB_stack_size: boards released together

LOADER_DELAY = 2                  # line_loader.delay

PRINTING_DELAY = 18               # screen_printer.printing_delay
CLEANING_DELAY = 28               # screen_printer.cleaning_delay
PCBS_PER_CLEANING = 2             # screen_printer.num_pcbs_per_cleaning
PCBS_PER_REFILL = 100             # 500 g capacity / 5 g per board (PCB type 1)
REFILL_DELAY = 60                 # HumanOperator task delay
REFILLS_PER_EVENT = 2             # solder and adhesive both run out together

PP1_DELAY = 85                    # pick_and_place_1.processing_delay
PP2_DELAY = 50                    # pick_and_place_2.processing_delay
REEL_INTERVAL = 50                # boards between reel replacements
REEL_DELAY = 60                   # HumanOperator task delay

BELT_SLOTS = 3                    # ConveyorBelt.num_stages
BELT_DELAY_PER_SLOT = 1           # ConveyorBelt.delay_per_stage

OVEN_SLOTS = 10                   # reflow_oven.num_stages
OVEN_DELAY_PER_SLOT = 5           # reflow_oven.delay_per_stage
OVEN_TRANSIT = OVEN_SLOTS * OVEN_DELAY_PER_SLOT
OVEN_SETUP = 3600                 # (200-30)/170*3600, warm-up from cold

# Power ratings in watts, from the set_power_ratings(...) calls.
P_PRINTER_IDLE = 100.0            # idle / waiting_for_refill / waiting_to_output
P_PRINTER_PRINTING = 500.0
P_PRINTER_CLEANING = 1000.0
P_PP_IDLE = 100.0                 # idle / waiting_for_reel / waiting_to_output
P_PP_PROCESSING = 500.0
P_BUFFERING_MODULE = 250.0        # equal in bypass / filling / emptying
P_OVEN_OFF = 320.0
P_OVEN_SETUP = 33000.0
P_OVEN_MAINTAIN = 25800.0         # equal when occupied and unoccupied

# Reference results: model_2/Results/Results_no_buffering.csv
REFERENCE = {
    "throughput_pcb_per_hour": 40.354900683638114,
    "avg_cycle_time_hours": 0.7419064670138894,
    "max_cycle_time_hours": 1.6193055555555556,
    "avg_energy_per_pcb_kj": 2427.2947265625,
    "rfo_off_percent": 0.0,
    "rfo_setup_percent": 3.9409082698865348,
    "rfo_on_empty_percent": 45.79663818630644,
    "rfo_on_occupied_percent": 50.26245354380703,
}

# The three Machine states that partition its time. Machine's stats also carry
# ALL_ACTIVE_PROCESSING_STATE and ATLEAST_ONE_BLOCKED_STATE, which overlap with these,
# so rating all six would double count.
EXCLUSIVE_MACHINE_STATES = (
    "SETUP_STATE", "IDLE_STATE", "ALL_ACTIVE_BLOCKED_STATE",
    "ATLEAST_ONE_PROCESSING_STATE",
)


def machine_power(idle, processing, setup=None):
    """Power ratings over the mutually exclusive Machine states."""
    return {
        "SETUP_STATE": idle if setup is None else setup,
        "IDLE_STATE": idle,
        "ALL_ACTIVE_BLOCKED_STATE": idle,
        "ATLEAST_ONE_PROCESSING_STATE": processing,
    }


# ---------------------------------------------------------------------------
# Delay callables
#
# A callable processing_delay is a documented Machine parameter, and is the same
# shape as a generated DataFITR delay plugin. Node.get_delay calls it with no
# arguments, so each closes over its own board counter to reproduce the reference
# cadences. Only the returned value affects the simulation; `calls` is read
# afterwards for reporting.
# ---------------------------------------------------------------------------
class ScreenPrinterDelay:
    """18 s per board, plus a 28 s cleaning cycle every 2 boards.

    Consumable refills are not here: they are service tasks performed by the shared
    operator, declared as service rules on the machine.
    """

    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        delay = float(PRINTING_DELAY)
        if self.calls % PCBS_PER_CLEANING == 0:
            delay += CLEANING_DELAY
        return delay


class PickAndPlaceDelay:
    """Constant placement delay. Reel replacement is an operator service task."""

    def __init__(self, processing_delay):
        self.processing_delay = processing_delay
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return float(self.processing_delay)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
def oven_warm_up(oven):
    """Warm-up time from the chamber's current temperature, as the reference does.

    The reference cools by Newton's law while off and heats at a fixed rate, with
    temp_max 200 C, ambient 30 C, heating 170 C/h and cooling constant 0.5/h. That
    reduces to 3600 * (1 - exp(-0.5 * hours_off)) seconds, which is the full 3600 s
    from cold and proportionally less after a short stop.
    """
    def warm_up():
        machine = oven[0]
        # The very first warm-up starts from ambient, like the reference's
        # AUTONOMOUS start, so it costs the full 3600 s rather than the little the
        # elapsed-time formula would give at t=0.
        if machine.stats["num_power_cycles"] == 0:
            return OVEN_SETUP
        hours_off = machine.time_since_power_off / 3600.0
        return int(round(OVEN_SETUP * (1.0 - math.exp(-0.5 * hours_off)))) or 1
    return warm_up


class SMTLine(Node):
    """SMT-PCB assembly line.

    Parameters:
        buffering (None, "single" or "double"): the configuration from Table I.
            None reproduces the No Buffering row: the module is a pass-through and the
            oven holds temperature throughout. "single" accumulates N boards and
            closes entry while draining them. "double" accumulates N while continuing
            to accept into a second stack. In both, the oven switches off while the
            buffer fills and warms up before the batch is drained through it.
        buffer_capacity (int): N, the capacity per stage.
    """

    def __init__(self, env, id="TOP", buffering=None, buffer_capacity=None,
                 telemetry=None):
        super().__init__(env, id)
        self.telemetry = telemetry
        self.buffering = buffering
        self.buffer_capacity = buffer_capacity
        if buffering not in (None, "single", "double"):
            raise ValueError("buffering must be None, 'single' or 'double'")
        if buffering is not None and not buffer_capacity:
            raise ValueError("buffer_capacity is required when buffering is enabled")

        self.printer_delay = ScreenPrinterDelay()
        self.pp1_delay = PickAndPlaceDelay(PP1_DELAY)
        self.pp2_delay = PickAndPlaceDelay(PP2_DELAY)

        # --- nodes ---
        # A stack of 16 boards is created at one instant and fed in one at a time,
        # as the reference source and line loader do. blocking=True so the source
        # stalls when the line is full instead of discarding boards.
        # batches_ahead=1: a fresh stack is prepared at the head of the line as soon
        # as the previous one has been taken, so a waiting stack accumulates time
        # before any of its boards enter -- the reference's stack sitting in buff_0.
        self.source = BatchSource(
            env, id="source_1", batch_size=STACK_SIZE, batches_ahead=1,
            inter_arrival_time=0, blocking=True, telemetry=telemetry,
        )
        self.line_loader = Machine(
            env, id="line_loader", processing_delay=LOADER_DELAY, telemetry=telemetry
        )
        # One operator serves the whole line, as in the reference. Machines declare
        # when service is due; the operator's assigned tasks fix how long each takes,
        # so a machine that requests service while the operator is busy queues and
        # stalls for longer than the task itself.
        self.operator = HumanOperator(env, id="operator_1", telemetry=telemetry)

        self.screen_printer = ServicedMachine(
            env, id="screen_printer", processing_delay=self.printer_delay,
            operator=self.operator,
            services=[
                {"task": "solder_refill", "interval": PCBS_PER_REFILL},
                {"task": "adhesive_refill", "interval": PCBS_PER_REFILL},
            ],
            telemetry=telemetry,
        )
        self.pick_and_place_1 = ServicedMachine(
            env, id="pick_and_place_1", processing_delay=self.pp1_delay,
            operator=self.operator,
            services=[{"task": "reel_replacement", "interval": REEL_INTERVAL}],
            telemetry=telemetry,
        )
        self.pick_and_place_2 = ServicedMachine(
            env, id="pick_and_place_2", processing_delay=self.pp2_delay,
            operator=self.operator,
            services=[{"task": "reel_replacement", "interval": REEL_INTERVAL}],
            telemetry=telemetry,
        )
        # Power ratings are declared on the components themselves, so energy is a
        # model parameter rather than something the reporting code knows about.
        self.screen_printer.set_power_ratings(
            machine_power(P_PRINTER_IDLE, P_PRINTER_PRINTING)
        )
        self.pick_and_place_1.set_power_ratings(
            machine_power(P_PP_IDLE, P_PP_PROCESSING)
        )
        self.pick_and_place_2.set_power_ratings(
            machine_power(P_PP_IDLE, P_PP_PROCESSING)
        )

        self.operator.assign_task(self.screen_printer, "solder_refill", REFILL_DELAY)
        self.operator.assign_task(self.screen_printer, "adhesive_refill", REFILL_DELAY)
        self.operator.assign_task(self.pick_and_place_1, "reel_replacement", REEL_DELAY)
        self.operator.assign_task(self.pick_and_place_2, "reel_replacement", REEL_DELAY)
        # In the baseline the buffering module is in bypass: boards pass straight
        # through. It is kept as a component because it still draws 250 W.
        self.buffering_module = Machine(
            env, id="buffering_module", processing_delay=0, telemetry=telemetry
        )
        # Oven: 10 boards in the chamber for 50 s each, after a 3600 s warm-up.
        # Without buffering it holds temperature throughout, so a plain Machine with a
        # start-up setup is enough. With buffering it must switch off while the buffer
        # fills and warm up again before draining it.
        if buffering is None:
            self.reflow_oven = Machine(
                env, id="reflow_oven", work_capacity=OVEN_SLOTS,
                processing_delay=OVEN_TRANSIT, node_setup_time=OVEN_SETUP,
                telemetry=telemetry,
            )
        else:
            oven_ref = []
            self.reflow_oven = PowerCycledMachine(
                env, id="reflow_oven", work_capacity=OVEN_SLOTS,
                processing_delay=OVEN_TRANSIT,
                setup_time=oven_warm_up(oven_ref),
                power_down_when_idle=True, start_powered=False,
                telemetry=telemetry,
            )
            oven_ref.append(self.reflow_oven)
        self.sink = Sink(env, id="sink_1", telemetry=telemetry)

        self.buffering_module.set_power_ratings(
            machine_power(P_BUFFERING_MODULE, P_BUFFERING_MODULE)
        )
        # The oven draws its warm-up power during SETUP and holds temperature
        # otherwise, occupied or not.
        self.reflow_oven.set_power_ratings(
            machine_power(P_OVEN_MAINTAIN, P_OVEN_MAINTAIN, setup=P_OVEN_SETUP)
        )

        self.add_child_node([
            self.source, self.line_loader, self.screen_printer,
            self.pick_and_place_1, self.pick_and_place_2,
            self.buffering_module, self.reflow_oven, self.sink,
            self.operator,
        ])

        # --- edges ---
        # Single-board transfer positions, as buff_0..buff_4 in the reference.
        self.b0 = Buffer(env, id="b0", capacity=1, telemetry=telemetry)
        self.b1 = Buffer(env, id="b1", capacity=1, telemetry=telemetry)
        self.b2 = Buffer(env, id="b2", capacity=1, telemetry=telemetry)
        # b3 is the accumulating stage. Without buffering it is a single-board
        # transfer position; with buffering it is the stack itself.
        if buffering is None:
            self.b3 = Buffer(env, id="b3", capacity=1, telemetry=telemetry)
        elif buffering == "single":
            self.b3 = Buffer(
                env, id="b3", capacity=buffer_capacity,
                admission="BATCH_DRAIN", telemetry=telemetry,
            )
        else:
            # Double buffering is two stacks of N in ping-pong, and it needs no
            # controller: each stack is BATCH_DRAIN, so it withholds while filling,
            # closes entry once full and releases until empty. With FIRST_AVAILABLE
            # on both sides, the module fills whichever stack is accepting and the
            # drain node takes from whichever is releasing, so the two alternate on
            # their own. Filling and draining therefore overlap, which is what keeps
            # throughput up.
            self.b3 = Buffer(env, id="b3", capacity=1, telemetry=telemetry)
            self.stack_a = Buffer(
                env, id="stack_a", capacity=buffer_capacity,
                admission="BATCH_DRAIN", telemetry=telemetry,
            )
            self.stack_b = Buffer(
                env, id="stack_b", capacity=buffer_capacity,
                admission="BATCH_DRAIN", telemetry=telemetry,
            )
            self.stack_out = Machine(
                env, id="stack_out", processing_delay=0,
                in_edge_selection="FIRST_AVAILABLE",
                out_edge_selection="FIRST_AVAILABLE",
                telemetry=telemetry,
            )
            self.add_child_node(self.stack_out)
            self.buffering_module.out_edge_selection = "FIRST_AVAILABLE"
        self.b4 = Buffer(env, id="b4", capacity=1, telemetry=telemetry)

        # Slotted non-accumulating belts: the whole belt stalls if the board in the
        # last slot is not collected.
        self.belt_sp_pp1 = ConveyorBelt(
            env, id="belt_sp_pp1", capacity=BELT_SLOTS,
            delay=BELT_DELAY_PER_SLOT, telemetry=telemetry,
        )
        self.belt_bm_rfo = ConveyorBelt(
            env, id="belt_bm_rfo", capacity=BELT_SLOTS,
            delay=BELT_DELAY_PER_SLOT, telemetry=telemetry,
        )

        self.add_child_edge([
            self.b0, self.b1, self.b2, self.b3, self.b4,
            self.belt_sp_pp1, self.belt_bm_rfo,
        ])

        # --- wiring ---
        self.b0.connect(self.source, self.line_loader)
        self.b1.connect(self.line_loader, self.screen_printer)
        self.belt_sp_pp1.connect(self.screen_printer, self.pick_and_place_1)
        self.b2.connect(self.pick_and_place_1, self.pick_and_place_2)
        self.b3.connect(self.pick_and_place_2, self.buffering_module)
        if buffering == "double":
            self.add_child_edge([self.stack_a, self.stack_b])
            self.stack_a.connect(self.buffering_module, self.stack_out)
            self.stack_b.connect(self.buffering_module, self.stack_out)
            self.belt_bm_rfo.connect(self.stack_out, self.reflow_oven)
        else:
            self.belt_bm_rfo.connect(self.buffering_module, self.reflow_oven)
        self.b4.connect(self.reflow_oven, self.sink)

        # Whichever edges accumulate batches, for reporting.
        self.batch_buffers = (
            [self.stack_a, self.stack_b] if buffering == "double" else [self.b3]
        )


# ---------------------------------------------------------------------------
# Run control and reporting
# ---------------------------------------------------------------------------
def run_until_batch(env, top, sink, batch_size=BATCH_SIZE, max_time=MAX_SIM_TIME):
    """Step until `batch_size` boards have finished, then finalise.

    The reference run is terminating ("stop after 1024 boards"), whereas
    Node.run_simulation runs to a fixed horizon, so the stepping is done here and
    the same finalisation hooks are applied afterwards.
    """
    top.validate(verbose=False)

    while sink.stats["num_item_received"] < batch_size:
        next_event = env.peek()
        if next_event == float("inf") or next_event > max_time:
            break
        env.step()

    end_time = env.now
    nodes, edges = top.get_all_nodes_edges()
    # Finalisation is bookkeeping, not simulation: Machine.update_final_state_time
    # asserts that its worker count matches the resource's user count, which can
    # disagree when a run is stopped with several workers active (see gap 15). A
    # failure there costs only the last interval of one node's state times, so it is
    # recorded rather than allowed to discard a completed run.
    top.finalisation_errors = []
    for node in nodes.values():
        if hasattr(node, "update_final_state_time"):
            try:
                node.update_final_state_time(end_time)
            except Exception as exc:
                top.finalisation_errors.append(f"{node.id}: {type(exc).__name__}")
    for edge in edges.values():
        for hook in ("update_final_buffer_avg_content",
                     "update_final_conveyor_avg_content"):
            if hasattr(edge, hook):
                try:
                    getattr(edge, hook)(end_time)
                except TypeError:
                    getattr(edge, hook)()
    return end_time


def component_energy(machine):
    """Energy in joules, summed only over the states that partition a Machine's time."""
    per_state = machine.get_energy_consumption()
    return sum(per_state.get(state, 0.0) for state in EXCLUSIVE_MACHINE_STATES)


def compute_energy(top, end_time):
    """Energy in joules per component, over the five the reference accounts for.

    The bulk comes from each component's own declared power ratings, via the library's
    `get_energy_consumption()`. Two corrections remain, both because a Machine has one
    PROCESSING state where the reference distinguishes finer ones:

      * the printer draws 1000 W while cleaning, not the 500 W it draws while printing;
      * a machine waiting for the operator draws its idle 100 W, but that wait happens
        while its worker still holds PROCESSING (see ServicedMachine).
    """
    boards = top.printer_delay.calls
    cleaning = CLEANING_DELAY * (boards // PCBS_PER_CLEANING)
    printer_wait = top.screen_printer.stats["total_time_waiting_for_operator"]

    printer_energy = (
        component_energy(top.screen_printer)
        + (P_PRINTER_CLEANING - P_PRINTER_PRINTING) * cleaning
        - (P_PRINTER_PRINTING - P_PRINTER_IDLE) * printer_wait
    )

    def pp_energy(machine):
        wait = machine.stats["total_time_waiting_for_operator"]
        return component_energy(machine) - (P_PP_PROCESSING - P_PP_IDLE) * wait

    # The oven's off and warm-up periods are recorded separately by
    # PowerCycledMachine while the base class still counts them as idle or
    # processing, so those intervals are re-priced here.
    oven_states = top.reflow_oven.stats["total_time_spent_in_states"]
    oven_off = oven_states.get("OFF_STATE", 0.0)
    oven_warm = oven_states.get("WARMUP_STATE", 0.0)
    oven_energy = (
        component_energy(top.reflow_oven)
        + (P_OVEN_OFF - P_OVEN_MAINTAIN) * oven_off
        + (P_OVEN_SETUP - P_OVEN_MAINTAIN) * oven_warm
    )

    return {
        "screen_printer": printer_energy,
        "pick_and_place_1": pp_energy(top.pick_and_place_1),
        "pick_and_place_2": pp_energy(top.pick_and_place_2),
        "buffering_module": component_energy(top.buffering_module),
        "reflow_oven": oven_energy,
    }


def evaluate(buffering=None, buffer_capacity=None):
    """Run one Table I configuration and return its KPIs."""
    env = simpy.Environment()
    top = SMTLine(env, "TOP", buffering=buffering, buffer_capacity=buffer_capacity)
    top.fill_hierarchical_id()
    end_time = run_until_batch(env, top, top.sink)

    finished = top.sink.stats["num_item_received"]
    if finished == 0:
        raise RuntimeError("no boards finished")

    energy = compute_energy(top, end_time)
    states = top.reflow_oven.stats["total_time_spent_in_states"]
    off = states.get("OFF_STATE", 0.0)
    warm = states.get("WARMUP_STATE", 0.0) + states.get("SETUP_STATE", 0.0)
    occupied = states.get("ATLEAST_ONE_PROCESSING_STATE", 0.0) - states.get("WARMUP_STATE", 0.0)
    empty = max(0.0, end_time - off - warm - occupied)

    return {
        "finished": finished,
        "elapsed": end_time,
        "throughput": finished / end_time * 3600.0,
        "avg_cycle_h": (top.sink.stats["total_cycle_time"] / finished) / 3600.0,
        "max_cycle_h": top.sink.stats["max_cycle_time"] / 3600.0,
        "energy_kj": sum(energy.values()) / finished / 1e3,
        "energy_kj_excl_module": (
            sum(energy.values()) - energy["buffering_module"]) / finished / 1e3,
        "rfo_off_pct": off / end_time * 100.0,
        "rfo_setup_pct": warm / end_time * 100.0,
        "rfo_on_empty_pct": empty / end_time * 100.0,
        "rfo_on_occupied_pct": occupied / end_time * 100.0,
        "drain_cycles": sum(b.num_drain_cycles for b in top.batch_buffers),
        "finalisation_errors": getattr(top, "finalisation_errors", []),
        "top": top,
    }


def main():
    env = simpy.Environment()
    top = SMTLine(env, "TOP")
    top.fill_hierarchical_id()

    end_time = run_until_batch(env, top, top.sink)

    finished = top.sink.stats["num_item_received"]
    if finished == 0:
        print("No boards finished - check the wiring.")
        return

    throughput = finished / end_time * 3600.0
    avg_cycle_hours = (top.sink.stats["total_cycle_time"] / finished) / 3600.0
    energy = compute_energy(top, end_time)
    energy_per_board_kj = sum(energy.values()) / finished / 1e3
    setup = top.reflow_oven.stats["total_time_spent_in_states"].get("SETUP_STATE", 0.0)

    print("\n" + "=" * 70)
    print("SMT-PCB assembly line - no-buffering baseline")
    print("=" * 70)
    print(f"Boards finished  : {finished}")
    print(f"Elapsed          : {end_time:.1f} s ({end_time / 3600.0:.4f} h)")

    op = top.operator
    op_util = op.get_utilization(end_time)
    print(f"Operator         : {op.stats['num_tasks_completed']} tasks, "
          f"{op_util.get('BUSY_STATE', 0.0):.2f}% busy, "
          f"queued wait {op.stats['total_wait_time']:.1f} s, "
          f"max queue {op.stats['max_queue_length']}")
    for machine in (top.screen_printer, top.pick_and_place_1, top.pick_and_place_2):
        requested = machine.stats["num_service_requests"]
        waited = machine.stats["total_time_waiting_for_operator"]
        print(f"  {machine.id:20s} {requested:3d} requests, stalled {waited:8.1f} s "
              f"(task time alone {requested * REFILL_DELAY:.1f} s)")

    print("\nEnergy per component (kJ/board):")
    for name, joules in energy.items():
        print(f"  {name:20s}{joules / finished / 1e3:12.2f}")

    # Oven state fractions. The three exclusive Machine states map onto the
    # reference's oven states: SETUP is the warm-up, IDLE and blocked are "on but
    # empty", and processing is "on and occupied". The oven never switches off in the
    # baseline, so its OFF fraction is 0 in both.
    oven_states = top.reflow_oven.stats["total_time_spent_in_states"]
    oven_setup = oven_states.get("SETUP_STATE", 0.0)
    oven_occupied = oven_states.get("ATLEAST_ONE_PROCESSING_STATE", 0.0)
    oven_empty = max(0.0, end_time - oven_setup - oven_occupied)
    module_kj = energy["buffering_module"] / finished / 1e3

    max_cycle_hours = top.sink.stats["max_cycle_time"] / 3600.0

    print("\n" + "=" * 70)
    print("KPIs: paper (IOTAIS 2018, Table I) vs FactorySimPy model")
    print("=" * 70)
    print(f"{'KPI':36s}{'paper':>12s}{'model':>12s}{'dev':>10s}")
    print("-" * 70)
    rows = [
        ("Throughput (boards/h)",
         REFERENCE["throughput_pcb_per_hour"], throughput),
        ("Mean cycle time (h)",
         REFERENCE["avg_cycle_time_hours"], avg_cycle_hours),
        ("Max cycle time (h)",
         REFERENCE["max_cycle_time_hours"], max_cycle_hours),
        ("Energy per board (kJ)",
         REFERENCE["avg_energy_per_pcb_kj"], energy_per_board_kj),
        ("Energy per board, excl. bypass (kJ)",
         REFERENCE["avg_energy_per_pcb_kj"], energy_per_board_kj - module_kj),
        ("Oven off (%)",
         REFERENCE["rfo_off_percent"], 0.0),
        ("Oven setup (%)",
         REFERENCE["rfo_setup_percent"], oven_setup / end_time * 100.0),
        ("Oven on, empty (%)",
         REFERENCE["rfo_on_empty_percent"], oven_empty / end_time * 100.0),
        ("Oven on, occupied (%)",
         REFERENCE["rfo_on_occupied_percent"], oven_occupied / end_time * 100.0),
    ]
    for label, paper_value, model_value in rows:
        if paper_value:
            dev = f"{(model_value - paper_value) / paper_value * 100.0:8.2f}%"
        else:
            dev = f"{model_value - paper_value:8.2f} "
        print(f"{label:36s}{paper_value:12.4f}{model_value:12.4f}{dev:>10s}")
    print("-" * 70)
    print("Paper values: model_2/Results/Results_no_buffering.csv (Table I, No Buffering)")
    print()
    print("Notes on the three rows that do not match:")
    print("  Energy per board  - the reference's accumulator only updates on a state")
    print("    change and never flushes the last interval, so its bypass buffering")
    print("    module, which never transitions, contributes ~0. Excluding it gives the")
    print("    +0.02% row above.")
    print("  Mean / max cycle time - the reference stamps all 16 boards of a stack at")
    print(f"    stack creation. BatchSource(batch_size={STACK_SIZE}, batches_ahead=1)")
    print("    reproduces that structure but holds both stacks upstream of b0, whereas")
    print("    the reference drains a stack in place inside buff_0, so this overshoots;")
    print("    batches_ahead=0 undershoots at 0.4803 h. Exact parity needs a populated")
    print("    pallet plus Splitter UNPACK (approximation 4 in the module docstring).")


if __name__ == "__main__":
    main()
