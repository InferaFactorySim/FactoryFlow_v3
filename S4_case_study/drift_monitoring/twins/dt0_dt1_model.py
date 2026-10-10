"""
ModelDescription:
# S4 — system description

Ten identical production lines in parallel, sharing one source and one sink.
Use every name exactly as given; these are the ids the plant reports in its
telemetry. Full ids are prefixed by their containers, e.g. `TOP.Line_05.M02`.

## Hierarchy

Top-level container `TOP` holds the source `src`, the sink `sink`, ten line
containers `Line_00` … `Line_09`, and the entry and exit buffers. All lines are
identical: build one line class and instantiate it ten times.

## Nodes

- `src`: Source, `inter_arrival_time=0.75`, `blocking=True`,
  `out_edge_selection="ROUND_ROBIN"` over `B_IN_00` … `B_IN_09` in that order.
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
1. The description states that machine `processing_delay` is inferred from telemetry. It is assumed that if telemetry data is not available for a specific machine, a default processing delay value will be used.
2. The input edge selection policy for the `sink` node, which receives from all ten `B_OUT` buffers, is not specified. It is assumed to use the default policy of accepting parts from any connected input edge as they become available.
3. For all components, any parameters not explicitly defined in the description (e.g., `setup_time` for `Machine` nodes, `admission` policy for `ConveyorBelt` edges) are assumed to use their default values as provided by the simulation library.
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
from factorysimpy.nodes.node import Node
from factorysimpy.nodes.source import Source
from factorysimpy.nodes.sink import Sink
from factorysimpy.nodes.machine import Machine
from factorysimpy.edges.buffer import Buffer
from factorysimpy.edges.converyorbelt import ConveyorBelt

class Line(Node):
    def __init__(self, env, id):
        super().__init__(env, id)
        
        power_ratings = [
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

        self.M = []
        for i in range(10):
            machine = Machine(
                env, 
                id=f"M{i:02d}", 
                work_capacity=1, 
                blocking=True, 
                in_edge_selection="FIRST_AVAILABLE", 
                out_edge_selection="ROUND_ROBIN"
            )
            machine.set_power_ratings(power_ratings[i])
            self.M.append(machine)
        self.add_child_node(self.M)

        edge_specs = {
            "CV00_01": (ConveyorBelt, {"capacity": 2, "delay": 0.5}),
            "B01_02": (Buffer, {"capacity": 3}),
            "B02_03": (Buffer, {"capacity": 3}),
            "CV03_04": (ConveyorBelt, {"capacity": 2, "delay": 0.5}),
            "BMID04_05": (Buffer, {"capacity": 10}),
            "CV05_06": (ConveyorBelt, {"capacity": 2, "delay": 0.5}),
            "B06_07": (Buffer, {"capacity": 3}),
            "B07_08": (Buffer, {"capacity": 3}),
            "CV08_09": (ConveyorBelt, {"capacity": 2, "delay": 0.5}),
        }
        
        conveyor_power = {"MOVING_STATE": 400, "STALLED_STATE": 120, "EMPTY_STATE": 50}
        self.edges = {}
        for name, (edge_type, params) in edge_specs.items():
            edge = edge_type(env, id=name, **params)
            if isinstance(edge, ConveyorBelt):
                edge.set_power_ratings(conveyor_power)
            self.edges[name] = edge
            
            src_idx = int(name.split('_')[0][-2:])
            dest_idx = int(name.split('_')[1])
            edge.connect(self.M[src_idx], self.M[dest_idx])
        
        self.add_child_edge(list(self.edges.values()))

class SystemModel(Node):
    def __init__(self, env, id):
        super().__init__(env, id)
        
        self.source = Source(
            env, 
            id="src", 
            inter_arrival_time=0.75, 
            blocking=True, 
            out_edge_selection="ROUND_ROBIN"
        )
        self.add_child_node(self.source)
        
        self.sink = Sink(env, id="sink")
        self.add_child_node(self.sink)
        
        self.Lines = [Line(env, id=f"Line_{i:02d}") for i in range(10)]
        self.add_child_node(self.Lines)
        
        self.B_IN = []
        self.B_OUT = []
        for i in range(10):
            b_in = Buffer(env, id=f"B_IN_{i:02d}", capacity=5)
            b_in.connect(self.source, self.Lines[i].M[0])
            self.B_IN.append(b_in)
            
            b_out = Buffer(env, id=f"B_OUT_{i:02d}", capacity=5)
            b_out.connect(self.Lines[i].M[9], self.sink)
            self.B_OUT.append(b_out)
            
        self.add_child_edge(self.B_IN)
        self.add_child_edge(self.B_OUT)

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
TOP.run_simulation(10000)