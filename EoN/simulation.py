from collections import defaultdict
import networkx as nx
import random
import heapq
import scipy
import EoN
import matplotlib.pyplot as plt


#######################
#                     #
#   Auxiliary stuff   #
#                     #
#######################



def _truncated_exponential_(rate, T):
    r'''returns a number between 0 and T from an
    exponential distribution conditional on the outcome being between 0 and T'''
    t = random.expovariate(rate)
    L = int(t/T)
    return t - L*T
   
class myQueue(object):
    r'''
    This class is used to store and act on a priority queue of events
    for event-driven simulations.

    Each queue is given a tmax (default is infinity) so that any event at later time is ignored.
    
    This is a priority queue of 4-tuples of the form (t, counter, function, function_arguments)

    The 'counter' is present just to break ties, which generally only occur when multiple events
    are put in place for the initial condition.

    note that the function is understood to have its first argument be t, and the tuple
    function_arguments does not include this first t.

    So function is called as function(t, *function_arguments)

    Previously I used a class of events, but sorting using the __lt__ function I wrote was
    significantly slower than simply using tuples.
    '''
    def __init__(self, tmax=float("Inf")):
        self._Q_ = []
        self.tmax=tmax
        self.counter = 0 #tie-breaker for putting things in priority queue
    def add(self, time, function, args = ()):
        r'''time is the time of the event.  args are the arguments of the
        function not including the first argument which must be time'''
        if time<self.tmax:   
            heapq.heappush(self._Q_, (time, self.counter, function, args))
            self.counter += 1
    def pop_and_run(self):
        t, counter, function, args = heapq.heappop(self._Q_)
        function(t, *args)
    def __len__(self): #this will allow for something like "while Q:" 
        return len(self._Q_)
        
        
class _ListDict_(object):
    r'''
    The Gillespie algorithm with rejection-sampling will involve a step 
    that samples a random element from a set.  This is slow in Python.  
    So I'm introducing a new class based on a stack overflow answer by
    Amber (http://stackoverflow.com/users/148870/amber) 
    for a question by
    tba (http://stackoverflow.com/users/46521/tba) 
    found at
    http://stackoverflow.com/a/15993515/2966723

    Based on some limited tests (in some perhaps-atypical networks),
    the benefit appears to be pretty small.  It may be worth creating 
    this data structure in C, but I doubt it.
    '''
    
    def __init__(self):
        self.item_to_position = {}
        self.items = []

    def __len__(self):
        return len(self.items)

    def add(self, item):
        if item in self.item_to_position:
            return
        self.items.append(item)
        self.item_to_position[item] = len(self.items)-1

    def remove(self, item):
        position = self.item_to_position.pop(item)
        last_item = self.items.pop()
        if position != len(self.items):
            self.items[position] = last_item
            self.item_to_position[last_item] = position

    def choose_random(self):
        return random.choice(self.items)



def _transform_to_node_history_(infection_times, recovery_times, tmin, SIR = True):
    r'''The original (v0.96 and earlier) returned infection_times and recovery_times.
    The new version returns node_history instead. This code transforms
    the former to the latter.
    
    It is only used for the continuous time cases.
    '''
    if SIR:
        node_history = defaultdict(lambda : ([tmin], ['S']))
        for node, time in infection_times.items():
            if time == tmin:
                node_history[node] = ([], [])
            node_history[node][0].append(time)
            node_history[node][1].append('I')
        for node, time in recovery_times.items():
            if time == tmin:
                node_history[node] = ([], [])
            node_history[node][0].append(time)
            node_history[node][1].append('R')        
    else:
        node_history = defaultdict(lambda : ([tmin], ['S']))
        for node, Itimes in infection_times.items():
            Rtimes = recovery_times[node]
            while Itimes:
                time = Itimes.pop(0)
                if time == tmin:
                    node_history[node] = ([], [])
                node_history[node][0].append(time)
                node_history[node][1].append('I')
                if Rtimes:
                    time = Rtimes.pop(0)
                    node_history[node][0].append(time)
                    node_history[node][1].append('S')
        
    return node_history


##########################
#                        #
#    SIMULATION CODE     #
#                        #
##########################

'''
    The code in the region below is used for stochastic simulation of 
    epidemics on networks
'''

def _simple_test_transmission_(u, v, p):
    r'''
    A simple test for whether u transmits to v assuming constant probability p
    
    From figure 6.8 of Kiss, Miller, & Simon.  Please cite the book if 
    using this test_transmission function for basic_discrete_SIR.

    This handles the simple case where transmission occurs with 
    probability p.

    Arguments :

        u (node)
            the infected node
        v : node
            the susceptible node
        p : number between 0 and 1
            the transmission probability

    Returns :
        
        :
            
            True if u will infect v (given opportunity)
            False otherwise
    '''

    return random.random()<p


def discrete_SIR(G, test_transmission=_simple_test_transmission_, args=(), 
                initial_infecteds=None, initial_recovereds = None, 
                rho = None, tmin = 0, tmax = float('Inf'),
                return_full_data = False):
    #tested in test_discrete_SIR
    r'''
    Simulates an SIR epidemic on G in discrete time, allowing user-specified transmission rules
    
    From figure 6.8 of Kiss, Miller, & Simon.  Please cite the book
    if using this algorithm.

    Return details of epidemic curve from a discrete time simulation.
    
    It assumes that individuals are infected for exactly one unit of 
    time and then recover with immunity.

    This is defined to handle a user-defined function
    `test_transmission(node1,node2,*args)`
    which determines whether transmission occurs.

    So elaborate rules can be created as desired by the user.

    By default it uses 
    `_simple_test_transmission_`
    in which case args should be entered as (p,)

    Arguments : 

        G: NetworkX Graph (or some other structure which quacks like a NetworkX Graph)
            The network on which the epidemic will be simulated.
        
        test_transmission: function(u,v,*args)
            (see below for args definition)
            A function that determines whether u transmits to v.
            It returns True if transmission happens and False otherwise.
            The default will return True with probability p, where args=(p,)

            This function can be user-defined.
            It is called like:
            test_transmission(u,v,*args)
            Note that if args is not entered, then args=(), and this call is 
            equivalent to
            test_transmission(u,v)

        args: a list or tuple
            The arguments of test_transmission coming after the nodes.  If 
            simply having transmission with probability p it should be 
            entered as 
            args=(p,)
            
            [note the comma is needed to tell Python that this is really a 
            tuple]

        initial_infecteds: node or iterable of nodes (default None)
            if a single node, then this node is initially infected
            if an iterable, then whole set is initially infected
            if None, then choose randomly based on rho.  If rho is also
            None, a random single node is chosen.
            If both initial_infecteds and rho are assigned, then there
            is an error.
       
        initial_recovereds : as for initial_infecteds, but initially 
            recovered nodes.
            
        rho : number
            initial fraction infected. number is int(round(G.order()*rho))

        tmin : start time
        
        tmax : stop time (if not extinct first).  Default step is 1.

        return_full_data: boolean (default False)
            Tells whether `node_history` should be returned.  

            

    Returns : 
        :
        if return_full_data is False returns 
        
        t, S, I, R:
                scipy arrays
            
        Or `if return_full_data is True` returns
      
        t, S, I, R, node_history
            node_history is a dict and 
            node_history[node] is a tuple (time_list, status_list)
            the time_list is the times the node's status changes
            the status_list is the status at those times.

    
    :SAMPLE USE:

    ::

        import networkx as nx
        import EoN
        import matplotlib.pyplot as plt
        G = nx.fast_gnp_random_graph(1000,0.002)
        t, S, I, R = EoN.discrete_SIR(G, args = (0.6,), 
                                            initial_infecteds=range(20))
        plt.plot(t,I)
    
    
    Because this sample uses the defaults, it is equivalent to a call to 
    basic_discrete_SIR
    '''
    if rho is not None and initial_infecteds is not None:
        raise EoN.EoNError("cannot define both initial_infecteds and rho")

    
    
    if initial_infecteds is None:  #create initial infecteds list if not given
        if rho is None:
            initial_number = 1
        else:
            initial_number = int(round(G.order()*rho))
        initial_infecteds=random.sample(G.nodes(), initial_number)
    elif G.has_node(initial_infecteds):
        initial_infecteds=[initial_infecteds]
    #else it is assumed to be a list of nodes.

    if return_full_data:
        node_history = defaultdict(lambda : ([tmin], ['S']))
        for node in initial_infecteds:
            node_history[node] = ([tmin], ['I'])
        if initial_recovereds is not None:
            node_history[node] = ([tmin], ['R'])
    
    N=G.order()
    t = [tmin]
    S = [N-len(initial_infecteds)]
    I = [len(initial_infecteds)]
    R = [0]
    
    susceptible = defaultdict(lambda: True)  
    #above line is equivalent to u.susceptible=True for all nodes.
    
    for u in initial_infecteds:
        susceptible[u] = False
    if initial_recovereds is not None:
        for u in initial_recovereds:
            susceptible[u] = False
        
    infecteds = initial_infecteds
    
    #may be useful to rewrite this someday to be more like SIS version
    #taking advantage of sets.
    while infecteds and t[-1]<tmax:
        new_infecteds = []
        for u in infecteds:
            for v in G.neighbors(u):
                if susceptible[v] and test_transmission(u, v, *args):
                    new_infecteds.append(v)
                    susceptible[v] = False

        if return_full_data:
            next_time = t[-1]+1
            if next_time <= tmax:
                for u in infecteds:
                    node_history[u][0].append(next_time)
                    node_history[u][1].append('R')
                for v in new_infecteds:
                    node_history[v][0].append(next_time)
                    node_history[v][1].append('I')

        infecteds = new_infecteds

        R.append(R[-1]+I[-1])
        I.append(len(infecteds))
        S.append(S[-1]-I[-1])
        t.append(t[-1]+1)
    if not return_full_data:
        return scipy.array(t), scipy.array(S), scipy.array(I), \
               scipy.array(R)
    else:
        return scipy.array(t), scipy.array(S), scipy.array(I), \
                scipy.array(R), node_history



def basic_discrete_SIR(G, p, initial_infecteds=None, 
                                initial_recovereds = None, rho = None,
                                tmin = 0, tmax=float('Inf'), 
                                return_full_data = False):
    #tested in test_basic_discrete_SIR   
    r'''
    Performs simple discrete SIR simulation assuming constant transmission probability p.
    
    From figure 6.8 of Kiss, Miller, & Simon.  Please cite the book if 
    using this algorithm.

    Does a simulation of the simple case of all nodes transmitting
    with probability p independently to each neighbor and then
    recovering.

    Arguments : 

        G (networkx Graph)
            The network the disease will transmit through.
            
        p : number
            transmission probability
            
        initial_infecteds: node or iterable of nodes (default None)
            if a single node, then this node is initially infected
            if an iterable, then whole set is initially infected
            if None, then choose randomly based on rho.  If rho is also
            None, a random single node is chosen.
            If both initial_infecteds and rho are assigned, then there
            is an error.
       
        initial_recovereds : as for initial_infecteds, but initially 
            recovered nodes.
            
        rho : number
            initial fraction infected. number is int(round(G.order()*rho))
        
        tmin : start time
        
        tmax : stop time (if not extinct first).  Default step is 1.

        return_full_data: boolean (default False)
            Tells whether `node_history` should be returned.  

    Returns : 
        :
        if return_full_data is False returns 
        
        t, S, I, R:
            scipy arrays
            these scipy arrays give all the times observed and the number 
            in each state at each time.
            
        Or `if return_full_data is True` returns
      
        t, S, I, R, node_history
            node_history is a dict and 
            node_history[node] is a tuple (time_list, status_list)
            the time_list is the times the node's status changes
            the status_list is the status at those times.

    
    :SAMPLE USE:

    ::


        import networkx as nx
        import EoN
        import matplotlib.pyplot as plt
        G = nx.fast_gnp_random_graph(1000,0.002)
        t, S, I, R = EoN.basic_discrete_SIR(G, 0.6)
        plt.plot(t,S)
    
    
        #This sample may be boring if the randomly chosen initial infection
        #doesn't trigger an epidemic.

'''

    return discrete_SIR(G, _simple_test_transmission_, (p,), 
                                    initial_infecteds, initial_recovereds, 
                                    rho, tmin, tmax, return_full_data)

