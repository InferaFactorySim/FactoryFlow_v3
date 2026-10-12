from factorysimpy.edges.edge import Edge
from factorysimpy.base.buffer_store import BufferStore 
from factorysimpy.utils.telemetry import Telemetry

class Buffer(Edge):
    """
    Buffer class representing a FIFO queue.
    Inherits from the Edge class. This buffer can have a single input edge and a single output edge.

    Attributes:
        state (str): The current state of the buffer.
        capacity (int): The capacity of the buffer's internal storage.
        mode (str): Mode of operation for the buffer, It can be
            - "FIFO" (First In First Out) 
            - "LIFO" (Last In First Out).
        delay (int, float): Delay after which the item becomes available. It Can be
        
            - int or float: Used as a constant delay.
           

     Behavior:
            The Buffer is a type of edge represents components that holds the items that are waiting to be accepted by the destination node. Items that are added in buffer becomes available
            for use after `delay` amount of time. It operates in two modes- 
            1. `FIFO`: It prioritizes items in the order they were added, with the oldest items being available for the destination node first.
            2. `LIFO`: It prioritizes items in the reverse order of their arrival, items that newly added are available to use by the destination node first
            Incoming edges can use reserve_get and reserve_put calls on the store in the buffer to reserve an item or space and after yielding 
            the requests, an item can be put and obtained by using put and get methods.

    

    Raises:
        AssertionError: If the buffer does not have at least one source node or one destination node.

    Output performance metrics:
        The key performance metrics of the buffer edge are captured in the `stats` attribute (dict) during a simulation run. 
            
            last_state_change_time                      : Time when the state was last changed.
            time_averaged_num_of_items_in_buffer        : Time-averaged number of items available in the buffer.
            total_time_spent_in_states                  : Dictionary with total time spent in each state.
    """

    def __init__(self, env, id,  capacity=1, delay=0,  mode="FIFO", admission="CONTINUOUS", release_threshold=None, telemetry=None):
          super().__init__( env, id, capacity)
          self.state = "IDLE_STATE"
          self.mode=mode
          self.delay = delay
          self.capacity =  capacity
          self.telemetry =telemetry
          self.stats = {
            "last_state_change_time": None,
            "time_averaged_num_of_items_in_buffer": 0,
            "total_time_spent_in_states":{"IDLE_STATE": 0.0, "RELEASING_STATE": 0.0, "BLOCKED_STATE": 0.0}
        }
          
         
          
          if self.mode not in ["FIFO", "LIFO"]:
            raise ValueError("Invalid mode. Choose either 'FIFO' or 'LIFO'.")

          # `mode` decides the order items leave in; `admission` decides when new
          # items may enter.
          #   "CONTINUOUS"  : admit whenever a slot is free (default, unchanged).
          #   "BATCH_DRAIN" : admit until full, then refuse every item until the
          #                   buffer is completely empty. This is how an accumulating
          #                   stack feeding a batch process behaves - nothing may be
          #                   loaded while the stack is being unloaded - and it is what
          #                   makes the throughput cost of such buffering visible.
          #   "BATCH_RELEASE": withhold items until `release_threshold` have
          #                    accumulated, then release until empty while still
          #                    accepting new ones - two stacks in ping-pong, which is
          #                    how double buffering keeps throughput up.
          if admission not in ["CONTINUOUS", "BATCH_DRAIN", "BATCH_RELEASE"]:
            raise ValueError(
              "Invalid admission. Choose 'CONTINUOUS', 'BATCH_DRAIN' or 'BATCH_RELEASE'.")
          self.admission = admission
          self.release_threshold = capacity if release_threshold is None else release_threshold
          if self.release_threshold < 1 or self.release_threshold > capacity:
            raise ValueError("release_threshold must be between 1 and capacity.")

          # Initialize the buffer store

          self.inbuiltstore = BufferStore(env, capacity=self.capacity,  mode=self.mode, admission=self.admission, release_threshold=self.release_threshold)
          
    
          
         
          

          if callable(delay) or hasattr(delay, '__next__') or isinstance(delay, (int, float)) or delay is None:
            self.delay = delay
    
          else:
            raise ValueError("delay must be None, int, float, generator, or callable.")
            
          #self.behavior =  self.env.process(self.behaviour())
          #self.stats_collector = self.env.process(self._stats_collector(sample_interval=0.4))
    @property
    def drain_state(self):
        """"FILLING" while entry is open, "EMPTYING" while it is closed.

        Always "FILLING" under the default "CONTINUOUS" admission.
        """
        return self.inbuiltstore.drain_state

    @property
    def num_drain_cycles(self):
        """Number of times the buffer filled and began draining."""
        return self.inbuiltstore.num_drain_cycles

    def initial_test(self):
        assert self.src_node is not None , f"Buffer '{self.id}' must have atleast 1 src_node."
        assert self.dest_node is not None , f"Buffer '{self.id}' must have atleast 1 dest_node."

    def update(self, params):
       for param in params:
          if hasattr(self, param):
             setattr(self, param, params[param])    
    
    def _stats_collector1(self, sample_interval=1):
        """
        Periodically sample the number of items in the buffer and compute the time-averaged value.
        """
        self.initial_test()
    
        while True:
            yield self.env.timeout(sample_interval)
            #print(self.stats["time_averaged_num_of_items_in_buffer"])
            self.stats["time_averaged_num_of_items_in_buffer"] = self.inbuiltstore.time_averaged_num_of_items_in_store
    
    def _buffer_stats_collector(self):
        """
        Periodically sample the number of items in the buffer and compute the time-averaged value.
        """
        self.initial_test()
        
        self.inbuiltstore._update_time_averaged_level()
        
        self.stats["time_averaged_num_of_items_in_buffer"] = self.inbuiltstore.time_averaged_num_of_items_in_store
    
    def update_final_buffer_avg_content(self, simulation_end_time):
        now = simulation_end_time
        interval = now - self.inbuiltstore._last_level_change_time
        self.inbuiltstore._weighted_sum += self.inbuiltstore._last_num_items * interval
        self.inbuiltstore._last_level_change_time = now
        self.inbuiltstore._last_num_items = len(self.inbuiltstore.items)+len(self.inbuiltstore.ready_items)
        
        total_time = now
        self.inbuiltstore.time_averaged_num_of_items_in_store = (
            self.inbuiltstore._weighted_sum / total_time if total_time > 0 else 0.0
        )
        self._buffer_stats_collector()

    def can_put(self):
        """
        Check if the buffer can accept an item.
        
        Returns
        -------
        bool
            True if the buffer can accept an item, False otherwise.
        """
        # Check if the buffer has space for new items
        if  len(self.inbuiltstore.items)+len(self.inbuiltstore.ready_items)== self.capacity:
            return False
        # return True if the number of items in the buffer is less than the store capacity minus the number of reservations
        # reservations_put is the number of items that are already reserved to be put in the buffer
        return (self.capacity-len(self.inbuiltstore.items)-len(self.inbuiltstore.ready_items)) >len(self.inbuiltstore.reservations_put)
    
    def can_get(self):
        """
        Check if the buffer can accept an item.
        
        Returns
        -------
        bool
            True if the buffer can give an item, False otherwise.
        """
        if not self.inbuiltstore.ready_items:
            return False
        # Under "BATCH_DRAIN" the buffer withholds items until the batch is complete,
        # so nothing can be taken while it is still filling.
        if (self.admission in ("BATCH_DRAIN", "BATCH_RELEASE")
                and self.inbuiltstore.drain_state == "FILLING"):
            return False
        #only return items that are older than the delay. Count such items
        #count = sum(1 for item in self.inbuiltstore.items if item.time_stamp_creation + self.delay <= self.env.now)
        # count should be greater than the number of reservations that are already there
        return len(self.inbuiltstore.ready_items) > len(self.inbuiltstore.reservations_get)
    


    def reserve_put(self):
       return self.inbuiltstore.reserve_put()
    
    def reserve_get(self):
        return self.inbuiltstore.reserve_get()
    
    def put(self, event, item):
       delay=self.get_delay(self.delay)
       print(f"T={self.env.now:.2f}: {self.id} is putting item {item.id} with delay {delay} at time {self.env.now}, total item in buffer is {len(self.inbuiltstore.items)+len(self.inbuiltstore.ready_items)}")
       
       proceed=self.inbuiltstore.put(event, (item,delay))
       item.record_path_entry(self.hierarchical_id, self.env)
       if self.telemetry is not None:
            self.telemetry.event(
                        event_type="entry",
                        entity_type="Buffer",
                        entity_id= self.hierarchical_id,
                        sim_time=self.env.now,
                        attributes={"item_id": item.id})
       self._buffer_stats_collector()
       return proceed
    
    def get(self, event):
        """
        Get an item from the buffer.
        
        Parameters
        ----------
        event : simpy.Event
            The event that was reserved for getting an item.
        
        Returns
        -------
        item : object
            The item retrieved from the buffer.
        """
        item = self.inbuiltstore.get(event)
        self._buffer_stats_collector()
        #item.path_history[self.hierarchical_id] =  self.env.now
        if self.telemetry is not None:
            self.telemetry.event(
                        event_type="exit",
                        entity_type="Buffer",
                        entity_id= self.hierarchical_id,
                        sim_time=self.env.now,
                        attributes={"item_id": item.id})
        return item
    
    def reserve_get_cancel(self,event):
        """
        Cancel a reserved get event.
        
        Parameters
        ----------
        event : simpy.Event
            The event that was reserved for getting an item.
        """
        return self.inbuiltstore.reserve_get_cancel(event)
    
    def reserve_put_cancel(self,event):
        """
        Cancel a reserved put event.
        
        Parameters
        ----------
        event : simpy.Event
            The event that was reserved for putting an item.
        """
        return self.inbuiltstore.reserve_put_cancel(event)
    def occupancy(self):
       return len(self.inbuiltstore.items) + len(self.inbuiltstore.ready_items)

    def ready_items(self):
       return self.inbuiltstore.ready_items
    
    def items(self):
         items = [i[0] for i in self.inbuiltstore.items] + self.inbuiltstore.ready_items
         return items
         

    def behaviour(self):
      
      #Simulates the buffer behavior, checking the state of the buffer and processing items.
      
      assert self.src_node is not None , f"Buffer '{self.id}' must have atleast 1 src_node."
      assert self.dest_node is not None , f"Buffer '{self.id}' must have atleast 1 dest_node."

      
      while True: 
        if self.inbuiltstore.ready_items or self.inbuiltstore.items: 
          self.update_state("RELEASING_STATE", self.env.now)
          print(f"T={self.env.now:.2f}: {self.id } is releasing an item from its in store")

        else:
          
          self.update_state("EMPTY_STATE", self.env.now)
          print(f"T={self.env.now:.2f}: {self.id } is waiting to get an item ")

        
        
    
        #yield self.env.all_of([put_event,get_event]) 
    
        
          

    
        









