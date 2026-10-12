"""Additive extension of the AMG schema and system prompt.

`AMG_with_langgraph.PARAMS` declares Source, Machine, Sink, Node and Buffer. Lines that
use conveyors, batch-fed sources, operator-serviced machines or power-cycled thermal
units cannot be expressed with that set. This module appends the missing components to a
copy of the existing schema, and appends the corresponding contracts to a copy of the
existing system prompt, so nothing in AMG_with_langgraph is modified and experiments that
import PARAMS or SYSTEM_PROMPT directly keep their current behaviour.

Usage:
    from IM.amg_extensions import extended_params, EXTENDED_SYSTEM_PROMPT
    state = run_initial_from_description(
        system_prompt=EXTENDED_SYSTEM_PROMPT,
        description=description,
        folder="IM/examples",
        params=extended_params(),
    )
"""

import copy

from IM.AMG_with_langgraph import PARAMS as BASE_PARAMS, SYSTEM_PROMPT as BASE_PROMPT


# Components absent from the base schema, with the parameters that matter for modelling.
EXTRA_NODE_TYPES = ["BatchSource", "ServicedMachine", "PowerCycledMachine",
                    "HumanOperator"]
EXTRA_EDGE_TYPES = ["ConveyorBelt"]

EXTRA_NODE_PARAMS = {
    "BatchSource": ["id", "batch_size", "batches_ahead", "inter_arrival_time",
                    "blocking", "flow_item_type"],
    "ServicedMachine": ["id", "work_capacity", "processing_delay", "operator",
                        "services", "node_setup_time", "in_edge_selection",
                        "out_edge_selection", "blocking"],
    "PowerCycledMachine": ["id", "work_capacity", "processing_delay", "setup_time",
                           "power_down_when_idle", "start_powered", "idle_poll",
                           "in_edge_selection", "out_edge_selection", "blocking"],
    "HumanOperator": ["id", "capacity"],
}

EXTRA_EDGE_PARAMS = {
    "ConveyorBelt": ["id", "capacity", "delay"],
}

# Parameters the base schema omits for components it already declares.
ADDITIONAL_BASE_PARAMS = {
    "Source": ["inter_arrival_time", "blocking", "out_edge_selection",
               "flow_item_type"],
    "Machine": ["node_setup_time"],
}
ADDITIONAL_BASE_EDGE_PARAMS = {
    "Buffer": ["admission", "release_threshold"],
}


def extended_params():
    """Return a copy of the base schema with the additional components appended."""
    params = copy.deepcopy(BASE_PARAMS)

    for node_type in EXTRA_NODE_TYPES:
        if node_type not in params["nodes_supported_type"]:
            params["nodes_supported_type"].append(node_type)
    for edge_type in EXTRA_EDGE_TYPES:
        if edge_type not in params["edges_supported_type"]:
            params["edges_supported_type"].append(edge_type)

    params["node_params"].update(copy.deepcopy(EXTRA_NODE_PARAMS))
    params["edge_params"].update(copy.deepcopy(EXTRA_EDGE_PARAMS))

    # Append missing parameters to components the base schema already declares,
    # preserving whatever order it used.
    for node_type, extra in ADDITIONAL_BASE_PARAMS.items():
        existing = params["node_params"].setdefault(node_type, ["id"])
        for name in extra:
            if name not in existing:
                existing.append(name)
    for edge_type, extra in ADDITIONAL_BASE_EDGE_PARAMS.items():
        existing = params["edge_params"].setdefault(edge_type, ["id"])
        for name in extra:
            if name not in existing:
                existing.append(name)

    # The base schema names the Source arrival parameter "inter_arrival_rate"; the
    # constructor argument is inter_arrival_time. Drop the stale name so the generator
    # is not offered a parameter that does not exist.
    source_params = params["node_params"].get("Source", [])
    params["node_params"]["Source"] = [p for p in source_params
                                       if p != "inter_arrival_rate"]

    return params


