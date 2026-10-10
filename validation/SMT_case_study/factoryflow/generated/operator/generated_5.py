import simpy
from factorysimpy.nodes.node import Node
from factorysimpy.nodes.machine import Machine
from factorysimpy.nodes.source import Source
from factorysimpy.nodes.sink import Sink
from factorysimpy.nodes.batch_source import BatchSource
from factorysimpy.nodes.serviced_machine import ServicedMachine
from factorysimpy.nodes.human_operator import HumanOperator
from factorysimpy.edges.buffer import Buffer
from factorysimpy.edges.converyorbelt import ConveyorBelt
from factorysimpy.constants import (
    SETUP_STATE,
    IDLE_STATE,
    ALL_ACTIVE_BLOCKED_STATE,
    ATLEAST_ONE_PROCESSING_STATE,
)


class SystemModel(Node):
    def __init__(self, env, id):
        super().__init__(env, id)

        self.operator_1 = HumanOperator(env, id="operator_1")
        self.add_child_node(self.operator_1)

        self.source = BatchSource(
            env,
            id="source_1",
            batch_size=16,
            batches_ahead=1,
            inter_arrival_time=0,
            blocking=True,
        )
        self.line_loader = Machine(env, id="line_loader", processing_delay=2)

        self.screen_printer_counter = 0
        def screen_printer_delay():
            self.screen_printer_counter += 1
            delay = 18
            if self.screen_printer_counter % 2 == 0:
                delay += 28
            return delay

        self.screen_printer = ServicedMachine(
            env,
            id="screen_printer",
            processing_delay=screen_printer_delay,
            operator=self.operator_1,
            services=[{"task": "top_up", "interval": 100}],
        )
        self.pick_and_place_1 = ServicedMachine(
            env,
            id="pick_and_place_1",
            processing_delay=85,
            operator=self.operator_1,
            services=[{"task": "reel_change", "interval": 50}],
        )
        self.pick_and_place_2 = ServicedMachine(
            env,
            id="pick_and_place_2",
            processing_delay=50,
            operator=self.operator_1,
            services=[{"task": "reel_change", "interval": 50}],
        )
        self.buffering_module = Machine(env, id="buffering_module", processing_delay=0)
        self.reflow_oven = Machine(
            env,
            id="reflow_oven",
            work_capacity=10,
            processing_delay=50,
            node_setup_time=3600,
        )
        self.sink = Sink(env, id="sink_1")

        self.add_child_node(
            [
                self.source,
                self.line_loader,
                self.screen_printer,
                self.pick_and_place_1,
                self.pick_and_place_2,
                self.buffering_module,
                self.reflow_oven,
                self.sink,
            ]
        )

        self.operator_1.assign_task(self.screen_printer, "top_up", 60)
        self.operator_1.assign_task(self.pick_and_place_1, "reel_change", 60)
        self.operator_1.assign_task(self.pick_and_place_2, "reel_change", 60)

        self.b0 = Buffer(env, id="b0", capacity=1)
        self.b1 = Buffer(env, id="b1", capacity=1)
        self.belt_sp_pp1 = ConveyorBelt(env, id="belt_sp_pp1", capacity=3, delay=1)
        self.b2 = Buffer(env, id="b2", capacity=1)
        self.b3 = Buffer(env, id="b3", capacity=1)
        self.belt_bm_rfo = ConveyorBelt(env, id="belt_bm_rfo", capacity=3, delay=1)
        self.b4 = Buffer(env, id="b4", capacity=1)

        self.add_child_edge(
            [
                self.b0,
                self.b1,
                self.belt_sp_pp1,
                self.b2,
                self.b3,
                self.belt_bm_rfo,
                self.b4,
            ]
        )

        self.b0.connect(self.source, self.line_loader)
        self.b1.connect(self.line_loader, self.screen_printer)
        self.belt_sp_pp1.connect(self.screen_printer, self.pick_and_place_1)
        self.b2.connect(self.pick_and_place_1, self.pick_and_place_2)
        self.b3.connect(self.pick_and_place_2, self.buffering_module)
        self.belt_bm_rfo.connect(self.buffering_module, self.reflow_oven)
        self.b4.connect(self.reflow_oven, self.sink)

        self.screen_printer.set_power_ratings(
            {
                IDLE_STATE: 100,
                ALL_ACTIVE_BLOCKED_STATE: 100,
                ATLEAST_ONE_PROCESSING_STATE: 500,
            }
        )
        self.pick_and_place_1.set_power_ratings(
            {
                IDLE_STATE: 100,
                ALL_ACTIVE_BLOCKED_STATE: 100,
                ATLEAST_ONE_PROCESSING_STATE: 500,
            }
        )
        self.pick_and_place_2.set_power_ratings(
            {
                IDLE_STATE: 100,
                ALL_ACTIVE_BLOCKED_STATE: 100,
                ATLEAST_ONE_PROCESSING_STATE: 500,
            }
        )
        self.buffering_module.set_power_ratings(
            {
                IDLE_STATE: 250,
                ALL_ACTIVE_BLOCKED_STATE: 250,
                ATLEAST_ONE_PROCESSING_STATE: 250,
            }
        )
        self.reflow_oven.set_power_ratings(
            {
                SETUP_STATE: 33000,
                IDLE_STATE: 25800,
                ALL_ACTIVE_BLOCKED_STATE: 25800,
                ATLEAST_ONE_PROCESSING_STATE: 25800,
            }
        )


env = simpy.Environment()
TOP = SystemModel(env, "TOP")
TOP.fill_hierarchical_id()
TOP.validate()
TOP.run_simulation(100000)