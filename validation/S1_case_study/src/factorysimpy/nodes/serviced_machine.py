from factorysimpy.nodes.machine import Machine


class ServicedMachine(Machine):
    """
        ServicedMachine is a Machine that periodically needs a service task performed
        by a shared HumanOperator, such as refilling a consumable or replacing a
        component reel. While it waits for the operator the machine is stalled, so the
        contention between machines sharing one operator is modelled rather than
        approximated by adding the task time to the processing delay.

        Every parameter is declarative, so a generated model can configure this
        component the same way it configures any other.

        Parameters:
            operator (HumanOperator): The operator that performs the tasks. Required
                when `services` is non-empty.
            services (list of dict): Service rules, each
                `{"task": <task_name>, "interval": <items between services>}`.
                The interval counts items processed by this machine since its last
                service of that task. The task's *duration* is not given here: it comes
                from `operator.assign_task`, because the operator performs the work.
                A rule may also carry `"first_after"` to make the first service fall
                due after a different number of items than the repeating interval.
            All other parameters are inherited from Machine unchanged.

        Behavior:
            Before processing an item, the machine checks whether any service rule is
            due. If one is, it requests that task from the operator and waits for the
            operator's `done` event before processing continues. Requests are queued by
            the operator in FIFO order, so a machine may wait longer than the task's own
            duration when the operator is already busy. Several rules may fall due at
            the same time; they are then requested in the order they are declared.

            Item processing, edge selection and blocking behaviour are inherited from
            Machine and are not re-implemented here.

        Note on state accounting:
            While waiting for the operator, the worker thread keeps the base class's
            `PROCESSING_STATE`, so `ATLEAST_ONE_PROCESSING_STATE` in the inherited stats
            includes the waiting time. The waiting time is also recorded separately as
            `WAITING_FOR_OPERATOR_STATE`, so a caller that needs processing time alone
            subtracts it. This keeps the base class's state machine untouched.

        Output performance metrics:
        In addition to the metrics inherited from Machine, `stats` carries:

            total_time_waiting_for_operator : Total time stalled waiting for service.
            num_service_requests            : Number of service tasks requested.
            service_requests_per_type       : Requested task count, keyed by task name.
            total_time_spent_in_states["WAITING_FOR_OPERATOR_STATE"]
                                            : Same total, alongside the inherited states.
    """

    def __init__(self, env, id, operator=None, services=None, **kwargs):
        super().__init__(env, id, **kwargs)

        self.operator = operator
        self.services = list(services) if services else []

        for rule in self.services:
            if "task" not in rule or "interval" not in rule:
                raise ValueError(
                    f"{self.id} - each service rule needs 'task' and 'interval', "
                    f"got {rule}"
                )
            if rule["interval"] is None or rule["interval"] <= 0:
                raise ValueError(
                    f"{self.id} - service interval must be a positive number of items"
                )
        if self.services and self.operator is None:
            raise ValueError(
                f"{self.id} - an operator is required when service rules are given"
            )

        # Items processed since the last service, per task.
        self._items_since_service = {rule["task"]: 0 for rule in self.services}
        # Number of items after which the next service of each task falls due.
        self._next_due = {
            rule["task"]: rule.get("first_after", rule["interval"])
            for rule in self.services
        }

        self.stats["total_time_waiting_for_operator"] = 0.0
        self.stats["num_service_requests"] = 0
        self.stats["service_requests_per_type"] = {}
        self.stats["total_time_spent_in_states"]["WAITING_FOR_OPERATOR_STATE"] = 0.0

    # ------------------------------------------------------------------
    def _due_services(self):
        """Return the task names whose interval has elapsed, in declaration order."""
        return [
            rule["task"]
            for rule in self.services
            if self._items_since_service[rule["task"]] >= self._next_due[rule["task"]]
        ]

    def _serve(self, task_name):
        """Request `task_name` and stall until the operator has finished it."""
        wait_start = self.env.now
        self.stats["num_service_requests"] += 1
        self.stats["service_requests_per_type"][task_name] = (
            self.stats["service_requests_per_type"].get(task_name, 0) + 1
        )
        print(
            f"T={self.env.now:.2f}: {self.id} requests '{task_name}' from "
            f"{self.operator.id}"
        )

        done = self.operator.request_task(self, task_name)
        yield done

        waited = self.env.now - wait_start
        self.stats["total_time_waiting_for_operator"] += waited
        self.stats["total_time_spent_in_states"]["WAITING_FOR_OPERATOR_STATE"] += waited
        self._items_since_service[task_name] = 0
        interval = next(
            rule["interval"] for rule in self.services if rule["task"] == task_name
        )
        self._next_due[task_name] = interval
        print(
            f"T={self.env.now:.2f}: {self.id} resumed after '{task_name}' "
            f"(waited {waited:.2f})"
        )

    # ------------------------------------------------------------------
    def worker(self, item, processing_delay, req_token):
        """Insert any due service before the inherited processing behaviour.

        Machine.worker is a generator, so it is delegated to with `yield from`: none
        of its processing, edge-selection or blocking logic is duplicated here.
        """
        for task_name in self._due_services():
            yield from self._serve(task_name)

        yield from super().worker(item, processing_delay, req_token)

        for task_name in self._items_since_service:
            self._items_since_service[task_name] += 1
