
# TOP (SystemModel) is a 2-machine line sharing one human operator. TOP holds 1 Source src (inter_arrival_time=5, blocking=True), 2 ServicedMachines M0 (processing_delay=6, services=[tool_change interval 40]) and M1 (processing_delay=7, services=[refill interval 25]), 1 HumanOperator operator (capacity=1; tasks tool_change@M0=60, refill@M1=60), 1 Sink sink. 3 Buffers (capacity=1 each): e[0] src->M0, e[1] M0->M1, e[2] M1->sink. A machine whose service falls due stalls until the operator is free and the 60-unit task completes; requests queue FIFO and an interrupted task resumes rather than restarts.

# importing required libraries
import simpy, sys, os
import factorysimpy
from factorysimpy.nodes.node import Node
from factorysimpy.nodes.serviced_machine import ServicedMachine
from factorysimpy.nodes.human_operator import HumanOperator
from factorysimpy.edges.buffer import Buffer
from factorysimpy.nodes.source import Source
from factorysimpy.nodes.sink import Sink
from factorysimpy.utils.utils import draw_blockdiagram


# defining the model
# two serviced machines sharing one operator
class SystemModel(Node):
    def __init__(self, env, id):
        super().__init__(env, id)
        # creating nodes

        # initializing the shared operator.
        # It stays idle until a machine requests a task, serves requests one at a
        # time in FIFO order, and resumes an interrupted task rather than restarting.
        self.operator = HumanOperator(env, id="operator")
        # adding operator to child nodes
        self.add_child_node(self.operator)

        # initializing source
        self.src = Source(env, id="src", inter_arrival_time=5, blocking=True)
        # adding source to child nodes
        self.add_child_node(self.src)

        # initializing serviced machines.
        # A service rule says WHEN service falls due, as parts processed since the
        # last service of that task. How long the task takes is set on the operator.
        self.M0 = ServicedMachine(env, id="M0", processing_delay=6,
                                  operator=self.operator,
                                  services=[{"task": "tool_change", "interval": 40}])
        self.M1 = ServicedMachine(env, id="M1", processing_delay=7,
                                  operator=self.operator,
                                  services=[{"task": "refill", "interval": 25}])
        # adding machines to child nodes
        self.add_child_node([self.M0, self.M1])

        # initializing sink
        self.sink = Sink(env, id="sink")
        # adding sink to child nodes
        self.add_child_node(self.sink)

        # assigning tasks to the operator, which fixes how long each one takes
        self.operator.assign_task(self.M0, "tool_change", 60)
        self.operator.assign_task(self.M1, "refill", 60)

        # creating edges
        # initializing buffers
        self.e = [Buffer(env, id=f"e[{i}]", capacity=1) for i in range(3)]
        # adding edges to child edges
        self.add_child_edge(self.e)

        # connecting nodes with edges
        self.e[0].connect(self.src, self.M0)
        self.e[1].connect(self.M0, self.M1)
        self.e[2].connect(self.M1, self.sink)


# initiliating simpy environment
env = simpy.Environment()
# creating the model
TOP = SystemModel(env, "TOP")
# filling hierarchical ids from TOP
TOP.fill_hierarchical_id()
# validating the model
TOP.validate()
# drawing block diagram
fig = draw_blockdiagram(TOP, "block_diagram")

import streamlit as st
st.graphviz_chart(fig.source)
# running the simulation for 2000 time units
TOP.run_simulation(2000)

# operator utilisation is available as TOP.operator.get_utilization(2000)
# and the time a machine stalled waiting for it as
# TOP.M0.stats["total_time_waiting_for_operator"]
