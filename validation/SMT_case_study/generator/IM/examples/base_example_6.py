import simpy,sys, os
import factorysimpy
from factorysimpy.nodes.node import Node
from factorysimpy.nodes.machine import Machine
from factorysimpy.edges.buffer import Buffer
from factorysimpy.nodes.source import Source
from factorysimpy.nodes.sink import Sink
from factorysimpy.utils.file_loaders import load_func_from_file

# Default values from PARAMS
machine_processing_delay_default = 0.0
source_inter_arrival_rate_default = 1.0

class SeriesOfTenMachines(Node):
    def __init__(self, env, id):
        super().__init__(env, id)
        num_machines = 10

        self.source = Source(env, id="src", inter_arrival_time=source_inter_arrival_rate_default)
        self.add_child_node(self.source)

        self.machines = []
        # Create machines M1 to M10 and assign their processing delays.
        # load_func_from_file loads the DataFITR-calibrated generator for the node when a
        # matching plugin exists (filename = "<hierarchical_id>_process_delay.py"), otherwise
        # it returns the `default` constant. The helper hides the plugin directory and importlib.
        for i in range(num_machines):
            machine_id = f"M{i+1}"
            processing_delay = load_func_from_file(
                f"{id}.{machine_id}_process_delay.py",
                default=machine_processing_delay_default,
            )
            out_edge_selection = load_func_from_file(
                f"{id}.{machine_id}_out_edge_selection.py",
                default=0,
            )
            machine = Machine(env, id=machine_id, processing_delay=processing_delay, out_edge_selection=out_edge_selection)
            self.machines.append(machine)
        self.add_child_node(self.machines)

        self.sink = Sink(env, id="sink")
        self.add_child_node(self.sink)

        self.edges = []
        edge_idx = 0

        # Connect source to M1
        edge = Buffer(env, id=f"edge{edge_idx}")
        self.add_child_edge(edge)
        edge.connect(self.source, self.machines[0])
        self.edges.append(edge)
        edge_idx += 1

        # Connect M[i] to M[i+1]
        for i in range(num_machines - 1):
            edge = Buffer(env, id=f"edge{edge_idx}")
            self.add_child_edge(edge)
            edge.connect(self.machines[i], self.machines[i+1])
            self.edges.append(edge)
            edge_idx += 1

        # Connect M10 to sink
        edge = Buffer(env, id=f"edge{edge_idx}")
        self.add_child_edge(edge)
        edge.connect(self.machines[num_machines - 1], self.sink)
        self.edges.append(edge)

env = simpy.Environment()
TOP = SeriesOfTenMachines(env, "TOP")

TOP.fill_hierarchical_id()
TOP.get_hierarchical_info()
TOP.validate()
TOP.show_hierarchy()
TOP.run_simulation(25)