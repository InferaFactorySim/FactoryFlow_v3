class BaseFlowItem:
    """A class representing an item ."""
    def __init__(self, id):
        self.id = id
        self.timestamp_creation = None
        self.timestamp_destruction = None
        self.timestamp_node_entry = None
        self.timestamp_node_exit = None
        self.current_node_id = None
        self.source_id = None      # Track the source node
        self.payload = None
        self.destructed_in_node = None  # Node where item was destructed
        self.stats = {}  # Dictionary to store time spent at each node
        # Legacy last-entry lookup retained for existing consumers. Repeated
        # visits overwrite the value here, so completed-path reconstruction
        # must use path_events instead.
        self.path_history = {}
        self.path_events = []
        self._path_sequence = 0

    def set_creation(self, source_id, env):
        """Set creation time and source node ID."""
        self.timestamp_creation = env.now
        self.source_id = source_id
    
    def set_destruction(self, node_id,  env):
        """set the destruction time and node of the item."""
        self.timestamp_destruction = env.now
        self.destructed_in_node = node_id

    def record_path_entry(self, hierarchical_id, env):
        """Record one ordered component visit, including repeated visits."""
        self._path_sequence += 1
        event = {
            "component_id": hierarchical_id,
            "sim_time": env.now,
            "sequence": self._path_sequence,
        }
        self.path_events.append(event)
        self.path_history[hierarchical_id] = env.now
        return event

    def get_item_path(self):
        """Return the ordered component path, preserving loops and revisits."""
        if self.path_events:
            return [event["component_id"] for event in self.path_events]
        return sorted(
            self.path_history,
            key=lambda node: self.path_history[node],
        )

    def get_path_events(self):
        """Return JSON-serializable copies of the ordered visit records."""
        return [dict(event) for event in self.path_events]

    def update_node_event(self, hierarchical_id, env, event_type="entry"):
        """
        Update item details and stats when entering or exiting a node.

        Args:
            hierarchical_id (str): The fully-qualified hierarchical ID of the node.
            env (simpy.Environment): The simulation environment (for current time).
            event_type (str): "entry" or "exit" to specify the event.
        """
        if event_type == "entry":
            self.timestamp_node_entry = env.now
            #print(f"T={self.timestamp_node_entry:.2f}: {self.id} entered node {hierarchical_id}")
            self.current_node_id = hierarchical_id
            self.record_path_entry(hierarchical_id, env)
        elif event_type == "exit":
            self.timestamp_node_exit = env.now
            # Calculate time spent at the node and update stats
            if self.current_node_id is not None and self.timestamp_node_entry is not None:
                time_spent = self.timestamp_node_exit - self.timestamp_node_entry
                if self.current_node_id in self.stats:
                    self.stats[self.current_node_id] += time_spent
                else:
                    self.stats[self.current_node_id] = time_spent
            #self.current_node_id = None
            #self.timestamp_node_entry = None
            

    def __repr__(self):
        return f"Item({self.id})"
