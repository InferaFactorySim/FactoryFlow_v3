"""
ModelDescription:
# S4 — system description after the routing change

Ten identical production lines in parallel, sharing one source and one sink.
Use every name exactly as given; these are the ids the plant reports in its
telemetry. Full ids are prefixed by their containers, e.g. `TOP.Line_05.M02`.

The source no longer feeds `Line_00`. `Line_00`, its machines and edges, and
`B_IN_00` and `B_OUT_00` all remain in the model; they simply receive no items.
Only the source's routing differs from the previous model.

## Hierarchy

Implement the top level as a class `SystemModel`, and create the model as
`TOP = SystemModel(env, "TOP")` followed by `TOP.fill_hierarchical_id()`. `TOP` holds the source `src`, the sink `sink`, ten line
containers `Line_00` … `Line_09`, and the entry and exit buffers. All lines are
identical: build one line class and instantiate it ten times.

## Nodes

- `src`: Source, `inter_arrival_time=0.75`, `blocking=True`. Connect its ten
  outgoing edges in the order `B_IN_00` … `B_IN_09`, so `B_IN_rr` is out-edge
  index `rr`. `out_edge_selection` is a generator yielding the indices
  1, 2, …, 9, 1, 2, … in turn: round robin over `B_IN_01` … `B_IN_09`, never
  index 0 (`B_IN_00`).
- Machines `M00` … `M09` in each line, in flow order (100 in all), each with
  `work_capacity=1`, `blocking=True`, `in_edge_selection="FIRST_AVAILABLE"`,
  `out_edge_selection="ROUND_ROBIN"`. `processing_delay` is not given: it is
  inferred from telemetry per machine.
- `sink`: Sink receiving from all ten lines.

## Edges within each line

Machines are joined in series, `M00` → `M01` → … → `M09`:

- ConveyorBelt, `capacity=2`, `delay=0.5`: `CV00_01`, `CV03_04`, `CV05_06`,
  `CV08_09`.
- Buffer, `capacity=3`: `B01_02`, `B02_03`, `B06_07`, `B07_08`.
- Buffer, `capacity=10`: `BMID04_05` (mid-line bank).

Each edge joins the two machines its name gives, e.g. `CV03_04` joins `M03` to
`M04`.

## Edges owned by `TOP`

For each line `rr` = `00` … `09`:

- Buffer `B_IN_rr`, `capacity=5`: `src` → `Line_rr.M00`.
- Buffer `B_OUT_rr`, `capacity=5`: `Line_rr.M09` → `sink`.

All buffers are FIFO with no delay.

## Power ratings (W)

Set with `set_power_ratings` on four machine states only:
`ATLEAST_ONE_PROCESSING_STATE`, `IDLE_STATE`, `ALL_ACTIVE_BLOCKED_STATE`,
`SETUP_STATE`. Ratings depend only on position, given as processing / idle /
blocked / setup:

- `M00` 7000/800/900/3000; `M01` 6500/750/850/2800; `M02` 5500/700/800/2500
- `M03` 8000/900/1000/3500; `M04` 12000/1500/1700/5000; `M05` 5000/600/700/2200
- `M06` 4000/500/600/1800; `M07` 6000/700/800/2600; `M08` 5800/700/800/2500
- `M09` 7500/850/950/3200

Every ConveyorBelt: `MOVING_STATE` 400, `STALLED_STATE` 120, `EMPTY_STATE` 50.
Buffers draw no power.


 MODEL ASSUMPTIONS:
1. The description states that machine `processing_delay` is inferred from telemetry. Since no telemetry data is provided, a default value will be used for this parameter for all machines.
2. A class name, `ProductionLine`, will be used for the identical production line model, as this is not specified in the description.
3. The `in_edge_selection` policy for the `sink` node, which receives from ten buffers, is not specified. The default policy of "FIRST_AVAILABLE" will be used.
4. The total simulation run time is not specified. A default value will be used.
"""

import os
import sys

# The exported model lives in digital_twin/ and FactorySimPy is in sibling src/.
_factorysimpy_src = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "src")
)
if _factorysimpy_src not in sys.path:
    sys.path.insert(0, _factorysimpy_src)
from factorysimpy.utils.file_loaders import load_func_from_file
from factorysimpy.utils.file_loaders import (
    load_policy_from_file, materialize_edge_policy,
)
import simpy
import itertools
from factorysimpy.nodes.node import Node
from factorysimpy.nodes.source import Source
from factorysimpy.nodes.sink import Sink
from factorysimpy.nodes.machine import Machine
from factorysimpy.edges.buffer import Buffer
from factorysimpy.edges.converyorbelt import ConveyorBelt


