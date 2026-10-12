def generate_data():
    import scipy
    mean=4.016241514465214
    variance=0.9780884260371938
    num_datapoints=100
    M4_processing_delay=scipy.stats.norm(loc=mean,scale=variance)
    while True:
        value = float(M4_processing_delay.rvs())
        if value >= 0:
            yield value