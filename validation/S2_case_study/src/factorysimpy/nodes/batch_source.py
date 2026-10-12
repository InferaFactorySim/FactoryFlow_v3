import simpy

from factorysimpy.nodes.source import Source
from factorysimpy.helper.item import Item
from factorysimpy.helper.pallet import Pallet


class BatchSource(Source):
    """
        BatchSource creates a batch of flow items together at a single time instant and
        then releases them into its out edge one at a time as space becomes available.

        This models a stack, magazine or tray of parts that is loaded at the head of a
        line in one go: the parts exist from the moment the stack is loaded, but enter
        the line one by one. Because every item of a batch is stamped with the same
        creation timestamp, the time an item spends waiting its turn for release counts
        towards its time in the system, exactly as it does for a part sitting in a stack.

        `Source` creates one item per arrival and stamps it at the moment it is released,
        so an item never accumulates waiting time before entering the line. BatchSource
        is a separate class rather than a mode on `Source`, leaving `Source` untouched.

        Parameters:
            batch_size (int): Number of items created together per batch. Must be >= 1.
                A value of 1 still differs from `Source`, in that the stamp is applied at
                creation rather than at release.
            batches_ahead (int): How many further batches may be created while the
                current batch is still being released. Must be >= 0.

                - 0 (default): the next batch is created only once the current batch has
                  been fully released, so exactly one batch exists at a time.
                - 1 or more: that many batches are prepared ahead and wait, fully
                  stamped, for their turn. This models a line where a fresh stack is
                  loaded at the head as soon as the previous one has been taken, so a
                  waiting stack accumulates time before any of its parts enter.
            inter_arrival_time: Delay before each *batch*, not before each item. Accepts
                the same int, float, generator or callable forms as `Source`.
            blocking (bool): Must be True. A batch is held until it has been released, so
                discarding on a full out edge is not meaningful here.
            All other parameters are inherited from `Source` unchanged.

        Behavior:
            Two processes run concurrently. A creator waits `inter_arrival_time`, creates
            `batch_size` items at that instant and stamps them all, provided fewer than
            `batches_ahead + 1` batches are outstanding. A releaser takes items from the
            oldest outstanding batch in creation order and pushes them into the out edge,
            waiting in `BLOCKED_STATE` whenever the edge cannot yet accept one and
            returning to `GENERATING_STATE` after each release. Separating the two is
            what lets a batch be prepared while the previous one is still draining.

        Raises:
            ValueError: If `batch_size` is not a positive integer, `batches_ahead` is not
                a non-negative integer, or `blocking` is False.
            AssertionError: If the source has an in edge, or does not have exactly one
                out edge.

        Output performance metrics:
        In addition to the metrics inherited from `Source`, `stats` carries:

            num_batches_generated : Total number of batches created.
            num_items_pending     : Items created but not yet released, sampled at the
                                    end of the run.
    """

    def __init__(self, env, id, batch_size=1, batches_ahead=0, **kwargs):
        super().__init__(env, id, **kwargs)

        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError(
                f"BatchSource '{id}' - batch_size must be a positive integer, "
                f"got {batch_size!r}"
            )
        if (
            not isinstance(batches_ahead, int)
            or isinstance(batches_ahead, bool)
            or batches_ahead < 0
        ):
            raise ValueError(
                f"BatchSource '{id}' - batches_ahead must be a non-negative integer, "
                f"got {batches_ahead!r}"
            )
        if not self.blocking:
            raise ValueError(
                f"BatchSource '{id}' - blocking must be True: a batch is held until it "
                f"has been released."
            )

        self.batch_size = batch_size
        self.batches_ahead = batches_ahead
        self.stats["num_batches_generated"] = 0
        self.stats["num_items_pending"] = 0

        # Outstanding batches, oldest first. Each is a list of items awaiting release.
        self._batches = []
        # Signals between the creator and the releaser.
        self._slot_free = self.env.event()
        self._items_available = self.env.event()

        self.creator = self.env.process(self._creator())

    # ------------------------------------------------------------------
    @staticmethod
    def _signal(event):
        """Trigger an event unless it has already fired."""
        if not event.triggered:
            event.succeed()

    def _create_batch(self, first_serial):
        """Create `batch_size` items, all stamped with the current time instant."""
        batch = []
        for offset in range(self.batch_size):
            serial = first_serial + offset
            if self.flow_item_type == "item":
                item = Item(f'item_{self.id + "_" + str(serial)}')
            else:
                item = Pallet(f'pallet_{self.id + "_" + str(serial)}')
            item.length = self.item_length
            # The whole batch shares this instant as its creation time.
            item.set_creation(self.id, self.env)
            item.update_node_event(self.hierarchical_id, self.env, "entry")
            self.stats["num_item_generated"] += 1
            if self.telemetry is not None:
                self.telemetry.event(
                    event_type="entry",
                    entity_type="Source",
                    entity_id=self.hierarchical_id,
                    sim_time=self.env.now,
                    attributes={"item_id": item.id, "batch_size": self.batch_size},
                )
            batch.append(item)
        self.stats["num_batches_generated"] += 1
        return batch

    def _release(self, item, out_edge):
        """Release one item into the out edge, waiting for space if necessary.

        Written out rather than delegating to `Source._push_item`, because that helper
        stamps the creation time at release, which would overwrite the batch stamp.
        """
        put_token = out_edge.reserve_put()
        yield put_token

        item.timestamp_node_exit = self.env.now
        result = out_edge.put(put_token, item)
        if isinstance(result, simpy.events.Process):
            yield result

        if self.telemetry is not None:
            self.telemetry.event(
                event_type="exit",
                entity_type="Source",
                entity_id=self.hierarchical_id,
                sim_time=self.env.now,
                attributes={"item_id": item.id, "selected_out_edge": 0},
            )
        print(f"T={self.env.now:.2f}: {self.id} released {item.id} into {out_edge.id}")

    # ------------------------------------------------------------------
    def _creator(self):
        """Create batches, keeping at most `batches_ahead + 1` outstanding."""
        yield self.env.timeout(self.node_setup_time)
        serial = 0

        while True:
            while len(self._batches) > self.batches_ahead:
                self._slot_free = self.env.event()
                yield self._slot_free

            next_arrival_time = self.get_delay(self.inter_arrival_time)
            if not isinstance(next_arrival_time, (int, float)):
                raise AssertionError(
                    "inter_arrival_time returns an invalid value. It should be int or float"
                )
            yield self.env.timeout(next_arrival_time)

            self._batches.append(self._create_batch(serial + 1))
            serial += self.batch_size
            print(
                f"T={self.env.now:.2f}: {self.id} created a batch of {self.batch_size} "
                f"items ({len(self._batches)} outstanding)"
            )
            self._signal(self._items_available)

    def behaviour(self):
        """Release items of the oldest outstanding batch, in creation order."""
        assert self.in_edges is None, f"BatchSource '{self.id}' must not have an in_edge."
        assert (
            self.out_edges is not None and len(self.out_edges) == 1
        ), f"BatchSource '{self.id}' must have exactly 1 out_edge."

        self.reset()
        out_edge = self.out_edges[0]

        self.update_state("SETUP_STATE", self.env.now)
        print(
            f"T={self.env.now:.2f}: {self.id} is in SETUP_STATE. Waiting for setup time "
            f"{self.node_setup_time} seconds"
        )
        yield self.env.timeout(self.node_setup_time)
        self.update_state("GENERATING_STATE", self.env.now)
        print(f"T={self.env.now:.2f}: {self.id} is now {self.state}")

        while True:
            while not self._batches:
                self._items_available = self.env.event()
                yield self._items_available

            batch = self._batches[0]
            self.update_state("BLOCKED_STATE", self.env.now)
            yield self.env.process(self._release(batch[0], out_edge))
            batch.pop(0)

            if not batch:
                self._batches.pop(0)
                self._signal(self._slot_free)

            self.stats["num_items_pending"] = sum(len(b) for b in self._batches)
            self.update_state("GENERATING_STATE", self.env.now)