def basic_discrete_SIS(G, p, initial_infecteds=None, rho = None,
                                tmin = 0, tmax = 100, return_full_data = False):
    
    '''Does a simulation of the simple case of all nodes transmitting
    with probability p independently to each susceptible neighbor and then
    recovering.
    
    This is not directly described in Kiss, Miller, & Simon.
    
    
    
    Arguments : 

        G (networkx Graph)
            The network the disease will transmit through.
            
        p : number
            transmission probability
            
        initial_infecteds: node or iterable of nodes (default None)
            if a single node, then this node is initially infected
            if an iterable, then whole set is initially infected
            if None, then choose randomly based on rho.  If rho is also
            None, a random single node is chosen.
            If both initial_infecteds and rho are assigned, then there
            is an error.
       
        rho : number
            initial fraction infected. number is int(round(G.order()*rho))

        return_full_data: boolean (default False)
            Tells whether `node_history` should be returned.  

    Returns : 
        :
            t, S, I, : 
                All scipy arrays (if return_full_data is False)

            t, S, I, node_history
                node_history is a dict and 
                node_history[node] is a tuple (time_list, status_list)
                the time_list is the times the node's status changes
                the status_list is the status at those times.
                (if return_full_data is True)

    '''
    
    if rho is not None and initial_infecteds is not None:
        raise EoN.EoNError("cannot define both initial_infecteds and rho")

    
    
    if initial_infecteds is None:  #create initial infecteds list if not given
        if rho is None:
            initial_number = 1
        else:
            initial_number = int(round(G.order()*rho))
        initial_infecteds=random.sample(G.nodes(), initial_number)
    elif G.has_node(initial_infecteds):
        initial_infecteds=[initial_infecteds]
    #else it is assumed to be a list of nodes.

    if return_full_data:
        node_history = defaultdict(lambda : ([tmin], ['S']))
        for u in initial_infecteds:
            node_history[u] = ([tmin], ['I'])
    N=G.order()
    t = [tmin]
    S = [N-len(initial_infecteds)]
    I = [len(initial_infecteds)]
    
    infecteds = set(initial_infecteds)
    while infecteds and t[-1]<tmax:
        next_infecteds = set()
        for u in infecteds:
            next_infecteds.union({v for v in G.neighbors(u) if random.random()<p and v not in infecteds})

        if return_full_data:
            next_time = t[-1]+1
            if next_time<= tmax:
                for u in infecteds:
                    node_history[u][0].append(next_time)
                    node_history[u][1].append('S')
                for v in next_infecteds:
                    node_history[v][0].append(next_time)
                    node_history[v][1].append('I')
        infecteds = next_infecteds
        t.append(t[-1]+1)
        S.append(N-len(infecteds))
        I.append(len(infecteds))
            
        
    if not return_full_data:
        return scipy.array(t), scipy.array(S), scipy.array(I)
    else:
        return scipy.array(t), scipy.array(S), scipy.array(I), node_history

    
    
def percolate_network(G, p):
    #tested indirectly in test_basic_discrete_SIR   

    r'''
    Performs percolation on a network G with each edge persisting with probability p
    
    From figure 6.10 of Kiss, Miller, & Simon.  Please cite the book
    if using this algorithm.

    Performs bond percolation on the network G, keeping edges with 
    probability p

    Arguments : 

        G (networkx Graph)
        p : number between 0 and 1
            the probability of keeping edge

    Returns : 
        :
        H : NetworkX Graph
            A network with same nodes as G, but with each edge retained 
            independently with probability p.
        
    :SAMPLE USE:

    ::


        import networkx as nx
        import EoN
        import matplotlib.pyplot as plt
        G = nx.fast_gnp_random_graph(1000,0.002)
        H = EoN.percolate_network(G, 0.6)

        #H is now a graph with about 60% of the edges of G
'''

    H = nx.Graph()
    H.add_nodes_from(G.nodes())
    for edge in G.edges():
        if random.random()<p:
            H.add_edge(*edge)
    return H

def _edge_exists_(u, v, H):
    r'''
    Tests if directed edge u, v exists in graph H.
    
    From figure 6.10 of Kiss, Miller, & Simon.  Please cite the book
    if using this algorithm.

    Tests whether H has an edge from u to v.

    Arguments : 

        u : node
        v : node
        H : graph

    Returns : 
        :
        True : if H has the edge
        False : if H does not have the edge
    '''
    return H.has_edge(u,v)

def percolation_based_discrete_SIR(G, p, 
                                            initial_infecteds=None, 
                                            initial_recovereds = None,
                                            rho = None, tmin = 0,
                                            tmax = float('Inf'),
                                            return_full_data = False):
    #tested in test_basic_discrete_SIR   
    r'''
    perfoms a simple SIR epidemic but using percolation as the underlying method.
    
    From figure 6.10 of Kiss, Miller, & Simon.  Please cite the book
    if using this algorithm.

    
    The simple case of all nodes transmitting with probability p 
    independently to each neighbor and then recovering, but using a 
    percolation-based approach.  
    
    Note:
        You probably DON'T REALLY WANT TO USE THIS.
        See basic_discrete_SIR which should produce equivalent 
    outputs.  
    
        That algorithm will be faster than this one.  
    
    The value of this function is that by performing many simulations we 
    can see that the outputs of the two are equivalent.  
    
    This algorithm leads to a better understanding of the theory.


    Arguments : 

        G (networkx Graph)
            The network the disease will transmit through.
        p : number
            transmission probability

        initial_infecteds: node or iterable of nodes (default None)
            if a single node, then this node is initially infected
            if an iterable, then whole set is initially infected
            if None, then choose randomly based on rho.  If rho is also
            None, a random single node is chosen.
            If both initial_infecteds and rho are assigned, then there
            is an error.
       
       
        initial_recovereds : as for initial_infecteds, but initially 
            recovered nodes.
            
        rho : number
            initial fraction infected. number is int(round(G.order()*rho))
        
        tmin : start time
        
        tmax : stop time (if not extinct first).  Default step is 1.

        return_full_data: boolean (default False)
            Tells whether `node_history` should be returned.  

    Returns : 
        :
        t, S, I, R : Scipy arrays
        
        OR if `return_full_data is True`:
            
        t, S, I, R, node_history 
            node_history is a dict and 
            node_history[node] is a tuple (time_list, status_list)
            the time_list is the times the node's status changes
            the status_list is the status at those times.
    
    :SAMPLE USE:

    ::

        import networkx as nx
        import EoN
        import matplotlib.pyplot as plt
        G = nx.fast_gnp_random_graph(1000,0.002)
        t, S, I, R = EoN.percolation_based_discrete_SIR(G, p)
        plt.plot(t,S)
    
    This is equivalent to basic_discrete_epidemic (but many simulations
    may be needed before it's clear, since these are stochastic)

'''

    H = percolate_network(G, p)
    return discrete_SIR(H, test_transmission=H.has_edge, 
                                initial_infecteds=initial_infecteds, 
                                initial_recovereds = initial_recovereds,
                                rho = rho, tmin = tmin, tmax = tmax, 
                                return_full_data=return_full_data)
                                

def estimate_SIR_prob_size(G, p):
    #tested in test_estimate_SIR_prob_size
    r'''
    Uses percolation to estimate the probability and size of epidemics assuming constant transmission probability p
    
    From figure 6.12 of Kiss, Miller, & Simon.  Please cite the
    book if using this algorithm.

    Provies an estimate of epidemic probability and size assuming a 
    fixed transmission probability p.  
    
    The estimate is found by performing bond percolation and then 
    finding the largest connected component in the remaining network.  
    
    This assumes that there is a single giant component above threshold.  

    It will not be an appropriate measure if the network is made up of 
    several densely connected components with very weak connections 
    between these components.

    Arguments : 

        G (networkx Graph)
            The network the disease will transmit through.
        p : number
            transmission probability

    Returns : 
        :
        PE, AR : 
            (numbers) estimates of the probability and proportion 
            infected (attack rate) in epidemics
            (the two are equal, but each given for consistency with 
            `estimate_directed_SIR_prob_size`)
          
            
    :SAMPLE USE:

    ::

        import networkx as nx
        import EoN
    
        G = nx.fast_gnp_random_graph(1000,0.002)
        PE, AR = EoN.estimate_SIR_prob_size(G, 0.6)

    '''
    H = percolate_network(G, p)
    size = max((len(CC) for CC in nx.connected_components(H)))
    returnval = float(size)/G.order()
    return returnval, returnval


def directed_percolate_network(G, tau, gamma, weights = True):
    #indirectly tested in test_estimate_SIR_prob_size
    r'''
    performs directed percolation, assuming that transmission and recovery are Markovian
    
    
    From figure 6.13 of Kiss, Miller, & Simon.  Please cite the
    book if using this algorithm.  
    
    This performs directed percolation corresponding to an SIR epidemic
    assuming that transmission is at rate tau and recovery at rate 
    gamma

    :SEE ALSO:

    -nonMarkov_directed_percolate_network which allows for duration and time
    to infect to come from other distributions.
    
    -nonMarkov_directed_percolate_network which allows for more complex 
    rules
    
    Arguments : 

        G (networkx Graph)
            The network the disease will transmit through.
        tau (positive float)
            transmission rate
        gamma : number
            recovery rate
        weights : boolean
            if True, then includes information on time to recovery
            and delay to transmission.  If False, just the directed graph.

    Returns : 
        :
        H : networkx DiGraph  (directed graph)
            a u->v edge exists in H if u would transmit to v if ever 
            infected.
        
            The edge has a time attribute (time_to_infect) which gives the 
            delay from infection of u until transmission occurs.
        
            Each node u has a time attribute (duration) which gives the 
            duration of its infectious period.
        
    :SAMPLE USE:


    ::

        import networkx as nx
        import EoN
        
        G = nx.fast_gnp_random_graph(1000,0.002)
        H = EoN.directed_percolate_network(G, 2, 1)

    '''
    
    #simply calls directed_percolate_network_with_timing, using markovian rules.
    
    def trans_time_fxn(u, v, tau):
        if tau>0:
            return random.expovariate(tau)
        else:
            return float('Inf')
    trans_time_args = (tau,)
    
    def rec_time_fxn(u, gamma):
        if gamma>0:
            return random.expovariate(gamma)
        else:
            return float('Inf')
    rec_time_args = (gamma,)
    
    return nonMarkov_directed_percolate_network_with_timing(G, trans_time_fxn, 
                                                            rec_time_fxn,  
                                                            trans_time_args,
                                                            rec_time_args, 
                                                            weights=weights)
                
def _out_component_(G, source):
    '''
    rather than following the pseudocode in figure 6.15 of 
        Kiss, Miller & Simon,
    this uses a built-in Networkx command.  

    finds the set of nodes (including source) which are reachable from 
    nodes in source.

    Arguments : 

        G (networkx Graph)
            The network the disease will transmit through.
        source : either a node or an iterable of nodes (set, list, tuple)
            The nodes from which the infections start.  We assume no node
            will ever have a name that is an iterable of other node names.
            It will run, but may not use source user expects.

    :Warning: 
        if the graph G has nodes like 1, 2, 3, and (1,2,3), then a
        source of (1,2,3) is potentially ambiguous.  It will interpret
        the source as the single node (1,2,3)

    Returns : 
        :            
        reachable_nodes : set
            the set of nodes reachable from source (including source).


    '''
    if G.has_node(source): 
        source_nodes = {source}
    else:
        source_nodes = set(source)
        
    reachable_nodes = set().union(source_nodes)

    for node in source_nodes:
        reachable_nodes = reachable_nodes.union(
                                        set(nx.descendants(G, node)))

    
    return reachable_nodes

