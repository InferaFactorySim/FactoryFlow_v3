
# TOP (SystemModel) is a machine feeding an oven through one accumulating stack. TOP holds 1 Source src (inter_arrival_time=8, blocking=True), 1 Machine M0 (processing_delay=8), 1 PowerCycledMachine oven (work_capacity=5, processing_delay=20, setup_time=300, power_down_when_idle=True), 1 Sink sink. 3 Buffers: e0 (capacity=1) src->M0, stack (capacity=30, admission=BATCH_DRAIN) M0->oven, e2 (capacity=1) oven->sink. stack withholds parts while filling, closes its entry once full and releases until completely empty; oven switches off whenever it has no part in progress and none waiting, and serves a 300-unit warm-up before the next part.

# importing required libraries
import simpy, sys, os
import factorysimpy
from factorysimpy.nodes.node import Node
from factorysimpy.nodes.machine import Machine
from factorysimpy.nodes.power_cycled_machine import PowerCycledMachine
from factorysimpy.edges.buffer import Buffer
from factorysimpy.nodes.source import Source
from factorysimpy.nodes.sink import Sink
from factorysimpy.utils.utils import draw_blockdiagram


# defining the model
# a machine, an accumulating buffer and an oven that powers down when idle
class SystemModel(Node):
    def __init__(self, env, id):
        super().__init__(env, id)
        # creating nodes

        # initializing source
        self.src = Source(env, id="src", inter_arrival_time=8, blocking=True)
        # adding source to child nodes
        self.add_child_node(self.src)

        # initializing the feeding machine
        self.M0 = Machine(env, id="M0", processing_delay=8)
        # adding machine to child nodes
        self.add_child_node(self.M0)

        # initializing the oven.
        # power_down_when_idle switches it off once it has no part in progress and
        # none waiting on any in edge; setup_time is the warm-up it must serve before
        # the next part. work_capacity is how many parts fit inside at once.
        self.oven = PowerCycledMachine(env, id="oven", work_capacity=5,
                                       processing_delay=20, setup_time=300,
                                       power_down_when_idle=True)
        # adding oven to child nodes
        self.add_child_node(self.oven)

        # initializing sink
        self.sink = Sink(env, id="sink")
        # adding sink to child nodes
        self.add_child_node(self.sink)

        # creating edges
        # initializing buffers.
        # admission="BATCH_DRAIN" makes the buffer withhold parts while it fills,
        # then close its entry once full and release until it is completely empty.
        self.e0 = Buffer(env, id="e0", capacity=1)
        self.stack = Buffer(env, id="stack", capacity=30, admission="BATCH_DRAIN")
        self.e2 = Buffer(env, id="e2", capacity=1)
        # adding edges to child edges
        self.add_child_edge([self.e0, self.stack, self.e2])

        # connecting nodes with edges
        self.e0.connect(self.src, self.M0)
        self.stack.connect(self.M0, self.oven)
        self.e2.connect(self.oven, self.sink)


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
# running the simulation for 5000 time units
TOP.run_simulation(5000)

# the number of fill-and-drain cycles is TOP.stack.num_drain_cycles, and the time
# the oven spent off and warming up is in
# TOP.oven.stats["total_time_spent_in_states"]["OFF_STATE"] and ["WARMUP_STATE"]
