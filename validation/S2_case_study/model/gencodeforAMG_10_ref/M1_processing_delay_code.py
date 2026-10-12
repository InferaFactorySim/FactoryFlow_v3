def generate_data():
    import scipy
    lowerlimit=3.80048545704513
    upperlimit=0.9986409592579308
    num_datapoints=100
    M1_processing_delay=scipy.stats.uniform(loc=lowerlimit,scale=upperlimit)
    while True:
        value = float(M1_processing_delay.rvs())
        if value >= 0:
            yield value