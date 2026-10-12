def generate_data():
    import scipy
    args=(3.4964221602509475,)
    num_datapoints=100
    M5_processing_delay=scipy.stats.weibull_min(*args,loc=1.8366833760573544,scale=3.4972108631744985)
    while True:
        value = float(M5_processing_delay.rvs())
        if value >= 0:
            yield value
