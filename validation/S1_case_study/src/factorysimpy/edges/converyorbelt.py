"""A synchronous, slotted conveyor edge.

This module intentionally keeps the historical file name (``converyorbelt``),
but exports the correctly named :class:`ConveyorBelt` class.
"""

from factorysimpy.base.buffer_store import BufferStore
from factorysimpy.edges.edge import Edge


class ConveyorBelt(Edge):
    """Move items one slot at a time between two connected nodes.

    The input and output are reservable one-place stores, so the edge uses the
    same ``reserve -> yield -> put/get`` protocol as :class:`Buffer`.  On every
    tick all slots move together.  If the output has not been collected, the
    complete belt stalls (the behaviour of ``cb_ref.py``).

    Parameters
    ----------
    env : simpy.Environment
        Simulation environment.
    id : str
        Edge identifier.
    capacity : int
        Number of physical belt slots (at least two).
    delay : int, float, generator, or callable
        Time taken for one slot movement.  Every sampled value must be > 0.
    telemetry : optional
        FactorySimPy telemetry collector.
    start_time : int or float
        Time at which the belt process starts ticking.
    """

    def __init__(self, env, id, capacity, delay=1, telemetry=None, start_time=0):
        super().__init__(env, id, capacity)
        if capacity < 2:
            raise ValueError("capacity (number of conveyor slots) must be at least 2")
        if not (callable(delay) or hasattr(delay, "__next__") or isinstance(delay, (int, float))):
            raise ValueError("delay must be an int, float, generator, or callable")
        if not isinstance(start_time, (int, float)) or start_time < 0:
            raise ValueError("start_time must be a non-negative number")

        self.num_stages = capacity
        self.delay = delay
        self.delay_per_slot = delay
        self.delay_per_stage = delay  # reference-model compatibility
        self.start_time = start_time
        self.telemetry = telemetry

        self.input_buf = BufferStore(env, capacity=1, mode="FIFO")
        self.output_buf = BufferStore(env, capacity=1, mode="FIFO")
        # Existing node code identifies a ConveyorBelt's get resource as .belt.
        self.belt = self.output_buf
        # Sink uses ``inbuiltstore`` directly for its input-edge reservation.
        # Point it at conveyor egress without changing Buffer or node behavior.
        self.inbuiltstore = self.output_buf
        self.stages = [None] * capacity

        self.state = "EMPTY_STATE"
        self.stats = {
            "last_state_change_time": env.now,
            "time_averaged_num_of_items_in_conveyor": 0.0,
            "total_time_spent_in_states": {
                "EMPTY_STATE": 0.0,
                "MOVING_STATE": 0.0,
                "STALLED_STATE": 0.0,
            },
        }
        self._last_level_change_time = env.now
        self._last_occupancy = 0
        self._weighted_occupancy = 0.0
        self.process = env.process(self.behaviour())

    def __str__(self):
        return self.id

    def initial_test(self):
        assert self.src_node is not None, f"ConveyorBelt '{self.id}' must have at least 1 src_node."
        assert self.dest_node is not None, f"ConveyorBelt '{self.id}' must have at least 1 dest_node."

    def update(self, params):
        """Update existing attributes, matching the other configurable edges."""
        for name, value in params.items():
            if hasattr(self, name):
                setattr(self, name, value)

    @staticmethod
    def _store_occupancy(store):
        return len(store.items) + len(store.ready_items)

    def _update_occupancy_stats(self):
        now = self.env.now
        self._weighted_occupancy += self._last_occupancy * (now - self._last_level_change_time)
        self._last_level_change_time = now
        self._last_occupancy = self.occupancy()
        self.stats["time_averaged_num_of_items_in_conveyor"] = (
            self._weighted_occupancy / now if now > 0 else 0.0
        )

    def update_final_conveyor_avg_content(self, simulation_end_time):
        interval = simulation_end_time - self._last_level_change_time
        if interval < 0:
            raise ValueError("simulation_end_time cannot be before the last update")
        self._weighted_occupancy += self._last_occupancy * interval
        self._last_level_change_time = simulation_end_time
        self.stats["time_averaged_num_of_items_in_conveyor"] = (
            self._weighted_occupancy / simulation_end_time if simulation_end_time > 0 else 0.0
        )

    def _set_state(self, state):
        if state != self.state:
            self.update_state(state, self.env.now)

    def can_put(self):
        occupied = self._store_occupancy(self.input_buf)
        return occupied + len(self.input_buf.reservations_put) < 1

    def reserve_put(self):
        return self.input_buf.reserve_put()

    def put(self, event, item):
        result = self.input_buf.put(event, (item, 0))
        self._update_occupancy_stats()
        if hasattr(item, "record_path_entry"):
            item.record_path_entry(self.hierarchical_id, self.env)
        if self.telemetry is not None:
            self.telemetry.event(
                event_type="entry", entity_type="ConveyorBelt",
                entity_id=self.hierarchical_id, sim_time=self.env.now,
                attributes={"item_id": item.id},
            )
        return result

    def reserve_put_cancel(self, event):
        return self.input_buf.reserve_put_cancel(event)

    def can_get(self):
        return len(self.output_buf.ready_items) > len(self.output_buf.reservations_get)

    def get_copy(self):
        """Return the next output item without removing or reserving it."""
        if not self.can_get():
            raise RuntimeError("No unreserved conveyor output item is available")
        return self.output_buf.ready_items[len(self.output_buf.reservations_get)]

    def reserve_get(self):
        return self.output_buf.reserve_get()

    def get(self, event):
        item = self.output_buf.get(event)
        self._update_occupancy_stats()
        if self.telemetry is not None:
            self.telemetry.event(
                event_type="exit", entity_type="ConveyorBelt",
                entity_id=self.hierarchical_id, sim_time=self.env.now,
                attributes={"item_id": item.id},
            )
        return item

    def reserve_get_cancel(self, event):
        return self.output_buf.reserve_get_cancel(event)

    def occupancy(self):
        return (
            self._store_occupancy(self.input_buf)
            + sum(item is not None for item in self.stages)
            + self._store_occupancy(self.output_buf)
        )

    belt_occupancy = occupancy

    def is_full(self):
        return self.occupancy() >= self.capacity

    def is_stalled(self):
        return self._store_occupancy(self.output_buf) > 0

    def ready_items(self):
        return list(self.output_buf.ready_items)

    def items(self):
        ingress = [pair[0] for pair in self.input_buf.items] + list(self.input_buf.ready_items)
        transit = [item for item in self.stages if item is not None]
        egress = [pair[0] for pair in self.output_buf.items] + list(self.output_buf.ready_items)
        return ingress + transit + egress

    def empty(self):
        return self.occupancy() == 0

    is_empty = empty

    def show_occupancy(self):
        return "|" + "".join("*|" if item is not None else " |" for item in self.stages)

    def _take_ingress(self):
        """Remove and return the ingress item; called only by belt behaviour."""
        if not self.input_buf.ready_items:
            return None
        event = self.input_buf.reserve_get()
        yield event
        return self.input_buf.get(event)

    def _put_egress(self, item):
        """Place an item at egress; called only when egress is known empty."""
        event = self.output_buf.reserve_put()
        yield event
        self.output_buf.put(event, (item, 0))

    def behaviour(self):
        """Run synchronous slot movements for the lifetime of the simulation."""
        if self.start_time:
            yield self.env.timeout(self.start_time)

        while True:
            tick_delay = self.get_delay(self.delay_per_slot)
            if tick_delay <= 0:
                raise ValueError("delay per slot must be greater than zero")

            if self._store_occupancy(self.output_buf):
                self._set_state("STALLED_STATE")
                yield self.env.timeout(tick_delay)
                continue

            ingress_waiting = bool(self.input_buf.ready_items)
            if self.empty():
                self._set_state("EMPTY_STATE")
            else:
                self._set_state("MOVING_STATE")

            exiting = self.stages[-1]
            self.stages[1:] = self.stages[:-1]
            self.stages[0] = None
            if ingress_waiting:
                self.stages[0] = yield self.env.process(self._take_ingress())
            if exiting is not None:
                yield self.env.process(self._put_egress(exiting))
            self._update_occupancy_stats()
            yield self.env.timeout(tick_delay)


# Tolerate the common spelling used by the module name.
ConveryorBelt = ConveyorBelt