def _in_component_(G, target):
    r'''
    creates the _in_component_ by basically reversing _out_component_.

    Arguments : 

        G (networkx Graph)
            The network the disease will transmit through.
        target : a target node (or iterable of target nodes)
            The node whose infection we are interested in.

            In principle target could be an iterable, but in this case we 
            would be finding those possible sources whose infection leads to 
            infection of at least one target, not all.

    :Warning: 
        
        if the graph G has nodes like 1, 2, 3, and (1,2,3), then a
        target of (1,2,3) is potentially ambiguous.  It will interpret
        the target as the single node (1,2,3)

    Returns : 

        source_nodes : (set)
            the set of nodes (including target) from which target is 
            reachable


    '''
    if G.has_node(target):
        #potential bug if for example G has  nodes like 1, 2, 3, and 
        #(1,2,3).  Then a target of (1,2,3) is ambiguous
        target_nodes = {target}
    else:
        target_nodes = set(target)

    source_nodes = set().union(target_nodes)
    
    for node in target_nodes:
        source_nodes = source_nodes.union(set(nx.ancestors(G, node)))

    return source_nodes


def get_infected_nodes(G, tau, gamma, initial_infecteds=None, 
                initial_recovereds = None):
    r'''
    Finds all eventually infected nodes in a simulation, through a percolation approach 
    
    From figure 6.15 of Kiss, Miller, & Simon.  Please cite the book if 
    using this algorithm

    Assumes that
    the intial infecteds are as given and transmission occurs with rate 
    tau and recovery with rate gamma.          
    

    Note that the output of this algorithm is stochastic.
    
    This code has similar run-time whether an epidemic occurs or not.
    There are much faster ways to implement an algorithm giving the same 
    output, for example by actually running an epidemic.
    
    :WARNING:
    
    why are you using this command? If it's to better understand some
    concept, that's fine.  But this command IS NOT an efficient way to
    calculate anything.  Don't do it like this.  Use one of the other
    algorithms.  Try fast_SIR, for example.
    
    Arguments : 

        G (networkx Graph)
            The network the disease will transmit through.
        tau (positive float)
            transmission rate
        gamma : number
            recovery rate
        initial_infecteds: node or iterable of nodes
            if a single node, then this node is initially infected
            if an iterable, then whole set is initially infected
            if None, then a randomly chosen node is initially infected.
        initial_recovereds: node or iterable of nodes
            if a single node, then this node is initially recovered
            if an iterable, then whole set is initially recovered

    Returns : 
        :
            
        infected_nodes : set
            the set of nodes infected eventually in a simulation.
        
    :SAMPLE USE:

        import networkx as nx
        import EoN
    
        G = nx.fast_gnp_random_graph(1000,0.002)
        finalR = EoN.get_infected_nodes(G, 2, 1, initial_infecteds=[0, 5])
    
    
    finds the nodes infected if 0 and 5 are the initial nodes infected
    and tau=2, gamma=1
    '''    
    if initial_recovereds is None:
        initial_recovereds = set()
    elif G.has_node(initial_recovereds):
        initial_recovereds = set([initial_recovereds])
    else:
        initial_recovereds = set(initial_recovereds)
    if initial_infecteds is None:
        while True:
            node = random.choice(G.nodes())
            if node not in initial_recovereds:
                break
        initial_infecteds=set([node])
    elif G.has_node(initial_infecteds):
        initial_infecteds=set([initial_infecteds])
    else:
        initial_infecteds = set(initial_infecteds)
    if initial_infecteds.intersection(initial_recovereds):
        raise EoN.EoNError("initial infecteds and initial recovereds overlap")
    H = directed_percolate_network(G, tau, gamma)
    for node in initial_recovereds:
        H.remove_node(node)
    infected_nodes = _out_component_(H, initial_infecteds)
    return infected_nodes


def estimate_directed_SIR_prob_size(G, tau, gamma):
    #tested in test_estimate_SIR_prob_size
    '''
    Predicts probability and attack rate assuming continuous-time Markovian SIR disease on network G
    
    From figure 6.17 of Kiss, Miller, & Simon.  Please cite the book if 
    using this algorithm
    
    
    
    :SEE ALSO:

    estimate_nonMarkov_SIR_prob_size which handles nonMarkovian versions

    Arguments : 

        G (networkx Graph)
            The network the disease will transmit through.
        tau (positive float)
            transmission rate
        gamma : number
            recovery rate

    Returns : 
        :
        PE, AR  :  numbers (between 0 and 1)
            Estimates of epidemic probability and attack rate found by 
            performing directed percolation, finding largest strongly 
            connected component and finding its in/out components.
        
    :SAMPLE USE:

    ::


        import networkx as nx
        import EoN
    
        G = nx.fast_gnp_random_graph(1000,0.003)
        PE, AR = EoN.estimate_directed_SIR_prob_size(G, 2, 1)
    
    '''
    
    H = directed_percolate_network(G, tau, gamma)
    return estimate_SIR_prob_size_from_dir_perc(H)

def estimate_SIR_prob_size_from_dir_perc(H):
    #indirectly tested in test_estimate_SIR_prob_size
    r'''
    Estimates probability and size of SIR epidemics for an input network after directed percolation
    
    From figure 6.17 of Kiss, Miller, & Simon.  Please cite the book if 
    using this algorithm

    Arguments : 

        H:  directed graph (assumed to be from directed percolation on 
            previous graph G)

    Returns : 
        :
        PE, AR  :  numbers
            Estimates of epidemic probability and attack rate found by 
            finding largest strongly connected component and finding in/out 
            components.
        
    :SAMPLE USE:


    ::

        import networkx as nx
        import EoN

        G = nx.fast_gnp_random_graph(1000,0.003)
        H = some_user_defined_operation_to_do_percolation(G, argument)
        PE, AR = EoN.estimate_SIR_prob_size_from_dir_perc(H)

    '''

    Hscc = max((nx.strongly_connected_components(H)), key = len)
    u = list(Hscc)[0]  #random.choice(Hscc)
    inC = _in_component_(H, u) #includes both H_{IN} and H_{SCC}
    outC = _out_component_(H, u) #includes both H_{OUT} and H_{SCC}
    N=float(H.order())
    PE = len(inC)/N
    AR = len(outC)/N
    return PE, AR
 
def estimate_nonMarkov_SIR_prob_size_with_timing(G, 
                                                trans_time_fxn, 
                                                rec_time_fxn, 
                                                trans_time_args=(),
                                                rec_time_args=()):
    '''
    estimates probability and size for user-input transmission and recovery time functions.
    
       Arguments : 
        G : Networkx Graph
            the input graph
        trans_time_fxn : function
            trans_time_fxn(u, v, *trans_time_args) 
            returns the delay from u's infection to transmission to v.
        rec_time_fxn : function
            rec_time_fxn(u, *rec_time_args)
            returns the delay from u's infection to its recovery.
        trans_time_args : tuple
            any additional arguments required by trans_time_fxn.  For example
            weights of nodes.
        rec_time_args : tuple
            any additional arguments required by rec_time_fxn

    Returns : 
        :
        PE, AR  :  numbers (between 0 and 1)
            Estimates of epidemic probability and attack rate found by 
            finding largest strongly connected component and finding in/out 
            components.
        
    :SAMPLE USE:


    ::

        #mimicking the standard version with transmission rate tau
        #and recovery rate gamma
        #equivalent to 
        #PE, AR = EoN.estimate_SIR_prob_size(G, tau, gamma)
    
        import networkx as nx
        import EoN
        import random
        from collections import defaultdict
    
        G=nx.fast_gnp_random_graph(1000,0.002)
        
        tau = 2
        
        gamma = 1
    
        def trans_time_fxn(u,v, tau):
            
            return random.expovariate(tau)
            
        def rec_time_fxn(u, gamma):
            
            return random.expovariate(gamma)
        
        PE, AR = EoN.estimate_nonMarkov_SIR_prob_size(G, 
                                                    trans_time_fxn=trans_time_fxn,
                                                    rec_time_fxn = rec_time_fxn,
                                                    trans_time_args = (tau,),
                                                    rec_time_args = (gamma,)
                                                    )

    '''
    
    H = nonMarkov_directed_percolate_network_with_timing(G, 
                                                        trans_time_fxn,
                                                        rec_time_fxn,
                                                        trans_time_args,
                                                        rec_time_args)
    return estimate_SIR_prob_size_from_dir_perc(H)
    
    
def estimate_nonMarkov_SIR_prob_size(G, xi, zeta, transmission):
    '''
    Predicts epidemic probability and size using nonMarkov_directed_percolate_network.
    
    This is not directly described in Kiss, Miller, & Simon, but is based on (fig 6.18).
    
    Note:
        You probably DON'T REALLY WANT TO USE THIS.
        Check if estimate_nonMarkov_prob_size_with_timing fits your needs better.
    
    Arguments : 

        G (networkx Graph)
            The input graph

        xi : dict
            xi[u] gives all necessary information to determine what u's 
            infectiousness is.
        zeta : dict
            zeta[v] gives everything needed about v's susceptibility

        transmission : user-defined function
            transmission(xi[u], zeta[v]) determines whether u transmits to 
            v.

    Returns : 
        :
        PE, AR  :  numbers (between 0 and 1)
            Estimates of epidemic probability and attack rate found by 
            finding largest strongly connected component and finding in/out 
            components.
        
    :SAMPLE USE:


    ::

        #mimicking the standard version with transmission rate tau
        #and recovery rate gamma
    
        import networkx as nx
        import EoN
        import random
        from collections import defaultdict
    
        G=nx.fast_gnp_random_graph(1000,0.002)
        tau = 2
        gamma = 1
    
        xi = {node:random.expovariate(gamma) for node in G.nodes()}  
        #xi[node] is duration of infection of node.
        
        zeta = defaultdict(lambda : tau) #every node has zeta=tau, so same 
                                        #transmission rate
        
        def my_transmission(infection_duration, trans_rate):
            #infect if duration is longer than time to infection.
            if infection_duration > random.expovariate(trans_rate):
                return True
            else:  
                return False
        
        PE, AR = EoN.estimate_nonMarkov_SIR_prob_size(G, xi, zeta, 
                                                        my_transmission)
            

    '''
    
    H = nonMarkov_directed_percolate_network(G, xi, zeta, transmission)
    return estimate_SIR_prob_size_from_dir_perc(H)
        
def nonMarkov_directed_percolate_network_with_timing(G, 
                                                    trans_time_fxn, 
                                                    rec_time_fxn,
                                                    trans_time_args=(),
                                                    rec_time_args=(), 
                                                    weights=True):
    r'''
    Performs directed percolation on G for user-specified transmission time
    and recovery time distributions.
    
    A generalization of figure 6.13 of Kiss, Miller & Simon
    
    The returned graph is built by assigning each node an infection duration 
    and then each edge (in each direction) a delay until transmission.  
    If   delay<duration  it adds directed edge to H.  
    
    if weights is True, then returned graph contains the duration and delays
    as weights.  Else it's just a directed graph.
    
    The arguments are very much like in fast_nonMarkov_SIR
    
    Arguments : 
        G : Networkx Graph
            the input graph
        trans_time_fxn : function
            trans_time_fxn(u, v, *trans_time_args) 
            returns the delay from u's infection to transmission to v.
        rec_time_fxn : function
            rec_time_fxn(u, *rec_time_args)
            returns the delay from u's infection to its recovery.
        trans_time_args : tuple
            any additional arguments required by trans_time_fxn.  For example
            weights of nodes.
        rec_time_args : tuple
            any additional arguments required by rec_time_fxn
        weights : boolean
            if true, then return network with these weights.
    Note:
        if delay == duration, we assume infection happens.
          
    :SEE ALSO:
        -directed_percolate_network
        if it's just a constant transmission and recovery rate.

        -nonMarkov_directed_percolate_network
        if your rule for creating the percolated network cannot be expressed
        as simply calculating durations and delays until transmission.
    

        '''
    H = nx.DiGraph()
    if weights:
        for u in G.nodes():
            duration = rec_time_fxn(u, *rec_time_args)
            H.add_node(u, duration = duration)
            for v in G.neighbors(u):
                delay = trans_time_fxn(u, v, *trans_time_args)
                if delay<=duration:
                    H.add_edge(u,v, delay_to_infection = delay)
    else:
        for u in G.nodes():
            duration = rec_time_fxn(u, *rec_time_args)
            H.add_node(u)
            for v in G.neighbors(u):
                delay = trans_time_fxn(u, v, *trans_time_args)
                if delay<=duration:
                    H.add_edge(u,v)
    return H

