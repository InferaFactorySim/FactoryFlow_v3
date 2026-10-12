import simpy
from collections import namedtuple

from factorysimpy.nodes.node import Node


# One outstanding service request. `done` is the event the requesting machine
# waits on; the operator succeeds it when the task is finished.
ServiceRequest = namedtuple(
    "ServiceRequest", "machine_id task_name delay done request_time"
)


class HumanOperator(Node):
    """
        HumanOperator represents a shared human resource that machines interrupt when
        they need a service task performed, such as refilling a consumable or replacing
        a component reel. It is not part of the item flow: no flow item passes through
        an operator, so it has no in_edges or out_edges. It is a resource that machines
        contend for.

        Parameters:
            capacity (int): Number of operators in the pool. Only 1 is implemented;
                the parameter exists so that a pool can be added later without an API
                change.

        Behavior:
            The operator stays in `IDLE_STATE` until a machine requests a task. Requests
            are served one at a time in FIFO order. While a task is running the operator
            is in `BUSY_STATE`; a request arriving during a task does not abandon it --
            the remaining time is carried forward and the task is resumed, matching
            preempt-and-resume behaviour. When a task completes the requesting machine's
            `done` event is triggered and the machine resumes.

            A task must be assigned with `assign_task` before a machine may request it,
            which is what fixes its duration: the machine decides *when* service is
            needed, the operator decides *how long* it takes.

        Raises:
            ValueError: If `capacity` is not 1.
            KeyError: If a machine requests a task that was never assigned to it.

        Output performance metrics:
        The key performance metrics of the HumanOperator are captured in the `stats`
        attribute (dict) during a simulation run.

            last_state_change_time     : Time when the state was last changed.
            total_time_spent_in_states : Dictionary with total time spent in each state.
            num_tasks_completed        : Total number of service tasks completed.
            total_busy_time            : Total time spent performing tasks.
            tasks_completed_per_type   : Completed task count, keyed by task name.
            total_wait_time            : Total time requests spent queued before start.
            max_queue_length           : Longest the pending queue ever became.
    """

    def __init__(self, env, id, capacity=1, telemetry=None):
        super().__init__(env, id)

        if capacity != 1:
            raise ValueError(
                "HumanOperator supports capacity=1 only; a multi-operator pool is "
                "not implemented yet."
            )
        self.capacity = capacity
        self.telemetry = telemetry

        # (machine_id, task_name) -> delay
        self.assigned_tasks = {}
        # FIFO queue of ServiceRequest waiting to be served
        self.pending_tasks = []

        self.state = "IDLE_STATE"
        self.stats = {
            "last_state_change_time": None,
            "total_time_spent_in_states": {"IDLE_STATE": 0.0, "BUSY_STATE": 0.0},
            "num_tasks_completed": 0,
            "total_busy_time": 0.0,
            "tasks_completed_per_type": {},
            "total_wait_time": 0.0,
            "max_queue_length": 0,
        }

        self.behave = self.env.process(self.behaviour())

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    def assign_task(self, machine, task_name, delay):
        """Assign a task to this operator, fixing how long that task takes.

        Args:
            machine (Node or str): The machine the task is performed on, or its id.
            task_name (str): Name of the task, e.g. "reel_replacement".
            delay (int or float): Time the operator takes to perform it.
        """
        if delay is None or delay < 0:
            raise ValueError(f"{self.id} - task delay must be non-negative")
        machine_id = machine if isinstance(machine, str) else machine.id
        self.assigned_tasks[(machine_id, task_name)] = delay

    # ------------------------------------------------------------------
    # Request protocol (the only place the interrupt mechanism lives)
    # ------------------------------------------------------------------
    def request_task(self, machine, task_name):
        """Request a task. Returns an event that is triggered when it is done.

        Called from a machine's worker process. The request is queued first and the
        operator's process is then interrupted purely to wake it, so nothing depends
        on the interrupt payload.
        """
        machine_id = machine if isinstance(machine, str) else machine.id
        key = (machine_id, task_name)
        if key not in self.assigned_tasks:
            raise KeyError(
                f"{self.id} - task '{task_name}' is not assigned for machine "
                f"'{machine_id}'. Call assign_task first."
            )

        done = self.env.event()
        request = ServiceRequest(
            machine_id=machine_id,
            task_name=task_name,
            delay=self.assigned_tasks[key],
            done=done,
            request_time=self.env.now,
        )
        self.pending_tasks.append(request)
        self.stats["max_queue_length"] = max(
            self.stats["max_queue_length"], len(self.pending_tasks)
        )

        # Wake the operator. Guarded: a dead process, or the operator itself, cannot
        # be interrupted.
        if self.behave.is_alive and self.env.active_process is not self.behave:
            self.behave.interrupt(f"{machine_id}:{task_name}")

        if self.telemetry is not None:
            self.telemetry.event(
                event_type="service_requested",
                entity_type="HumanOperator",
                entity_id=self.hierarchical_id,
                sim_time=self.env.now,
                attributes={"machine_id": machine_id, "task_name": task_name},
            )
        return done

    # ------------------------------------------------------------------
    # Behaviour
    # ------------------------------------------------------------------
    def behaviour(self):
        while True:
            try:
                if not self.pending_tasks:
                    self.update_state("IDLE_STATE", self.env.now)
                    # Sleep until interrupted by a request. An event that is never
                    # triggered is the idiomatic way to block indefinitely.
                    yield self.env.event()
                    continue

                request = self.pending_tasks.pop(0)
                self.stats["total_wait_time"] += self.env.now - request.request_time
                self.update_state("BUSY_STATE", self.env.now)

                # Perform the task. A request arriving mid-task interrupts this
                # process, so the remaining time is carried forward and the task
                # resumed rather than restarted or abandoned.
                remaining = request.delay
                while remaining > 0:
                    task_start = self.env.now
                    try:
                        yield self.env.timeout(remaining)
                        remaining = 0
                    except simpy.Interrupt:
                        remaining -= self.env.now - task_start

                self.stats["total_busy_time"] += request.delay
                self.stats["num_tasks_completed"] += 1
                self.stats["tasks_completed_per_type"][request.task_name] = (
                    self.stats["tasks_completed_per_type"].get(request.task_name, 0) + 1
                )
                if self.telemetry is not None:
                    self.telemetry.event(
                        event_type="service_completed",
                        entity_type="HumanOperator",
                        entity_id=self.hierarchical_id,
                        sim_time=self.env.now,
                        attributes={
                            "machine_id": request.machine_id,
                            "task_name": request.task_name,
                        },
                    )
                request.done.succeed()

            except simpy.Interrupt:
                # A request arrived while idle, or between tasks. It is already
                # queued, so just continue.
                continue

    def update_final_state_time(self, simulation_end_time):
        """Flush the time spent in the state held when the run ended."""
        if self.state is not None and self.stats["last_state_change_time"] is not None:
            duration = simulation_end_time - self.stats["last_state_change_time"]
            self.stats["total_time_spent_in_states"][self.state] = (
                self.stats["total_time_spent_in_states"].get(self.state, 0.0) + duration
            )
            self.stats["last_state_change_time"] = simulation_end_time

    def get_utilization(self, simulation_end_time=None):
        """Return the fraction of time spent in each state, as percentages."""
        end = self.env.now if simulation_end_time is None else simulation_end_time
        times = dict(self.stats["total_time_spent_in_states"])
        if self.stats["last_state_change_time"] is not None:
            times[self.state] = times.get(self.state, 0.0) + (
                end - self.stats["last_state_change_time"]
            )
        total = sum(times.values())
        if total <= 0:
            return {state: 0.0 for state in times}
        return {state: value / total * 100.0 for state, value in times.items()}
