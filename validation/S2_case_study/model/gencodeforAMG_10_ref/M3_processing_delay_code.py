def generate_data():
    import scipy
    loc=0.2867566076504672
    lamda=1.0054263875675065
    num_datapoints=100
    M3_processing_delay=scipy.stats.expon(loc=loc,scale=lamda)
    while True:
        value = float(M3_processing_delay.rvs())
        if value >= 0:
            yield value