def nonMarkov_directed_percolate_network(G, xi, zeta, transmission):
    r'''
    performs directed percolation on a network following user-specified rules.
    
    From figure 6.18 of Kiss, Miller, & Simon.  
    Please cite the book if using this algorithm.
    
    Note:
        You probably DON'T REALLY WANT TO USE THIS.
        Check if nonMarkov_directed_percolate_with_timing fits your needs better.

    xi and zeta are dictionaries of whatever data is needed so that 
    xi[u] and zeta[v] 
    are enough to determine the probability of a u-v transmission.

    transmissision is a user-defined function taking xi[u] and zeta[v] 
    and returning True if a transmission would occur

    Arguments : 

        G (networkx Graph)
            The input graph

        xi : dict
            xi[u] gives all necessary information to determine what us 
            infectiousness is.
        zeta : dict
            zeta[v] gives everything needed about vs susceptibility

        transmission : user-defined function
            transmission(xi[u], zeta[v]) determines whether u transmits to 
            v.

    Returns : 
        :
        H : networkx DiGraph (directed graph)
            Edge u,v exists in H if disease will transmit given the opportunity.
    
    :SAMPLE USE:

    for now, I'm being lazy.  
    Look at the sample for estimate_nonMarkov_SIR_prob_size to infer it.
    
    :SEE ALSO:
    -nonMarkov_directed_percolate_network_with_timing
    if your rule for creating the percolated network is based on calculating
    a recovery time for each node and then calculating a transmission time for 
    the edges this will be better.
    
    -directed_percolate_network
    if it's just a constant transmission and recovery rate.
'''
    H = nx.DiGraph()
    for u in G.nodes():
        H.add_node(u)
        for v in G.neighbors(u):
            if transmission(xi[u],zeta[v]):
                H.add_edge(u,v)
    return H
    
    
    
    
### Code starting here does event-driven simulations ###


def _find_trans_and_rec_delays_SIR_(node, sus_neighbors, trans_time_fxn, 
                                    rec_time_fxn,  trans_time_args=(),
                                    rec_time_args=()):

    rec_delay = rec_time_fxn(node, *rec_time_args)
    trans_delay={}
    for target in sus_neighbors:
        trans_delay[target] = trans_time_fxn(node, target, *trans_time_args)
    return trans_delay, rec_delay
    

def _process_trans_SIR_(time, G, node, times, S, I, R, Q, status, 
                            rec_time, pred_inf_time, trans_and_rec_time_fxn, 
                            trans_and_rec_time_args = ()):
    r'''
    From figure A.4 of Kiss, Miller, & Simon.  Please cite the book if 
    using this algorithm.

    Arguments : 

        time : number
            time of transmission
        G (networkx Graph)
        node : node
            node receiving transmission.
        times : list
            list of times at which events have happened
        S, I, R : lists
            lists of numbers of nodes of each status at each time
        Q : myQueue
            the queue of events
        status : dict
            dictionary giving status of each node
        rec_time : dict
            dictionary giving recovery time of each node
        pred_inf_time : dict
            dictionary giving predicted infeciton time of nodes 
        trans_and_rec_time_fxn : function
            trans_and_rec_time_fxn(node, susceptible_neighbors, *trans_and_rec_time_args) 
            returns tuple consisting of
               dict of delays until transmission from node to neighbors and 
               float having delay until recovery of node
            An example of how to use this appears in the code fast_SIR where
            depending on whether inputs are weighted, it constructs different
            versions of this function and then calls fast_nonMarkov_SIR.
        trans_and_rec_time_args : tuple (default empty)
            see trans_and_rec_time_fxn
    Returns : 
        :
        nothing returned

    MODIFIES
    --------
    status : updates status of newly infected node
    rec_time : adds recovery time for node
    times : appends time of event
    S : appends new S (reduced by 1 from last)
    I : appends new I (increased by 1)
    R : appends new R (same as last)
    Q : adds recovery and transmission events for newly infected node.
    pred_inf_time : updated for nodes that will receive transmission

    '''

    if status[node] == 'S':  #nothing happens if already infected.
        status[node] = 'I'
        times.append(time)
        S.append(S[-1]-1) #one less susceptible
        I.append(I[-1]+1) #one more infected
        R.append(R[-1])   #no change to recovered
        
        
        suscep_neighbors = [v for v in G.neighbors(node) if status[v]=='S']

        trans_delay, rec_delay = trans_and_rec_time_fxn(node, suscep_neighbors,
                                                *trans_and_rec_time_args)
                                
        rec_time[node] = time + rec_delay
        if rec_time[node]<=Q.tmax:
            Q.add(rec_time[node], _process_rec_SIR_, 
                            args = (node, times, S, I, R, status))
        for v in trans_delay:
            inf_time = time + trans_delay[v]
            if inf_time<= rec_time[node] and inf_time < pred_inf_time[v] and inf_time<=Q.tmax:
                Q.add(inf_time, _process_trans_SIR_, 
                              args = (G, v, times, S, I, R, Q, 
                                        status, rec_time, pred_inf_time, 
                                        trans_and_rec_time_fxn,
                                        trans_and_rec_time_args
                                     )
                             )
                pred_inf_time[v] = inf_time
    
def _process_rec_SIR_(time, node, times, S, I, R, status):
    r'''From figure A.3 of Kiss, Miller, & Simon.  Please cite the
    book if using this algorithm.

    Arguments : 

        event : event
            has details on node and time
        times : list
            list of times at which events have happened
        S, I, R : lists
            lists of numbers of nodes of each status at each time
        status : dict
            dictionary giving status of each node


    Returns : 
        :
        Nothing

    MODIFIES
    ----------
    status : updates status of newly recovered node
    times : appends time of event
    S : appends new S (same as last)
    I : appends new I (decreased by 1)
    R : appends new R (increased by 1)
    '''
    times.append(time)
    S.append(S[-1])   #no change to number susceptible
    I.append(I[-1]-1) #one less infected
    R.append(R[-1]+1) #one more recovered
    status[node] = 'R'
    
def _trans_and_rec_time_Markovian_const_trans_(node, sus_neighbors, tau, rec_rate_fxn):
    
    duration = random.expovariate(rec_rate_fxn(node))

        
    trans_prob = 1-scipy.exp(-tau*duration)
    number_to_infect = scipy.random.binomial(len(sus_neighbors),trans_prob)
        #print(len(suscep_neighbors),number_to_infect,trans_prob, tau, duration)
    transmission_recipients = random.sample(sus_neighbors,number_to_infect)
    trans_delay = {}
    for v in transmission_recipients:
        trans_delay[v] = _truncated_exponential_(tau, duration)
    return trans_delay, duration

def fast_SIR(G, tau, gamma, initial_infecteds = None, initial_recovereds = None, 
                rho = None, tmin = 0, tmax=float('Inf'), transmission_weight = None, 
                recovery_weight = None, return_full_data = False):
    r'''
    fast SIR simulation assuming exponentially distributed infection and
    recovery times
    
    From figure A.3 of Kiss, Miller, & Simon.  Please cite the
    book if using this algorithm.

    
    

    Arguments : 

        G (networkx Graph)
            The underlying network

        tau (positive float)
            transmission rate per edge

        gamma : number
            recovery rate per node

        initial_infecteds: node or iterable of nodes
            if a single node, then this node is initially infected
            if an iterable, then whole set is initially infected
            if None, then choose randomly based on rho.  If rho is also
            None, a random single node is chosen.
            If both initial_infecteds and rho are assigned, then there
            is an error.
       
        initial_recovereds: iterable of nodes (default None)
            this whole collection is made recovered.
            Currently there is no test for consistency with initial_infecteds.
            Understood that everyone who isn't infected or recovered initially
            is initially susceptible.

        rho : number
            initial fraction infected. number is int(round(G.order()*rho))

        tmin : number (default 0)
            starting time
            
        tmax : number   (default float('Inf'))
            maximum time after which simulation will stop.
            the default of running to infinity is okay for SIR, 
            but not for SIS.

        transmission_weight : string       (default None)
            the label for a weight given to the edges.
            transmission rate is
            G.edge[i][j][transmission_weight]*tau

        recovery_weight : string       (default None)
            a label for a weight given to the nodes to scale their 
            recovery rates
            gamma_i = G.node[i][recovery_weight]*gamma

        return_full_data: boolean (default False)
            Tells whether `node_history` should be returned.  

    Returns : 
        :
        times, S, I, R : Scipy arrays
        
        OR if `return_full_data is True`:
            
        times, S, I, R, node_history 
            node_history is a dict and 
            node_history[node] is a tuple (time_list, status_list)
            the time_list is the times the node's status changes
            the status_list is the status at those times.

    
    :SAMPLE USE:

    ::


        import networkx as nx
        import EoN
        import matplotlib.pyplot as plt
        
        G = nx.configuration_model([1,5,10]*100000)
        initial_size = 10000
        gamma = 1.
        tau = 0.3
        t, S, I, R = EoN.fast_SIR(G, tau, gamma, 
                                    initial_infecteds = range(initial_size))
                                    
        plt.plot(t, I)
    '''
    #tested in test_SIR_dynamics
    if transmission_weight is not None:
        trans_rate_fxn, rec_rate_fxn = EoN._get_rate_functions_(G, tau, gamma, 
                                                    transmission_weight,
                                                    recovery_weight)
        def trans_time_fxn(source, target, trans_rate_fxn):
            rate = trans_rate_fxn(source, target)
            if rate >0:
                return random.expovariate(rate)
            else:
                return float('Inf')
        def rec_time_fxn(node, rec_rate_fxn):
            rate = rec_rate_fxn(node)
            if rate >0:
                return random.expovariate(rate)
            else:
                return float('Inf') 

        trans_time_args = (trans_rate_fxn,)
        rec_time_args = (rec_rate_fxn,)
        return fast_nonMarkov_SIR(G, trans_time_fxn = trans_time_fxn, 
                        rec_time_fxn = rec_time_fxn,
                        trans_time_args = trans_time_args, 
                        rec_time_args = rec_time_args, 
                        initial_infecteds = initial_infecteds, 
                        initial_recovereds = initial_recovereds, 
                        rho=rho, tmin = tmin, tmax = tmax, 
                        return_full_data = return_full_data)
    else:
        #the transmission rate is tau for all edges.  We can use this
        #to speed up the code.
        
        #get rec_rate_fxn (recovery rate may be variable)
        trans_rate_fxn, rec_rate_fxn = EoN._get_rate_functions_(G, tau, gamma, 
                                                    transmission_weight,
                                                    recovery_weight)
        
        return fast_nonMarkov_SIR(G, 
                        trans_and_rec_time_fxn=_trans_and_rec_time_Markovian_const_trans_,
                        trans_and_rec_time_args=(tau, rec_rate_fxn),
                        initial_infecteds = initial_infecteds, 
                        initial_recovereds = initial_recovereds, 
                        rho=rho, tmin = tmin, tmax = tmax, 
                        return_full_data = return_full_data)