PROMPT_EXTENSION = """

Additional components
Besides Source, Machine, Sink and Buffer, the following components are available and
should be used when the description calls for them.

ConveyorBelt is an edge, used exactly like Buffer and connected with connect(src, dest).
It takes capacity, the number of belt slots, and delay, the time for one slot movement.
It does not accumulate: if the item in the last slot is not collected the whole belt
stalls. Import it as: from factorysimpy.edges.converyorbelt import ConveyorBelt
Note the spelling of that module file name; factorysimpy.edges.conveyor_belt does not
exist.

BatchSource is a Source that creates batch_size items at one time instant, all sharing
one creation timestamp, and releases them one at a time as space becomes available. Use
it when items arrive in stacks, magazines or trays rather than individually.
batches_ahead is how many further batches may be prepared while the current one drains,
and blocking must be True. inter_arrival_time applies once per batch, not per item.
Import: from factorysimpy.nodes.batch_source import BatchSource

HumanOperator is a node that is not part of the item flow: it has no in_edges or
out_edges and is not connected by any edge. It is a shared resource that machines
interrupt when they need a service task. Declare each task with
operator.assign_task(machine, task_name, delay), which is what fixes the task duration.
Import: from factorysimpy.nodes.human_operator import HumanOperator

ServicedMachine is a Machine that periodically needs a task performed by a HumanOperator,
such as a refill or a tool change. Pass operator and a services list of
{"task": name, "interval": items_between_services} entries. The machine decides when
service is due; the operator decides how long it takes. A machine waiting for a busy
operator stalls, so contention between machines sharing one operator is modelled.
Import: from factorysimpy.nodes.serviced_machine import ServicedMachine

PowerCycledMachine is a Machine that switches itself off when it has no work and must
warm up again before its next item. Use it only for a unit that actually cycles, that is
one the description says switches off, powers down or shuts down when idle or between
batches. Pass setup_time, the warm-up duration, and power_down_when_idle=True; use
start_powered=False if it also begins cold. setup_time may be a callable, which may read
the machine's time_since_power_off attribute if the warm-up depends on how long it has
been off. Setting setup_time with start_powered=True and power_down_when_idle=False is
rejected, because the warm-up could never happen.
Import: from factorysimpy.nodes.power_cycled_machine import PowerCycledMachine

Choosing between the two for a unit with a warm-up: if the description says the unit
warms up once before it starts and does not say it ever switches off again, it is a
plain Machine with node_setup_time set to the warm-up, not a PowerCycledMachine. A
Machine serves node_setup_time before it accepts any item, which is what a heated
chamber does; do not use PowerCycledMachine merely because a warm-up is mentioned.

Buffer admission
Buffer takes an admission parameter that controls when items may enter and leave:
"CONTINUOUS" is the default and admits an item whenever a slot is free; "BATCH_DRAIN"
withholds items while filling, then closes its entry once full and releases until
completely empty, which is how a stack feeding a batch process behaves; "BATCH_RELEASE"
withholds items until release_threshold have accumulated and then releases until empty
while still accepting new ones. Two BATCH_DRAIN buffers between the same pair of nodes,
with out_edge_selection and in_edge_selection set to "FIRST_AVAILABLE" on the nodes
either side, give two stacks working in ping-pong with no controller needed.

Contracts to observe
A processing_delay, inter_arrival_time or setup_time callable is invoked with no
arguments. If the value depends on how many items a component has processed, close over
a counter created in the model's __init__ rather than writing a function that takes the
machine as a parameter or that reads the component's stats.
Energy is not modelled by any component. If the description gives power ratings, declare
them with component.set_power_ratings({state_name: watts}) after the component is
created. For a Machine the states that partition its time are SETUP_STATE, IDLE_STATE,
ALL_ACTIVE_BLOCKED_STATE and ATLEAST_ONE_PROCESSING_STATE; do not also rate
ALL_ACTIVE_PROCESSING_STATE or ATLEAST_ONE_BLOCKED_STATE, which overlap with those.
"""

EXTENDED_SYSTEM_PROMPT = BASE_PROMPT + PROMPT_EXTENSION
