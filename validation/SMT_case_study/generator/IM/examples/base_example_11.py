
# TOP (SystemModel, stack_size=30) is a machine feeding an oven through two stacks in ping-pong. TOP holds 1 Source src (inter_arrival_time=8, blocking=True), 1 Machine M0 (processing_delay=8, out_edge_selection=FIRST_AVAILABLE), 1 Machine unload (processing_delay=0, in_edge_selection=FIRST_AVAILABLE, out_edge_selection=FIRST_AVAILABLE), 1 PowerCycledMachine oven (work_capacity=5, processing_delay=20, setup_time=300, power_down_when_idle=True), 1 Sink sink. 5 Buffers: e0 (capacity=1) src->M0, stack_a and stack_b (capacity=30 each, admission=BATCH_DRAIN) both M0->unload, e3 (capacity=1) unload->oven, e4 (capacity=1) oven->sink. Each stack withholds while filling and releases until empty; FIRST_AVAILABLE on both sides makes M0 fill whichever stack is accepting while unload draws from whichever is releasing, so filling overlaps draining and no controller is needed.

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
# two accumulating stacks in ping-pong feeding an oven that powers down when idle
class SystemModel(Node):
    def __init__(self, env, id, stack_size=30):
        super().__init__(env, id)
        # creating nodes

        # initializing source
        self.src = Source(env, id="src", inter_arrival_time=8, blocking=True)
        # adding source to child nodes
        self.add_child_node(self.src)

        # initializing the feeding machine.
        # It has two out edges, one per stack. out_edge_selection="FIRST_AVAILABLE"
        # sends each part to whichever stack is currently accepting.
        self.M0 = Machine(env, id="M0", processing_delay=8,
                          out_edge_selection="FIRST_AVAILABLE")
        # initializing the node that draws parts out of the stacks.
        # in_edge_selection="FIRST_AVAILABLE" takes from whichever stack is releasing.
        self.unload = Machine(env, id="unload", processing_delay=0,
                              in_edge_selection="FIRST_AVAILABLE",
                              out_edge_selection="FIRST_AVAILABLE")
        # adding machines to child nodes
        self.add_child_node([self.M0, self.unload])

        # initializing the oven
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
        # the two stacks. Each is BATCH_DRAIN, so it withholds parts while filling,
        # closes its entry once full and releases until empty. No controller is
        # needed: with FIRST_AVAILABLE on both sides the two alternate by themselves,
        # so filling and draining overlap and the line is not stalled during a drain.
        self.stack_a = Buffer(env, id="stack_a", capacity=stack_size,
                              admission="BATCH_DRAIN")
        self.stack_b = Buffer(env, id="stack_b", capacity=stack_size,
                              admission="BATCH_DRAIN")
        self.e0 = Buffer(env, id="e0", capacity=1)
        self.e3 = Buffer(env, id="e3", capacity=1)
        self.e4 = Buffer(env, id="e4", capacity=1)
        # adding edges to child edges
        self.add_child_edge([self.e0, self.stack_a, self.stack_b, self.e3, self.e4])

        # connecting nodes with edges
        self.e0.connect(self.src, self.M0)
        # both stacks run from the machine to the unloading node
        self.stack_a.connect(self.M0, self.unload)
        self.stack_b.connect(self.M0, self.unload)
        self.e3.connect(self.unload, self.oven)
        self.e4.connect(self.oven, self.sink)


# initiliating simpy environment
env = simpy.Environment()
# creating the model with stacks of 30
TOP = SystemModel(env, "TOP", stack_size=30)
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

# fill-and-drain cycles per stack are TOP.stack_a.num_drain_cycles and
# TOP.stack_b.num_drain_cycles