def fast_nonMarkov_SIR(G, trans_time_fxn=None,
                        rec_time_fxn=None,
                        trans_and_rec_time_fxn = None,
                        trans_time_args=(),
                        rec_time_args=(),
                        trans_and_rec_time_args = (),
                        initial_infecteds = None,
                        initial_recovereds = None,
                        rho=None, tmin = 0, tmax = float('Inf'), 
                        return_full_data = False, Q=None):
    r'''
    A modification of the algorithm in figure A.3 of Kiss, Miller, & 
    Simon to allow for user-defined rules governing time of 
    transmission.  
    
    Please cite the book if using this algorithm.

    This is useful if the transmission rule is non-Markovian in time, or
    for more elaborate models.  

    Allows the user to define functions (details below) to determine
    the rules of transmission times and recovery times.  There are two ways to do
    this.  The user can define a function that calculates the recovery time
    and another function that calculates the transmission time.  If recovery is after
    transmission, then transmission occurs.
    
    Alternately, the user can define a single function (details below) that would 
    determine both recovery and transmission times.  
    

    Arguments : 

        G : Networkx Graph
        
        trans_time_fxn : a user-defined function that returns the delay until 
                         transmission for an edge.  May depend on various 
                         arguments and need not be Markovian.  Returns float
            Called using the form
            trans_delay = trans_time_fxn(source_node, target_node, *trans_time_args)

        rec_time_fxn : a user-defined function that returns the delay until 
                       recovery for a node.  May depend on various arguments 
                       and need not be Markovian.  Returns float
            Called using the form
            rec_delay = rec_time_fxn(node, *rec_time_args)

    
        trans_and_rec_time_fxn : a user-defined function that returns both 
                                 a dict giving delay until transmissions for 
                                 all edges from source to susceptible 
                                 neighbors and a float giving delay until 
                                 recovery of the source.  
                                 
                                 CAN ONLY BE USED INSTEAD OF trans_time_fxn
                                 AND rec_time_fxn.  ERROR IF THESE ARE ALSO
                                 DEFINED.
            Called using the form 
            trans_delay_dict, rec_delay = trans_and_rec_time_fxn(
                                               node, susceptible_neighbors,
                                               *trans_and_rec_time_args)
            here trans_delay_dict is a dict whose keys are those neighbors
            who receive a transmission and rec_delay is a float.
            
        trans_time_args : tuple
            see trans_time_fxn
        
        rec_time_args : tuple
            see rec_time_fxn

        trans_and_rec_time_args : tuple
            see trans_and_rec_tim_fxn
        
        initial_infecteds: node or iterable of nodes
            if a single node, then this node is initially infected
            if an iterable, then whole set is initially infected
            if None, then choose randomly based on rho.  If rho is also
            None, a random single node is chosen.
            If both initial_infecteds and rho are assigned, then there
            is an error.
            
        initial_recovereds: iterable of nodes (default None)
            this whole collection is made recovered.
            Currently there is no test for consistency with initial_infecteds.
            Understood that everyone who isn't infected or recovered initially
            is initially susceptible.
       
        rho : number
            initial fraction infected. number is int(round(G.order()*rho))

        tmin : number (default 0)
            starting time
            
        tmax : (default infinity)
            final time

        return_full_data: boolean (default False)
            Tells whether `node_history` should be returned.  


    
         If Q is defined:
             Then initial_infecteds consists of those nodes infected 
             **PRIOR** to t=0.  
             Those infected at t=0 will be handled by being in Q 
             already.


        Q : If user wants to predefine some events, this can be done.  This 
            can be input as a heap or as a list (it will be heapified and 
            modified).
        
            User should understand the how events are entered.
        
            Currently there is no guarantee this is properly supported,
            so much so that right now I'm going to force the user to edit 
            the source code before trying it.  
        
            When Q is input, initial_infecteds should be the nodes in 
            I class **PRIOR** to t=0, and the events in Q must have all of 
            their recoveries.  
        
            The best way to handle nodes that should be already recovered is 
            to put them in initial_infecteds and give them a recovery event 
            at t=0.


    Returns : 
        :
        times, S, I, R : Scipy arrays
        
        OR if `return_full_data is True`:
            
        times, S, I, R, node_history 
            node_history is a dict and 
            node_history[node] is a tuple (time_list, status_list)
            the time_list is the times the node's status changes
            the status_list is the status at those times.

    :SAMPLE USE:

                
        import EoN
        
        import networkx as nx
        
        import matplotlib.pyplot as plt
        
        import random
        
        N=1000000
        
        G = nx.fast_gnp_random_graph(N, 5/(N-1.))
        

        
        #set up the code to handle constant transmission rate 
        
        #with fixed recovery time.
        
        def trans_time_fxn(source, target, rate):
        
            return random.expovariate(rate)

        def rec_time_fxn(node,D):
        
            return D
        
        D = 5
        
        tau = 0.3
        
        initial_inf_count = 100
        
        t, S, I, R = EoN.fast_nonMarkov_SIR(G, 
                                trans_time_fxn=trans_time_fxn, 
                                rec_time_fxn=rec_time_fxn,
                                trans_time_args=(tau,), 
                                rec_time_args=(D,),
                                initial_infecteds = range(initial_inf_count))
        
        # note the comma after `rate` and `D`.  This is needed for python
        
        # to recognize these are tuples

        # initial condition has first 100 nodes in G infected.
    
    '''                                 
    if rho and initial_infecteds:
        raise EoN.EoNError("cannot define both initial_infecteds and rho")
    if rho and initial_recovereds:
        raise EoN.EoNError("cannot define both initial_recovereds and rho")

    if (trans_time_fxn and not rec_time_fxn) or (rec_time_fxn and not trans_time_fxn):
        raise EoN.EoNError("must define both trans_time_fxn and rec_time_fxn or neither")
    elif trans_and_rec_time_fxn and trans_time_fxn:
        raise EoN.EoNError("cannot define trans_and_rec_time_fxn at the same time as trans_time_fxn and rec_time_fxn")
    elif not trans_and_rec_time_fxn and not trans_time_fxn:
        raise EoN.EoNError("if not defining trans_and_rec_time_fxn, must define trans_time_fxn and rec_time_fxn")
        
    if not trans_and_rec_time_fxn: #we define the joint function.
        trans_and_rec_time_fxn =  _find_trans_and_rec_delays_SIR_
        trans_and_rec_time_args = (trans_time_fxn, rec_time_fxn, trans_time_args, rec_time_args)
        
    #now we define the initial setup.
    status = defaultdict(lambda: 'S') #node status defaults to 'S'
    rec_time = defaultdict(lambda: tmin-1) #node recovery time defaults to -1
    if initial_recovereds is not None:
        for node in initial_recovereds:
            status[node] = 'R'
            rec_time[node] = tmin-1 #default value for these.  Ensures that the recovered nodes appear with a time
    pred_inf_time = defaultdict(lambda: float('Inf')) 
        #infection time defaults to \infty  --- this could be set to tmax, 
        #probably with a slight improvement to performance.
    
    #now set up Q if it's not already defined.
    #if it is already defined, this is because user is using a feature
    #that I hope to support in future, but don't right now.
    if Q is None:
        Q = myQueue(tmax)

        if initial_infecteds is None:  #create initial infecteds list if not given
            if rho is None:
                initial_number = 1
            else:
                initial_number = int(round(G.order()*rho))
            initial_infecteds=random.sample(G.nodes(), initial_number)
        elif G.has_node(initial_infecteds):
            initial_infecteds=[initial_infecteds]
        #else it is assumed to be a list of nodes.
        
        times, S, I, R= ([tmin], [G.order()], [0], [0])  

        for u in initial_infecteds:
            pred_inf_time[u] = tmin
            Q.add(tmin, _process_trans_SIR_, args=(G, u, times, S, I, R, Q, 
                                                        status, rec_time, 
                                                        pred_inf_time, 
                                                        trans_and_rec_time_fxn,
                                                        trans_and_rec_time_args
                                                    )
                            )
    else:
        raise EoN.EoNError("inputting Q is not currently supported.\n \
                        Email joel.c.miller.research@gmail.com for help.\n \
                        I believe this code will work, but you will need to \
                        delete this message.")

        if initial_infecteds is None:
            if rho is None:
                initial_number = 0 #assuming that input Q has this 
                                   #taken care of 
            else:
                initial_number = scipy.random.binomial(G.order(),rho)
            initial_infecteds=random.sample(G.nodes(), initial_number)
        elif G.has_node(initial_infecteds):
            initial_infecteds=[initial_infecteds]

        for node in initial_infecteds:
            status[node] = 'I'
            pred_inf_time[node] = tmin  #old version made -1, not sure why not 0?
        for event in Q:
            if event.action == 'transmit' and \
                            event.time<pred_inf_time[event.node]:
                pred_inf_time[event.node] = event.time
            elif event.action == 'recover':
                rec_time[event.node] = event.time
        heapq.heapify(Q)            
        times, S, I, R= ([tmin], [G.order()-len(initial_infecteds)], [0], [0])  
        
    
    #Note that when finally infected, pred_inf_time is correct
    #and rec_time is correct.  
    #So if return_full_data is true, these are correct

    while Q:  #all the work is done in this while loop.
        Q.pop_and_run()

    #the initial infections were treated as ordinary infection events at 
    #time 0.
    #So each initial infection added an entry at time 0 to lists.
    #We'd like to get rid these excess events.
    times = times[len(initial_infecteds):]
    S=S[len(initial_infecteds):]
    I=I[len(initial_infecteds):]
    R=R[len(initial_infecteds):]

    if not return_full_data:
        return scipy.array(times), scipy.array(S), scipy.array(I), \
               scipy.array(R) 
    else:
        #strip pred_inf_time and rec_time down to just the values for nodes 
        #that became infected
        #could use iteritems for Python 2, by   try ... except AttributeError
        infection_times = {node:time for (node,time) in 
                            pred_inf_time.items() if status[node]!='S'}
        recovery_times = {node:time for (node,time) in 
                                rec_time.items() if status[node] =='R'}
                                
                
        node_history = _transform_to_node_history_(infection_times, recovery_times, tmin, SIR = True)
        return scipy.array(times), scipy.array(S), scipy.array(I), \
                scipy.array(R), node_history


def _find_trans_and_rec_delays_SIS_(node, neighbors, trans_time_fxn, 
                                    rec_time_fxn,
                                    trans_time_args=(),
                                    rec_time_args=()):
    rec_delay = rec_time_fxn(node, *rec_time_args)
    trans_delays={}
    for target in neighbors:
        trans_delays[target] = trans_time_fxn(node, target, rec_delay, *trans_time_args)
    return trans_delays, rec_delay

def _process_trans_SIS_Markov(time, G, source, target, times, S, I, Q,
                        status, rec_time, infection_times, recovery_times, trans_rate_fxn, rec_rate_fxn):
    r'''From figure A.6 of Kiss, Miller, & Simon.  Please cite the
    book if using this algorithm.

    Does the Markovian version.  So it doesn't take in a list of transmission times
    
    Arguments : 

        time : number
            current time
        G (networkx Graph)
        source : node
            node causing transmission
        target : node
            node receiving transmission.
        times : list
            list of times at which events have happened
        S, I: lists
            lists of numbers of nodes of each status at each time
        Q : myQueue
            the queue of events
        status : dict
            dictionary giving status of each node
        rec_time : dict
            dictionary giving recovery time of each node
        infection_times : 
        
        recovery_times : 
            
        pred_inf_time : dict
            dictionary giving predicted infection time of nodes 
        trans_rate_fxn : function
            transmission rate trans_rate_fxn(u,v) gives transmission rate 
            from u to v
        rec_rate_fxn : function
            recovery rate rec_rate_fxn(u) is recovery rate of u.

    Returns : 
        :
        nothing returned

    MODIFIES
    --------
    status : updates status of target
    rec_time : adds recovery time for target
    times : appends time of event
    infection_times[node] : appends time of infection.
    S : appends new S (reduced by 1 from last)
    I : appends new I (increased by 1)
    Q : adds recovery and transmission events for target.

    '''

    if status[target] == 'S':
        status[target] = 'I'
        I.append(I[-1]+1) #one more infected
        S.append(S[-1]-1) #one less susceptible
        times.append(time)
        rec_time[target] = time + random.expovariate(rec_rate_fxn(target))
        
        if rec_time[target]<Q.tmax:
            Q.add(rec_time[target], _process_rec_SIS_, 
                    args = (target, times, recovery_times, S, I, status))
        for v in G.neighbors(target): #target plays role of source here
            _find_next_trans_SIS_Markov(Q, time, trans_rate_fxn(target, v), target, v, 
                                    status, rec_time,
                                    trans_event_args = (G, target, v, times, 
                                            S, I, Q, status, 
                                            rec_time, infection_times, recovery_times,
                                            trans_rate_fxn,
                                            rec_rate_fxn
                                            )
                                  )
        infection_times[target].append(time)
    if source != 'initial_condition':
        _find_next_trans_SIS_Markov(Q, time, trans_rate_fxn(source, target), 
                                source, target, status, rec_time, 
                                trans_event_args = (G, source, target, times, 
                                            S, I, Q, status, 
                                            rec_time, infection_times, recovery_times,
                                            trans_rate_fxn, 
                                            rec_rate_fxn
                                            )
                             )

