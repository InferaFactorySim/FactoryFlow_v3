from factorysimpy.nodes.machine import Machine


class PowerCycledMachine(Machine):
    """
        PowerCycledMachine is a Machine that switches itself off when it has no work and
        must warm up again before it can process the next item. It models a thermal
        process unit such as a reflow oven, curing tunnel, autoclave or furnace, where
        holding temperature while idle is the dominant energy cost and the saving from
        switching off is paid for by a warm-up delay.

        Parameters:
            setup_time (int, float, Generator or Callable): Time to warm up from off.
                A callable may read `time_since_power_off` on this machine, so a
                temperature-dependent warm-up can be expressed without subclassing.
            power_down_when_idle (bool): If True, the machine switches off once it has
                no item in progress and no item waiting on any in edge.
            idle_poll (int or float): How often the idle check runs. Must be > 0.
            start_powered (bool): If True (default) the machine is on once
                `node_setup_time` has elapsed, as an ordinary Machine is. If False it
                starts off and warms up when its first item arrives.
            All other parameters are inherited from Machine unchanged.

        Behavior:
            Before processing an item, a worker that finds the machine off waits for a
            warm-up of `setup_time`. Several workers may be active when
            `work_capacity > 1`; only one warm-up runs and the others wait for the same
            one. Powering down is driven by a periodic idle check rather than by a
            worker, so it happens whether the machine went idle through starvation or
            through finishing its last item.

            While warming up, the worker holds its item and its thread, so the machine
            stalls upstream exactly as it does while processing.

        Note on state accounting:
            Off and warm-up time are recorded as `OFF_STATE` and `WARMUP_STATE` in
            `stats["total_time_spent_in_states"]`, alongside the inherited states. The
            base class's own state machine is left untouched, so its
            `ATLEAST_ONE_PROCESSING_STATE` still counts warm-up time as processing; a
            caller that needs processing time alone subtracts `WARMUP_STATE`.

        Output performance metrics:
        In addition to the metrics inherited from Machine, `stats` carries:

            num_power_cycles : Number of completed warm-ups.
            num_power_downs  : Number of times the machine switched off.
            total_time_spent_in_states["OFF_STATE"]    : Total time switched off.
            total_time_spent_in_states["WARMUP_STATE"] : Total time warming up.
    """

    def __init__(self, env, id, setup_time=0, power_down_when_idle=True,
                 idle_poll=1, start_powered=True, **kwargs):
        super().__init__(env, id, **kwargs)

        if not (
            callable(setup_time)
            or hasattr(setup_time, "__next__")
            or isinstance(setup_time, (int, float))
        ):
            raise ValueError(
                f"{id} - setup_time must be an int, float, generator or callable"
            )
        if not isinstance(idle_poll, (int, float)) or idle_poll <= 0:
            raise ValueError(f"{id} - idle_poll must be a positive number")

        # A warm-up is only ever served when the machine is off: either because it
        # started off, or because it switched off while idle. Specifying one that can
        # never happen silently produces a machine that never warms up, so reject the
        # combination rather than ignore it.
        warmup_specified = (
            callable(setup_time)
            or hasattr(setup_time, "__next__")
            or (isinstance(setup_time, (int, float)) and setup_time > 0)
        )
        if warmup_specified and start_powered and not power_down_when_idle:
            raise ValueError(
                f"{id} - setup_time is set but can never apply: the machine starts "
                f"powered and never switches off. Use start_powered=False for a "
                f"machine that must warm up before its first item, or "
                f"power_down_when_idle=True for one that switches off when idle."
            )

        self.setup_time = setup_time
        self.power_down_when_idle = power_down_when_idle
        self.idle_poll = idle_poll

        self._powered = start_powered
        self._power_off_time = None if start_powered else 0.0
        self._warmup_in_progress = None

        self.stats["num_power_cycles"] = 0
        self.stats["num_power_downs"] = 0
        self.stats["total_time_spent_in_states"]["OFF_STATE"] = 0.0
        self.stats["total_time_spent_in_states"]["WARMUP_STATE"] = 0.0

        if self.power_down_when_idle:
            self.idle_monitor = self.env.process(self._idle_monitor())

    # ------------------------------------------------------------------
    @property
    def powered(self):
        """True while the machine is on or warming up, False while switched off."""
        return self._powered

    @property
    def time_since_power_off(self):
        """How long the machine has been off, or 0 while it is on."""
        if self._powered or self._power_off_time is None:
            return 0.0
        return self.env.now - self._power_off_time

    def _items_waiting(self):
        return any(edge.can_get() for edge in (self.in_edges or []))

    def _power_off(self):
        self._powered = False
        self._power_off_time = self.env.now
        self.stats["num_power_downs"] += 1
        print(f"T={self.env.now:.2f}: {self.id} powered down")

    def _idle_monitor(self):
        """Switch the machine off once it is idle with nothing waiting."""
        while True:
            yield self.env.timeout(self.idle_poll)
            if (
                self._powered
                and self._warmup_in_progress is None
                and self.state_rep == (0, 0)
                and not self._items_waiting()
            ):
                self._power_off()

    def _ensure_powered(self):
        """Warm up if switched off. Concurrent workers share a single warm-up."""
        if self._powered:
            return

        if self._warmup_in_progress is not None:
            yield self._warmup_in_progress
            return

        warmup_done = self.env.event()
        self._warmup_in_progress = warmup_done

        if self._power_off_time is not None:
            self.stats["total_time_spent_in_states"]["OFF_STATE"] += (
                self.env.now - self._power_off_time
            )

        delay = self.get_delay(self.setup_time)
        print(f"T={self.env.now:.2f}: {self.id} warming up for {delay}")
        yield self.env.timeout(delay)

        self.stats["total_time_spent_in_states"]["WARMUP_STATE"] += delay
        self.stats["num_power_cycles"] += 1
        self._powered = True
        self._power_off_time = None
        self._warmup_in_progress = None
        warmup_done.succeed()
        print(f"T={self.env.now:.2f}: {self.id} up to temperature")

    # ------------------------------------------------------------------
    def _before_pull(self):
        """Finish warming up before anything is taken in.

        A heated chamber does not accept items while it is still coming up to
        temperature, so the warm-up is served here, before the machine reserves an
        item, rather than inside `worker`. Items therefore wait upstream during a
        warm-up, which makes
        `PowerCycledMachine(setup_time=t, start_powered=False)` behave as
        `Machine(node_setup_time=t)` does for a unit that warms up once.
        """
        yield from self._ensure_powered()

    def worker(self, item, processing_delay, req_token):
        """Run the inherited processing behaviour.

        The warm-up has already been served by `_before_pull`; this remains as a
        guard for the case where the machine was switched off after an item had
        already been taken in.
        """
        yield from self._ensure_powered()
        yield from super().worker(item, processing_delay, req_token)

    def update_final_state_time(self, simulation_end_time):
        """Close off the open off-period, then flush the inherited state times."""
        if not self._powered and self._power_off_time is not None:
            self.stats["total_time_spent_in_states"]["OFF_STATE"] += (
                simulation_end_time - self._power_off_time
            )
            self._power_off_time = simulation_end_time
        return super().update_final_state_time(simulation_end_time)
