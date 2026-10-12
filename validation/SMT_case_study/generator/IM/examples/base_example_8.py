
# TOP (SystemModel, n=3) is a linear line fed by a batch source. TOP holds 1 BatchSource src (batch_size=20, batches_ahead=1, inter_arrival_time=100, blocking=True), 3 Machines M[0]..M[2] (processing_delay=4, work_capacity=1), 1 Sink sink. 4 Buffers (capacity=1 each): e[0] src->M[0], e[1] M[0]->M[1], e[2] M[1]->M[2], e[3] M[2]->sink. src creates 20 items at one instant sharing one creation timestamp and releases them one at a time as space frees, preparing one further batch while the current one drains.

# importing required libraries
import simpy, sys, os
import factorysimpy
from factorysimpy.nodes.node import Node
from factorysimpy.nodes.machine import Machine
from factorysimpy.edges.buffer import Buffer
from factorysimpy.nodes.batch_source import BatchSource
from factorysimpy.nodes.sink import Sink
from factorysimpy.utils.utils import draw_blockdiagram


# defining the model
# linear model fed by a batch source
class SystemModel(Node):
    def __init__(self, env, id, n=3):
        super().__init__(env, id)
        # creating nodes

        # initializing a batch source.
        # batch_size parts are created together at one time instant and share one
        # creation timestamp, then released one at a time as space becomes available.
        # batches_ahead=1 prepares one further stack while the current one is fed.
        # blocking must be True because a batch is held until it has been released.
        self.src = BatchSource(env, id="src", batch_size=20, batches_ahead=1,
                               inter_arrival_time=100, blocking=True)
        # adding source to child nodes
        self.add_child_node(self.src)

        # initializing machines
        self.M = [Machine(env, id=f"M[{i}]", processing_delay=4) for i in range(n)]
        # adding machines to child nodes
        self.add_child_node(self.M)

        # initializing sink
        self.sink = Sink(env, id="sink")
        # adding sink to child nodes
        self.add_child_node(self.sink)

        # creating edges
        # initializing buffers
        self.e = [Buffer(env, id=f"e[{i}]", capacity=1) for i in range(n+1)]
        # adding edges to child edges
        self.add_child_edge(self.e)

        # connecting nodes with edges
        # src to first machine
        self.e[0].connect(self.src, self.M[0])
        # machines in between
        for i in range(n-1):
            self.e[i+1].connect(self.M[i], self.M[i+1])
        # last machine to sink
        self.e[n].connect(self.M[n-1], self.sink)


# initiliating simpy environment
env = simpy.Environment()
# creating the model with 3 machines
TOP = SystemModel(env, "TOP", n=3)
# filling hierarchical ids from TOP
TOP.fill_hierarchical_id()
# validating the model
TOP.validate()
# drawing block diagram
fig = draw_blockdiagram(TOP, "block_diagram")

import streamlit as st
st.graphviz_chart(fig.source)
# running the simulation for 500 time units
TOP.run_simulation(500)

# number of stacks created is available as
# TOP.src.stats["num_batches_generated"]