def _process_trans_SIS_nonMarkov_(time, G, target, future_transmissions,
                        times, S, I, Q, status, rec_time, 
                        infection_times, recovery_times,
                        trans_and_rec_time_fxn, trans_and_rec_time_args=()):
    r'''From figure A.6 of Kiss, Miller, & Simon.  Please cite the
    book if using this algorithm.

    Does the nonMarkovian version.
    
    Arguments : 

        time : number
            current time
        G (networkx Graph)
        source : node
            node causing transmission
        target : node
            node receiving transmission.
        times : list
            list of times at which events have happened
        S, I: lists
            lists of numbers of nodes of each status at each time
        Q : myQueue
            the queue of events
        status : dict
            dictionary giving status of each node
        rec_time : dict
            dictionary giving recovery time of each node
        infection_times : 
        
        recovery_times : 
            
        trans_and_rec_time_fxn : function

        trans_and_rec_time_args : 

    Returns : 
        :
        nothing returned

    MODIFIES
    --------
    status : updates status of target
    rec_time : adds recovery time for target
    times : appends time of event
    infection_times[node] : appends time of infection.
    S : appends new S (reduced by 1 from last)
    I : appends new I (increased by 1)
    Q : adds recovery and transmission events for target.

    '''

    if status[target] == 'S':
        status[target] = 'I'
        infection_times[target].append(time)

        times.append(time)
        I.append(I[-1]+1) #one more infected
        S.append(S[-1]-1) #one less susceptible

        trans_delays, rec_delay = trans_and_rec_time_fxn(target, G.neighbors(target), *trans_and_rec_time_args)
        rec_time[target] = time + rec_delay
        
        if rec_time[target]<Q.tmax:
            Q.add(rec_time[target], _process_rec_SIS_, 
                    args = (target, times, recovery_times, S, I, status))
        for v in G.neighbors(target): #target plays role of source here
            if trans_delays[v]:
                trans_times = [time + td for td in trans_delays[v]] #when do transmissions happen
                if status[v] == 'I':  #only care about those after current infectious period
                    trans_times = [time for time in trans_times if time>rec_time[v]]
                following_transmissions = trans_times[1:]
                if trans_times: #no point adding any if there are none
                    Q.add(trans_times[0], _process_trans_SIS_nonMarkov_, args = (G, v, following_transmissions, times, S, I, Q, status, 
                                                                                    rec_time, infection_times, recovery_times,
                                                                                    trans_and_rec_time_fxn, trans_and_rec_time_args))
    
    #target is definitely infected now.  It has some future_transmissions stored.  
    #do they happen?
    trans_times = [time for time in future_transmissions if time> rec_time[target]]
    following_transmissions = trans_times[1:]
    if trans_times:
        Q.add(trans_times[0], _process_trans_SIS_nonMarkov_, args = (G, target, following_transmissions, times, S, I, Q, status, 
                                                                                    rec_time, infection_times, recovery_times,
                                                                                    trans_and_rec_time_fxn, trans_and_rec_time_args))
                                                                                    


def _find_next_trans_SIS_Markov(Q, time, tau, source, target, status, rec_time, 
                            trans_event_args=()):
    r'''From figure A.6 of Kiss, Miller, & Simon.  Please cite the
    book if using this algorithm.


    determines if a transmission from source to target will occur and if 
    so puts into Q

    Arguments : 

        Q : myQueue
            A priority queue of events
        t : current time
        tau : transmission rate
        source : infected node that may transmit
        target : the possibly susceptible node that may receive a 
             transmission
        status : a dict giving the current status of every node
        rec_time : a dict giving the recovery time of every node that has 
               been infected.

    Returns : 
        :
        nothing returned

    MODIFIES
    --------
    Q : if a transmission time is potentially valid, add the first 
        event.
        when this transmission occurs later we will consider adding 
        another event.
        note that the event includes the source, so we can later check 
        if same source will transmit again.

    Entry requirement:
    -------
    Only enter this if the source node is INFECTED.

    '''
    
    #assert(status[source]=='I')
    if rec_time[target]<rec_time[source]: 
        #if target is susceptible, then rec_time[target]<time
        delay = random.expovariate(tau)
        #transmission_time = max(time, rec_time[target]) + delay
        transmission_time = time + delay
        if transmission_time<rec_time[target]:
            delay = random.expovariate(tau)
            transmission_time = rec_time[target]+delay
        if transmission_time < rec_time[source] and transmission_time < Q.tmax:
            Q.add(transmission_time, _process_trans_SIS_Markov, 
                                args = trans_event_args
                            )
 
def _process_rec_SIS_(time, node, times, recovery_times, S, I, status):
    r'''From figure A.6 of Kiss, Miller, & Simon.  Please cite the
    book if using this algorithm.

    '''

    times.append(time)
    recovery_times[node].append(time)
    S.append(S[-1]+1)   #one more susceptible
    I.append(I[-1]-1) #one less infected
    status[node] = 'S'

def fast_SIS(G, tau, gamma, initial_infecteds=None, rho = None, tmin=0, tmax=100, 
                transmission_weight = None, recovery_weight = None, 
                return_full_data = False):
    r'''Fast SIS simulations for epidemics on weighted or unweighted
    networks, allowing edge and node weights to scale the transmission
    and recovery rates.  Assumes exponentially distributed times to recovery
    and to transmission.
    
    From figure A.5 of Kiss, Miller, & Simon.  Please cite the
    book if using this algorithm.

    Arguments : 
    
        G (networkx Graph)
            The underlying network

        tau (positive float)
            transmission rate per edge

        gamma : number
            recovery rate per node

        initial_infecteds: node or iterable of nodes
            if a single node, then this node is initially infected
            if an iterable, then whole set is initially infected
            if None, then choose randomly based on rho.  If rho is also
            None, a random single node is chosen.
            If both initial_infecteds and rho are assigned, then there
            is an error.
       
        rho : number
            initial fraction infected. number infected is int(round(G.order()*rho))
       
        tmin : number (default 0)
            starting time
            
        tmax : number
            stop time

        transmission_weight : string       (default None)
            the label for a weight given to the edges.
            transmission rate is
            G.edge[i][j][transmission_weight]*tau

        recovery_weight : string       (default None)
            a label for a weight given to the nodes to scale their 
            recovery rates
            `gamma_i = G.node[i][recovery_weight]*gamma`
    
        return_full_data: boolean (default False)
            Tells whether `node_history` should be returned.  

    Returns : 
        :
        times, S, I : each a scipy array
            giving times and number in each status for corresponding time
        
        or if return_full_data=True:
            
        times, S, I, node_history
            node_history is a dict and 
            node_history[node] is a tuple (time_list, status_list)
            the time_list is the times the node's status changes
            the status_list is the status at those times.
    
    :SAMPLE USE:

    ::


        import networkx as nx
        import EoN
        import matplotlib.pyplot as plt
        
        G = nx.configuration_model([1,5,10]*100000)
        initial_size = 10000
        gamma = 1.
        tau = 0.2
        t, S, I = EoN.fast_SIS(G, tau, gamma, tmax = 10,
                                    initial_infecteds = range(initial_size))
                                    
        plt.plot(t, I)
            
    '''
    if rho is not None and initial_infecteds is not None:
        raise EoN.EoNError("cannot define both initial_infecteds and rho")
    
    trans_rate_fxn, rec_rate_fxn = EoN._get_rate_functions_(G, tau, gamma, 
                                                transmission_weight,
                                                recovery_weight)

    if initial_infecteds is None:
        if rho is None:
            initial_number = 1
        else:
            initial_number = int(round(G.order()*rho))
        initial_infecteds=random.sample(G.nodes(), initial_number)
    elif G.has_node(initial_infecteds):
        initial_infecteds=[initial_infecteds]

    times = [tmin]
    S = [G.order()]
    I = [0]
    Q = myQueue(tmax)
    status = defaultdict(lambda: 'S') #node status defaults to 'S'
    rec_time = defaultdict(lambda: tmin-1) #node recovery time defaults to -1
    rec_time['initial_condition'] = tmin

    infection_times = defaultdict(lambda: []) #defaults to empty list
    recovery_times = defaultdict(lambda: [])
    for u in initial_infecteds:
        Q.add(tmin, _process_trans_SIS_Markov, 
                            args = (G, 'initial_condition', u, times, 
                                    S, I, Q, status, rec_time, infection_times, recovery_times, 
                                    trans_rate_fxn, rec_rate_fxn)
                        )
    while Q:
        Q.pop_and_run()

    #the initial infections were treated as ordinary infection events at 
    #time 0.
    #So each initial infection added an entry at time tmin to lists.
    #We'd like to get rid these excess events.
    times = times[len(initial_infecteds):]
    S=S[len(initial_infecteds):]
    I=I[len(initial_infecteds):]

    if not return_full_data:
        return scipy.array(times), scipy.array(S), scipy.array(I)
    else:
        node_history = _transform_to_node_history_(infection_times, recovery_times, tmin, SIR = False)
        return scipy.array(times), scipy.array(S), scipy.array(I), \
                node_history


