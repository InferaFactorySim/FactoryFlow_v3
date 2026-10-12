def generate_data():
    import scipy
    lowerlimit=4.501202775401904
    upperlimit=0.9985053115744904
    num_datapoints=100
    M7_processing_delay=scipy.stats.uniform(loc=lowerlimit,scale=upperlimit)
    while True:
        value = float(M7_processing_delay.rvs())
        if value >= 0:
            yield value