class ProductionLine(Node):
    def __init__(self, env, id):
        super().__init__(env, id)
        self.M = [
            Machine(
                env,
                id=f"M{i:02d}",
                work_capacity=1,
                processing_delay=1.0,
                blocking=True,
                in_edge_selection="FIRST_AVAILABLE",
                out_edge_selection="ROUND_ROBIN",
            )
            for i in range(10)
        ]
        self.add_child_node(self.M)

        self.CV00_01 = ConveyorBelt(env, id="CV00_01", capacity=2, delay=0.5)
        self.B01_02 = Buffer(env, id="B01_02", capacity=3)
        self.B02_03 = Buffer(env, id="B02_03", capacity=3)
        self.CV03_04 = ConveyorBelt(env, id="CV03_04", capacity=2, delay=0.5)
        self.BMID04_05 = Buffer(env, id="BMID04_05", capacity=10)
        self.CV05_06 = ConveyorBelt(env, id="CV05_06", capacity=2, delay=0.5)
        self.B06_07 = Buffer(env, id="B06_07", capacity=3)
        self.B07_08 = Buffer(env, id="B07_08", capacity=3)
        self.CV08_09 = ConveyorBelt(env, id="CV08_09", capacity=2, delay=0.5)

        self.edges = [
            self.CV00_01, self.B01_02, self.B02_03, self.CV03_04,
            self.BMID04_05, self.CV05_06, self.B06_07, self.B07_08,
            self.CV08_09
        ]
        self.add_child_edge(self.edges)

        for i in range(9):
            self.edges[i].connect(self.M[i], self.M[i + 1])

        machine_power_ratings = [
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
        for i, machine in enumerate(self.M):
            machine.set_power_ratings(machine_power_ratings[i])

        conveyor_power_ratings = {"MOVING_STATE": 400, "STALLED_STATE": 120, "EMPTY_STATE": 50}
        for edge in self.edges:
            if isinstance(edge, ConveyorBelt):
                edge.set_power_ratings(conveyor_power_ratings)


class SystemModel(Node):
    def __init__(self, env, id):
        super().__init__(env, id)

        def source_router():
            yield from itertools.cycle(range(1, 10))

        self.source = Source(
            env,
            id="src",
            inter_arrival_time=0.75,
            blocking=True,
            out_edge_selection=source_router(),
        )
        self.sink = Sink(env, id="sink")
        self.add_child_node([self.source, self.sink])

        self.Lines = [ProductionLine(env, id=f"Line_{i:02d}") for i in range(10)]
        self.add_child_node(self.Lines)

        self.B_IN = [Buffer(env, id=f"B_IN_{i:02d}", capacity=5) for i in range(10)]
        self.B_OUT = [Buffer(env, id=f"B_OUT_{i:02d}", capacity=5) for i in range(10)]
        self.add_child_edge(self.B_IN)
        self.add_child_edge(self.B_OUT)

        for i in range(10):
            self.B_IN[i].connect(self.source, self.Lines[i].M[0])
            self.B_OUT[i].connect(self.Lines[i].M[9], self.sink)


env = simpy.Environment()
TOP = SystemModel(env, "TOP")
TOP.fill_hierarchical_id()
# DataFITR RVG loading (embedded by FactoryFlow for portable exports)
_datafitr_folder = os.environ.get(
    "DELAY_PLUGIN_DIR", os.path.dirname(os.path.abspath(__file__))
)
_datafitr_suffixes = {
    "_process_delay.py": "processing_delay",
    "_processing_delay.py": "processing_delay",
    "_selected_in_edge.py": "in_edge_selection",
    "_selected_out_edge.py": "out_edge_selection",
    "_in_edge_selection.py": "in_edge_selection",
    "_out_edge_selection.py": "out_edge_selection",
    "_inter_arrival_time.py": "inter_arrival_time",
}
_datafitr_nodes, _ = TOP.get_all_nodes_edges()
try:
    _datafitr_files = os.listdir(_datafitr_folder)
except OSError:
    _datafitr_files = []
for _datafitr_file in sorted(_datafitr_files):
    for _datafitr_suffix, _datafitr_attr in _datafitr_suffixes.items():
        if not _datafitr_file.endswith(_datafitr_suffix):
            continue
        _datafitr_id = _datafitr_file[:-len(_datafitr_suffix)]
        _datafitr_node = _datafitr_nodes.get(_datafitr_id)
        if _datafitr_node is not None and hasattr(_datafitr_node, _datafitr_attr):
            _datafitr_default = getattr(_datafitr_node, _datafitr_attr)
            _datafitr_policy = (
                load_policy_from_file(_datafitr_file, delay_folder=_datafitr_folder)
                if _datafitr_attr in {"in_edge_selection", "out_edge_selection"}
                else None
            )
            if _datafitr_policy is not None:
                setattr(
                    _datafitr_node,
                    _datafitr_attr,
                    materialize_edge_policy(_datafitr_policy, _datafitr_node, _datafitr_attr),
                )
                break
            _datafitr_loaded = load_func_from_file(
                _datafitr_file,
                default=_datafitr_default,
                delay_folder=_datafitr_folder,
            )
            if (_datafitr_loaded is not _datafitr_default and
                    _datafitr_attr in {"in_edge_selection", "out_edge_selection"}):
                _datafitr_edge_count = len(
                    _datafitr_node.in_edges if _datafitr_attr == "in_edge_selection"
                    else _datafitr_node.out_edges
                )
                _datafitr_loaded = (
                    (int(value) % _datafitr_edge_count for value in _datafitr_loaded)
                    if hasattr(_datafitr_loaded, "__next__")
                    else (lambda fn=_datafitr_loaded, count=_datafitr_edge_count: int(fn()) % count)
                )
            setattr(_datafitr_node, _datafitr_attr, _datafitr_loaded)
        break
TOP.validate()
TOP.run_simulation(1000)