def fast_nonMarkov_SIS(G, trans_time_fxn=None, rec_time_fxn=None, 
                        trans_and_rec_time_fxn = None, trans_time_args=(),
                        rec_time_args = (), trans_and_rec_time_args=(),
                        initial_infecteds = None, rho = None, tmin=0, tmax = 100,
                        return_full_data = False):
                        
    r'''Similar to fast_nonMarkov_SIR. 
    
    WARNING: trans_time_fxn (or trans_and_rec_time_fxn) need to return lists of
    times.  Not just the next time. So this is different from the SIR version.

    Arguments : 
    
        G (networkx Graph)
            The underlying network

        trans_time_fxn : **RETURNS A LIST**
                         **has slightly different arguments than the SIR version**

                        a user-defined function that returns list of delays until 
                        transmission for an edge.  All delays are before recovery.
                        May depend on various 
                        arguments and need not be Markovian.                          
            Called using the form
            trans_delay = trans_time_fxn(source_node, target_node, rec_delay, *trans_time_args)

        rec_time_fxn : a user-defined function that returns the delay until 
                       recovery for a node.  May depend on various arguments 
                       and need not be Markovian.  Returns float
            Called using the form
            rec_delay = rec_time_fxn(node, *rec_time_args)

    
        trans_and_rec_time_fxn : a user-defined function that returns both 
                                 a dict whose values are lists of delays until 
                                 transmissions for all edges from source to  
                                 neighbors and a float giving delay until 
                                 recovery of the source.  
                                 
                                 CAN ONLY BE USED INSTEAD OF trans_time_fxn
                                 AND rec_time_fxn.  ERROR IF THESE ARE ALSO
                                 DEFINED.
            Called using the form 
            trans_delay_dict, rec_delay = trans_and_rec_time_fxn(
                                               node, susceptible_neighbors,
                                               *trans_and_rec_time_args)
            here trans_delay_dict is a dict whose keys are those neighbors
            who receive a transmission and rec_delay is a float.
        trans_time_args : tuple
            see trans_time_fxn
        
        rec_time_args : tuple
            see rec_time_fxn

        trans_and_rec_time_args : tuple
            see trans_and_rec_tim_fxn
        
        initial_infecteds: node or iterable of nodes
            if a single node, then this node is initially infected
            if an iterable, then whole set is initially infected
            if None, then choose randomly based on rho.  If rho is also
            None, a random single node is chosen.
            If both initial_infecteds and rho are assigned, then there
            is an error.
       
        rho : number
            initial fraction infected. number is int(round(G.order()*rho))
       
        tmin : number (default 0)
            starting time
            
        tmax : number (default 100)
            stop time

        return_full_data: boolean (default False)
            Tells whether `node_history` should be returned.  

    Returns : 
        :
        times, S, I : each a scipy array
            giving times and number in each status for corresponding time
        
        or if return_full_data=True:
            
        times, S, I, node_history
            node_history is a dict and 
            node_history[node] is a tuple (time_list, status_list)
            the time_list is the times the node's status changes
            the status_list is the status at those times.
    
    :SAMPLE USE:

    ::

                        
    '''
    if rho  and initial_infecteds:
        raise EoN.EoNError("cannot define both initial_infecteds and rho")
    
    if (trans_time_fxn and not rec_time_fxn) or (rec_time_fxn and not trans_time_fxn):
        raise EoN.EoNError("must define both trans_time_fxn and rec_time_fxn or neither")
    if trans_and_rec_time_fxn and trans_time_fxn:
        raise EoN.EoNError("cannot define trans_and_rec_time_fxn at the same time as either trans_time_fxn or rec_time_fxn")
    elif not trans_and_rec_time_fxn and not trans_time_fxn:
        raise EoN.EoNError("if not defining trans_and_rec_time_fxn, must define trans_time_fxn and rec_time_fxn")
    
    if not trans_and_rec_time_fxn: #we define the joint function.
        trans_and_rec_time_fxn =  _find_trans_and_rec_delays_SIS_
        trans_and_rec_time_args = (trans_time_fxn, rec_time_fxn, trans_time_args, rec_time_args)    
                
                
    if initial_infecteds is None:  #create initial infecteds list if not given
        if rho is None:
            initial_number = 1
        else:
            initial_number = int(round(G.order()*rho))
        initial_infecteds=random.sample(G.nodes(), initial_number)
    elif G.has_node(initial_infecteds):
        initial_infecteds=[initial_infecteds]
        
    times, S, I = ([tmin], [G.order()], [0])  

    Q = myQueue(tmax)
    status = defaultdict(lambda: 'S') #node status defaults to 'S'
    rec_time = defaultdict(lambda: tmin-1) #node recovery time defaults to -1

    infection_times = defaultdict(lambda: []) #defaults to empty list
    recovery_times = defaultdict(lambda: [])

    for u in initial_infecteds:
        Q.add(tmin, _process_trans_SIS_nonMarkov_, args=(G, 
                                                        u, [], times, S, I, Q, 
                                                        status, rec_time, 
                                                        infection_times, recovery_times,
                                                        trans_and_rec_time_fxn,
                                                        trans_and_rec_time_args
                                                    )
                )
                    
    while Q:  #all the work is done in this while loop.
        Q.pop_and_run()

    #the initial infections were treated as ordinary infection events at 
    #time 0.
    #So each initial infection added an entry at time tmin to lists.
    #We'd like to get rid these excess events.
    times = times[len(initial_infecteds):]
    S=S[len(initial_infecteds):]
    I=I[len(initial_infecteds):]

    if not return_full_data:
        return scipy.array(times), scipy.array(S), scipy.array(I)
    else:
        node_history = _transform_to_node_history_(infection_times, recovery_times, tmin, SIR = False)
        return scipy.array(times), scipy.array(S), scipy.array(I), \
                node_history



#####Now dealing with Gillespie code#####

def _Gillespie_initialize_SIR_(G, initial_infecteds, initial_recovereds, 
                            infection_times, recovery_times, tmin, 
                            return_full_data):
    '''Initializes the network'''
    times = [tmin]
    S = [G.order()-len(initial_infecteds)]
    I = [len(initial_infecteds)]
    R = [0]
    status = defaultdict(lambda:'S') #by default all are susceptible
    infected = list(initial_infecteds)
    infected_neighbor_count = defaultdict(lambda:0)#
    risk_group = defaultdict(lambda:_ListDict_()) 
    for node in initial_infecteds:
        status[node]='I'
    if initial_recovereds is not None:
        for node in initial_recovereds:
            status[node] = 'R'
            if return_full_data:
                recovery_times[node] = [tmin-1]
    for node in initial_infecteds:
        for neighbor in G.neighbors(node):
            if status[neighbor]=='S':
                infected_neighbor_count[neighbor] += 1
                if infected_neighbor_count[neighbor]>1:
                    risk_group[infected_neighbor_count[neighbor]-1].remove(
                                                                    neighbor)
                risk_group[infected_neighbor_count[neighbor]].add(neighbor)

    if return_full_data:
        for node in initial_infecteds:
            infection_times[node] = [tmin]

    return times, S, I, R, status, infected, infected_neighbor_count, risk_group

        
def _Gillespie_initialize_SIS_(G, initial_infecteds, infection_times, 
                                recovery_times, tmin, return_full_data):
    '''Initializes the network'''
    times = [tmin]
    S = [G.order()-len(initial_infecteds)]
    I = [len(initial_infecteds)]
    status = defaultdict(lambda:'S') #by default all are susceptible
    infected = list(initial_infecteds)
    infected_neighbor_count = defaultdict(lambda:0)#
    risk_group = defaultdict(lambda:_ListDict_()) 
    for node in initial_infecteds:
        status[node]='I'
    for node in initial_infecteds:
        for neighbor in G.neighbors(node):
            if status[neighbor]=='S':
                infected_neighbor_count[neighbor] += 1
                if infected_neighbor_count[neighbor]>1:
                    risk_group[infected_neighbor_count[neighbor]-1].remove(
                                                                    neighbor)
                risk_group[infected_neighbor_count[neighbor]].add(neighbor)

    if return_full_data:
        for node in initial_infecteds:
            infection_times[node] = [tmin]
    return times, S, I, status, infected, infected_neighbor_count, risk_group
    
def _Gillespie_infect_(G, S, I, R, times, infected, current_time, 
                        infected_neighbor_count, risk_group, status, 
                        infection_times, return_full_data, SIR=True):
    '''
    Chooses the node to infect.

    First chooses which risk_group the node is in.  
    Then choose the node.

    An alternative which may be cleaner (not sure about faster?) is to 
    create a data type which tracks maximum risk.  
    
    Then choose a random node and do a rejection sampling step.  
    
    If rejected, then select again.  
    
    May need a very careful rejection sampling to account for repeated 
    selection.  
    
    We could probably define this as a method of the class.
    '''
    r = random.random()*sum(n*len(risk_group[n]) for n in risk_group.keys())
    for n in risk_group.keys():
        r-= n*len(risk_group[n])
        if r<0:
            break
    #we've got n now

    recipient = risk_group[n].choose_random()
        #OLD VERSION choose random element from dict
        #based on http://stackoverflow.com/a/24949742/2966723
        #question by http://stackoverflow.com/users/2237265/jamyn
        #answer by http://stackoverflow.com/users/642757/scott-ritchie
        #
        #CURRENT VERSION
        #http://stackoverflow.com/a/15993515/2966723
    assert(status[recipient]=='S')
    risk_group[n].remove(recipient)
    infected.append(recipient)
    infection_times[recipient].append(current_time)
    status[recipient]='I'
    S.append(S[-1]-1)
    I.append(I[-1]+1)
    times.append(current_time)
    if SIR:
        R.append(R[-1])

    for neighbor in G.neighbors(recipient):
        if status[neighbor]=='S':
            if infected_neighbor_count[neighbor]>0:
                risk_group[infected_neighbor_count[neighbor]].remove(neighbor)
            infected_neighbor_count[neighbor]+=1
            risk_group[infected_neighbor_count[neighbor]].add(neighbor)
    
            

def _Gillespie_recover_SIR_(G, S, I, R, times, infected, current_time, status,
                            infected_neighbor_count, risk_group, 
                            recovery_times, return_full_data):
    r''' Changes: S, I, R, infected, times'''
    #assert(I[-1]==len(infected))
    index = random.randint(0,I[-1]-1)
    infected[index], infected[-1] = infected[-1], infected[index] 
            #http://stackoverflow.com/a/14088129/2966723
    recovering_node = infected.pop()

    I.append(I[-1]-1)
    status[recovering_node]='R'
    S.append(S[-1])
    R.append(R[-1]+1)
    times.append(current_time)
    for neighbor in G.neighbors(recovering_node):
        if status[neighbor] == 'S': #neighbor susceptible, 
                                    #its risk just got smaller
            risk_group[infected_neighbor_count[neighbor]].remove(neighbor)
            infected_neighbor_count[neighbor] -= 1
            if infected_neighbor_count[neighbor]>0:
                risk_group[infected_neighbor_count[neighbor]].add(neighbor)            
    if return_full_data:
        recovery_times[recovering_node].append(current_time)

def _Gillespie_recover_SIS_(G, S, I, times, infected, current_time, status, 
                            infected_neighbor_count, risk_group, 
                            recovery_times, return_full_data):
    r''' From figure A.5 of Kiss, Miller, & Simon.  Please cite the
    book if using this algorithm.
    
    Arguments : 
    '''
    assert(I[-1]==len(infected))
    index = random.randint(0,I[-1]-1)
    infected[index], infected[-1] = infected[-1], infected[index] 
                    #http://stackoverflow.com/a/14088129/2966723
    recovering_node = infected.pop()

    I.append(I[-1]-1)
    status[recovering_node]='S'
    S.append(S[-1]+1)
    times.append(current_time)
    infected_neighbor_count[recovering_node] = 0
    for neighbor in G.neighbors(recovering_node):
        if neighbor == recovering_node:
            continue  #Deals with selfloops
                      #there is probably a good way to count the 
                      #number of infected neighbors
        if status[neighbor] == 'I':
            infected_neighbor_count[recovering_node] += 1
        else: #neighbor susceptible, its risk just got smaller
            risk_group[infected_neighbor_count[neighbor]].remove(neighbor)
            infected_neighbor_count[neighbor] -= 1
            if infected_neighbor_count[neighbor]>0:
                risk_group[infected_neighbor_count[neighbor]].add(neighbor)
    if infected_neighbor_count[recovering_node]>0:
        risk_group[infected_neighbor_count[recovering_node]].add(
                                                            recovering_node)
    if return_full_data:
        recovery_times[recovering_node].append(current_time)

