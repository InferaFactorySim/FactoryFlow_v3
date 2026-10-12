class ConveyorBelt(BaseOperator):
    
     
    def __init__(self, env, name, num_stages,delay_per_stage):
        BaseOperator.__init__(self,env,name)
        
        self.num_stages=num_stages
        self.delay_per_stage=delay_per_stage
        
        #default parameter values
        self.start_time=0
        
        #A conveyor belt has at-least two stages
        assert(isinstance(self.num_stages,int) and (num_stages>=2))
        assert(isinstance(self.delay_per_stage,int))
        assert(isinstance(self.start_time, int))

        # create input and output buffers
        # corresponding to the first and last stages
        self.input_buf=simpy.Store(env,capacity=1)
        self.output_buf=simpy.Store(env,capacity=1)

        # a list to model stages
        self.stages=[None for i in range(num_stages)]
        
        #states
        self.define_states(["empty", "moving", "stalled"],start_state="empty")

        # start behavior
        self.process=env.process(self.behavior())


    def __str__(self):
        return self.name
    
    #A blocking methods to get/put jobs.
    #to be called with "yield"
    def get(self):
        return self.output_buf.get()
    
    def put(self,job):
        return self.input_buf.put(job)
    
    #Non-blocking methods to get state of the belt
    def can_put(self):
        if(len(self.input_buf.items)==0):
            return True
        else:
            return False
    
    def can_get(self):
        if(len(self.output_buf.items)!=0):
            return True
        else:
            return False
    
    # A non-blocking methods that 
    # returns a reference to the job
    # that would have been returned by get()
    def get_copy(self):
        assert(self.can_get())
        x = self.output_buf.items[0]
        return x


    # a method that returns a string 
    # with a pictorial representation 
    # of the occupancy of the conveyor belt.
    def show_occupancy(self):
        s = "|"
        s+=  "*|" if(len(self.input_buf.items)!=0 and self.input_buf.items[0]!=None) else " |"
        s+= "".join(["*|" if(self.stages[i]!=None) else " |" for i in range(1,self.num_stages-1)])
        s+= "*|" if(len(self.output_buf.items)!=0 and self.output_buf.items[0]!=None) else " |"
        return s
      
    def empty(self):
        if (len(self.input_buf.items)!=0):
            return False
        if (len(self.output_buf.items)!=0):
            return False
        for i in range(self.num_stages):
            if self.stages[i]!=None:
                return False
        return True

            
    def behavior(self):
        
        self.change_state("empty")

        #wait until the start_time
        yield (self.env.timeout(self.start_time))
        
        while True:
            
            # if the conveyor belt is empty, do nothing.
            if (self.empty()):
                self.change_state("empty")
                yield (self.env.timeout(self.delay_per_stage))

            # else, check if the conveyor belt is stalled
            elif (len(self.output_buf.items)!=0):
                self.change_state("stalled")
                print("T=",self.env.now+0.0, self.name, "Stalled", self.show_occupancy())
                yield (self.env.timeout(self.delay_per_stage))
            
            # else check if the conveyor belt can be moved.
            else:
                self.change_state("moving")
                
                #pick up the object from input_buf if present
                if(len(self.input_buf.items)==0):
                    obj=None
                else:
                    obj=yield self.input_buf.get()
                self.stages[0]=obj
            
                #shift right
                self.stages = [None] + self.stages[0:-1]

                # delay
                yield (self.env.timeout(self.delay_per_stage-1))
                # wait until the middle of the time-slot
                yield (self.env.timeout(0.5))

                #put the last object in output_buf
                obj=self.stages[-1]
                if(obj!=None):
                    yield self.output_buf.put(self.stages[-1])
                print("T=",self.env.now+0.0, self.name, "Shift-right", self.show_occupancy())
                # wait until an integer time instant
                yield (self.env.timeout(0.5))
