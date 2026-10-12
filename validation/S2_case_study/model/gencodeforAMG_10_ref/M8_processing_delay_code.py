def generate_data():
    import scipy
    args=(0.9914684636934573,)
    num_datapoints=100
    M8_processing_delay=scipy.stats.gamma(*args,loc=0.22326768265861127,scale=0.9728867690312852)
    while True:
        value = float(M8_processing_delay.rvs())
        if value >= 0:
            yield value