def Gillespie_SIR(G, tau, gamma, initial_infecteds=None, 
                    initial_recovereds = None,rho = None, tmin = 0, 
                    tmax=float('Inf'), return_full_data = False):
    #tested in test_SIR_dynamics
    r'''
    Performs SIR simulations for epidemics on unweighted
    networks.  The run time is slower than fast_SIR, but they are comparable.
    However, Gillespie_SIR relies on the lack of edge/node weights in order
    to achieve a significant improvement.  fast_SIR permits weighted networks.
    
    Adapted from figure A.1 of Kiss, Miller, & Simon.  Please cite the
    book if using this algorithm.

    A significant improvement compared to the obvious implementation of
    figure A.1 is that at_risk_nodes is actually broken into multiple risk 
    groups based on what the nodes' risks are.  This allows for a more 
    efficient step to correctly choose a random node to be infected.  
    
    To handle weighted networks would prevent us from having the risk 
    groups.  If we modify the code to have weights, then we cannot (easily) 
    put nodes into separate risk groups.  This will dramatically slow down 
    the calculation because it becomes much harder to choose which node 
    becomes infected.
            
    This code will not work for nonMarkovian transmission.  There is ongoing
    work by Boguna and collaborators on how to do Gillespie algorithms for
    nonMarkovian transmission.
    
    :SEE ALSO:

    fast_SIR which has the same inputs but uses a different method to 
    run faster (and can handle weighted networks).
    
    
    
    Arguments : 
        G (networkx Graph)
            The underlying network
        tau (positive float)
            transmission rate per edge
       
        gamma : number
            recovery rate per node
       
        initial_infecteds: node or iterable of nodes
            if a single node, then this node is initially infected
            if an iterable, then whole set is initially infected
            if None, then choose randomly based on rho.  If rho is also
            None, a random single node is chosen.
            If both initial_infecteds and rho are assigned, then there
            is an error.
       
        initial_recovereds: iterable of nodes (default None)
            this whole collection is made recovered.
            Currently there is no test for consistency with initial_infecteds.
            Understood that everyone who isn't infected or recovered initially
            is initially susceptible.

        rho : number
            initial fraction infected. number is int(round(G.order()*rho))

        tmin : number (default 0)
            starting time
            
        tmax : number
            stop time

        return_full_data: boolean (default False)
            Tells whether `node_history` should be returned.  

    Returns : 
        :
        times, S, I, R : each a scipy array
            giving times and number in each status for corresponding time

            OR if return_full_data=True:
        times, S, I, R, node_history
            node_history is a dict and 
            node_history[node] is a tuple (time_list, status_list)
            the time_list is the times the node's status changes
            the status_list is the status at those times.

    :SAMPLE USE:


    ::

        import networkx as nx
        import EoN
        import matplotlib.pyplot as plt
        
        G = nx.configuration_model([1,5,10]*100000)
        initial_size = 10000
        gamma = 1.
        tau = 0.3
        t, S, I, R = EoN.fast_SIR(G, tau, gamma, 
                                    initial_infecteds = range(initial_size))
                                    
        plt.plot(t, I)
    
    '''

    if rho is not None and initial_infecteds is not None:
        raise EoN.EoNError("cannot define both initial_infecteds and rho")

    
    infection_times = defaultdict(lambda: []) #defaults to an empty list for each node
    recovery_times = defaultdict(lambda: [])

    tau = float(tau)  #just to avoid integer division problems in python 2.
    gamma = float(gamma)
    
    if initial_infecteds is None:
        if rho is None:
            initial_number = 1
        else:
            initial_number = int(round(G.order()*rho))
        initial_infecteds=random.sample(G.nodes(), initial_number)
    elif G.has_node(initial_infecteds):
        initial_infecteds=[initial_infecteds]


    times, S, I, R, status, infected, infected_neighbor_count, risk_group = \
                    _Gillespie_initialize_SIR_(G, initial_infecteds, 
                                            initial_recovereds,
                                            infection_times, recovery_times, 
                                            tmin, return_full_data)

    total_trans_rate = tau*sum(n*len(risk_group[n]) 
                                    for n in risk_group.keys())
    total_rec_rate = gamma*len(infected)
    total_rate = total_rec_rate + total_trans_rate
    next_time = times[-1] + random.expovariate(total_rate)
    while next_time<tmax and infected:
        r = random.random()*total_rate
        if r<total_rec_rate:
            #a recovery occurs
            _Gillespie_recover_SIR_(G, S, I, R, times, infected, next_time, 
                                    status, infected_neighbor_count, 
                                    risk_group, recovery_times, 
                                    return_full_data)
            total_rec_rate = gamma*I[-1]
        else:
            #an infection occurs
            _Gillespie_infect_(G, S, I, R, times, infected, next_time, 
                                infected_neighbor_count, risk_group, status, 
                                infection_times, return_full_data, SIR=True)
        total_trans_rate = tau*sum(n*len(risk_group[n]) 
                                    for n in risk_group.keys())
        total_rate = total_rec_rate + total_trans_rate
        if total_rate >0:  
            next_time += random.expovariate(total_rate)

    if not return_full_data:
        return scipy.array(times), scipy.array(S), scipy.array(I), \
                scipy.array(R)
    else:
        #need to change data type of infection_times and recovery_times
        infection_time = {node: L[0] for node, L in infection_times.items()}
        recovery_time = {node: L[0] for node, L in recovery_times.items()}

        node_history = _transform_to_node_history_(infection_times, recovery_times, tmin, SIR = True)
        return scipy.array(times), scipy.array(S), scipy.array(I), \
                scipy.array(R), node_history



def Gillespie_SIS(G, tau, gamma, initial_infecteds=None, rho = None, tmin = 0,
                    tmax=100, return_full_data = False):
    r'''
    Performs SIS simulations for epidemics on unweighted
    networks.  The run time is slower than fast_SIS, but they are comparable.
    However, Gillespie_SIS relies on the lack of edge/node weights in order
    to achieve a significant improvement.  fast_SIS permits weighted networks.
    
    Based on figure A.1 of Kiss, Miller, & Simon.  Please cite the
    book if using this algorithm.
    
    See comments in Gillespie_SIR for limitations in weighted and nonMarkovian
    models.
    
    :WARNING: 
        
    self-edges will cause this to die.  
    You can remove self-edges by G.remove_edges_from(G.selfloop_edges())

    At present, this does not accept recovery or transmission weights.
    This is because including that will force us to sum up these weights
    more frequently rather than just counting how many exist
    which will slow the code down.  For weights, try fast_SIS
    
    :SEE ALSO:

    fast_SIS which has the same inputs but uses a much faster method.
    
    
    Arguments : 
        
        G (NetworkX Graph)
            The underlying network
        tau (positive float) 
            transmission rate per edge
        gamma : number
            recovery rate per node

        initial_infecteds: node or iterable of nodes
            if a single node, then this node is initially infected
            if an iterable, then whole set is initially infected
            if None, then choose randomly based on rho.  If rho is also
            None, a random single node is chosen.
            If both initial_infecteds and rho are assigned, then there
            is an error.
       
        rho : number
            initial fraction infected. number is int(round(G.order()*rho))

        tmin : number (default 0)
            starting time
            
        tmax : number
            stop time

        return_full_data: boolean (default False)
            Tells whether `node_history` should be returned.  
        
    Returns : 

        : times, S, I; 
            each a scipy array giving times and number in each status for corresponding time

        or if `return_full_data==True`
        
        times, S, I, node_history
            node_history is a dict and 
            node_history[node] is a tuple (time_list, status_list)
            the time_list is the times the node's status changes
            the status_list is the status at those times.

    :SAMPLE USE:

    ::

        import networkx as nx
        import EoN
        import matplotlib.pyplot as plt
        
        G = nx.configuration_model([1,5,10]*100000)
        initial_size = 10000
        gamma = 1.
        tau = 0.2
        t, S, I = EoN.Gillespie_SIS(G, tau, gamma, tmax = 20,
                                    initial_infecteds = range(initial_size))
                                
        plt.plot(t, I)

    '''
    if rho is not None and initial_infecteds is not None:
        raise EoN.EoNError("cannot define both initial_infecteds and rho")

    infection_times = defaultdict(lambda: []) #defaults to an empty list 
    recovery_times = defaultdict(lambda: [])  #for each node

    tau = float(tau)  #just to avoid integer division problems.
    gamma = float(gamma)
    
    if initial_infecteds is None:
        if rho is None:
            initial_number = 1
        else:
            initial_number = int(round(G.order()*rho))
        initial_infecteds=random.sample(G.nodes(), initial_number)
    elif G.has_node(initial_infecteds):
        initial_infecteds=[initial_infecteds]
        
    times, S, I, status, infected, infected_neighbor_count, risk_group = \
                _Gillespie_initialize_SIS_(G, initial_infecteds, infection_times,  
                                        recovery_times, tmin, return_full_data)
    #note that at this point times, S, and I must all be lists 
    
    #since we will be appending to them

    total_trans_rate = tau*sum(n*len(risk_group[n]) 
                                    for n in risk_group.keys())
    total_rec_rate = gamma*len(infected)
    total_rate = total_rec_rate + total_trans_rate
    next_time = times[-1] + random.expovariate(total_rate)
    while next_time<tmax and infected:
        r = random.random()*total_rate
        if r<total_rec_rate:
            #a recovery occurs
            _Gillespie_recover_SIS_(G, S, I, times, infected, next_time, 
                                    status, infected_neighbor_count, 
                                    risk_group, recovery_times, 
                                    return_full_data)
            total_rec_rate = gamma*I[-1]
        else:
            #an infection occurs
            _Gillespie_infect_(G, S, I, [], times, infected, next_time, 
                                infected_neighbor_count, risk_group, status, 
                                infection_times, return_full_data, SIR=False)
            #updates variables as needed and calculates new max_trans_rate
        total_trans_rate = tau*sum(n*len(risk_group[n]) 
                                    for n in risk_group.keys())
        total_rate = total_rec_rate + total_trans_rate
        if total_rate>0:
            next_time += random.expovariate(total_rate)
        else:  #occurs if everyone recovered
            next_time = float('Inf')
        #        print next_time, I[-1]

    if not return_full_data:
        return scipy.array(times), scipy.array(S), scipy.array(I)
    else:
        node_history = _transform_to_node_history_(infection_times, recovery_times, tmin, SIR = False)
        return scipy.array(times), scipy.array(S), scipy.array(I), \
                node_history



"""
What follows is code from a first step towards a weighted Gillespie algorithm
This is a low priority since it will be much slower than fast_SIR.


def randomlychoose(D, r):
    r'''
    Arguments : 
        D : defaultdict of rates
        r : number 
        
    Returns : 
        node : the node for which the action is occuring
    
    Modifies:
        D : removes node.
    '''
    for node in D:
        if r< D[node]:
            break
        else:
            r-= D[node]
    D.pop(node)
    return node
    
def update_infected_neighbors(G, node, status, infection_rate, total_infection_rate)
    for neighbor in G.neighbors(node):
        if status[neighbor]=='S':
            infection_rate[neighbor] = infection_rate[neighbor]+trans_rate_fxn(node, neighbor)
            total_infection_rate += trans_rate_fxn(node, neighbor)
    return total_infection_rate
    
def Gillespie_weighted_SIR(G, tau, gamma, initial_infecteds = None, rho = None,
                tmax=float('Inf'), transmission_weight = None, 
                recovery_weight = None, return_full_data = False):
    
    if rho is not None and initial_infecteds is not None:
        raise EoN.EoNError("cannot define both initial_infecteds and rho")

    
    infection_times = defaultdict(lambda: []) #defaults to an empty list for each node
    recovery_times = defaultdict(lambda: [])

    tau = float(tau)  #just to avoid integer division problems in python 2.
    gamma = float(gamma)

    trans_rate_fxn, rec_rate_fxn = EoN._get_rate_functions_(G, tau, gamma, 
                                                transmission_weight,
                                                recovery_weight)
    
    if initial_infecteds is None:
        if rho is None:
            initial_number = 1
        else:
            initial_number = int(round(G.order()*rho))
        initial_infecteds=random.sample(G.nodes(), initial_number)
    elif G.has_node(initial_infecteds):
        initial_infecteds=[initial_infecteds]
    
    times = [0]
    S = [G.order()-len(initial_infecteds)]
    I = [len(initial_infecteds)]
    R = [0]
    status = defaultdict(lambda:'S') #by default all are susceptible
    infection_rate = defaultdict(lambda: 0)
    recovery_rate = defaultdict(lambda: 0)
    infecteds = list(initial_infecteds)
    
    total_infection_rate=0
    total_recovery_rate = 0
    
    for node in infecteds:
        status[node]='I'
        recovery_rate[node] = rec_rate_fxn(node)
        total_recovery_rate += recovery_rate[node]
    for node in infecteds:
        total_infection_rate = update_infected_neighbors(G, node, status, 
                                    infection_rate, total_infection_rate)
                            
    total_rate = total_infection_rate + total_recovery_rate
    
    time = random.expovariate(total_rate)
    
    while time<tmax and infecteds:
        r = total_rate*random.random()
        if r< total_recovery_rate: #a recovery happens
            u = randomlychoose(infection_rate, r)
            for nbr in G.neighbors(u):
                localtau = trans_rate_fxn(node, neighbor)
                total_infection_rate[nbr] -= localtau
                total_infection_rate -= localtau
        else: # a transmission happens.
            r -= total_recovery_rate
            u = randomlychoose(recovery_rate, r)
        
"""
