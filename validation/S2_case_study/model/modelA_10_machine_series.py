"""Ten-machine serial Model A using the same runtime flow as Model C."""

import os
import sys

_factorysimpy_src = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
)
if _factorysimpy_src not in sys.path:
    sys.path.insert(0, _factorysimpy_src)

import simpy
from factorysimpy.utils.file_loaders import load_func_from_file
from factorysimpy.nodes.node import Node
from factorysimpy.nodes.machine import Machine
from factorysimpy.edges.buffer import Buffer
from factorysimpy.nodes.source import Source
from factorysimpy.nodes.sink import Sink


class SystemModel(Node):
    def __init__(self, env, id, telemetry=None):
        super().__init__(env, id)
        self.telemetry = telemetry

        self.source = Source(
            env, id="src", inter_arrival_time=1, telemetry=telemetry
        )
        self.sink = Sink(env, id="sink", telemetry=telemetry)
        self.add_child_node(self.source)
        self.add_child_node(self.sink)

        self.M = [
            Machine(env, id=f"M{i}", telemetry=telemetry)
            for i in range(1, 11)
        ]
        self.add_child_node(self.M)

        self.buffers = []
        for i in range(11):
            buffer = Buffer(env, id=f"B{i}", capacity=1, telemetry=telemetry)
            self.buffers.append(buffer)
            self.add_child_edge(buffer)

        self.buffers[0].connect(self.source, self.M[0])
        for i in range(9):
            self.buffers[i + 1].connect(self.M[i], self.M[i + 1])
        self.buffers[10].connect(self.M[9], self.sink)


def build_model(telemetry=None, delay_plugin_dir=None):
    """Build a fresh Model A instance and load the M1-M10 RVGs."""
    env = simpy.Environment()
    top = SystemModel(env, "TOP", telemetry=telemetry)
    top.fill_hierarchical_id()

    datafitr_folder = delay_plugin_dir or os.environ.get(
        "DELAY_PLUGIN_DIR",
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "gencodeforAMG_10"
        ),
    )
    datafitr_suffixes = {
        "_processing_delay_code.py": "processing_delay",
        "_process_delay.py": "processing_delay",
        "_processing_delay.py": "processing_delay",
        "_selected_in_edge.py": "in_edge_selection",
        "_selected_out_edge.py": "out_edge_selection",
        "_inter_arrival_time.py": "inter_arrival_time",
    }
    datafitr_nodes, _ = top.get_all_nodes_edges()
    datafitr_nodes_by_id = {
        node.id: node for node in datafitr_nodes.values()
    }
    try:
        datafitr_files = os.listdir(datafitr_folder)
    except OSError:
        datafitr_files = []

    for datafitr_file in sorted(datafitr_files):
        for datafitr_suffix, datafitr_attr in datafitr_suffixes.items():
            if not datafitr_file.endswith(datafitr_suffix):
                continue
            datafitr_id = datafitr_file[: -len(datafitr_suffix)]
            datafitr_node = datafitr_nodes.get(
                datafitr_id, datafitr_nodes_by_id.get(datafitr_id)
            )
            if datafitr_node is not None and hasattr(
                datafitr_node, datafitr_attr
            ):
                datafitr_default = getattr(datafitr_node, datafitr_attr)
                setattr(
                    datafitr_node,
                    datafitr_attr,
                    load_func_from_file(
                        datafitr_file,
                        default=datafitr_default,
                        delay_folder=datafitr_folder,
                    ),
                )
            break

    return env, top


def create_telemetry_from_environment():
    """Create RabbitMQ telemetry only when explicitly enabled."""
    if os.environ.get("MODELA_TELEMETRY", "0") != "1":
        return None

    from factorysimpy.utils.telemetry import RabbitMQSender, Telemetry

    telemetry_sender = RabbitMQSender(
        host=os.environ.get("RABBITMQ_HOST", "rabbitmq"),
        queue=os.environ.get("RABBITMQ_QUEUE", "factory_data"),
    )
    return Telemetry(
        telemetry_sender,
        system_name=os.environ.get("TELEMETRY_SYSTEM_NAME", "ModelA10"),
    )


def main():
    telemetry = create_telemetry_from_environment()
    _, top = build_model(telemetry=telemetry)
    top.run_simulation(float(os.environ.get("MODELA_SIM_TIME", "5000")))


if __name__ == "__main__":
    main()
