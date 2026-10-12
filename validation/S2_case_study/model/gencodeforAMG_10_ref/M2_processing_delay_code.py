def generate_data():
    import scipy
    lowerlimit=4.1000809649999415
    upperlimit=1.0996661688868166
    num_datapoints=100
    M2_processing_delay=scipy.stats.uniform(loc=lowerlimit,scale=upperlimit)
    while True:
        value = float(M2_processing_delay.rvs())
        if value >= 0:
            yield value