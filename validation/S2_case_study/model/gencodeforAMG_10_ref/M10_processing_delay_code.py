def generate_data():
    import scipy
    lowerlimit=4.30010015726672
    upperlimit=0.3996091861147253
    num_datapoints=100
    M10_processing_delay=scipy.stats.uniform(loc=lowerlimit,scale=upperlimit)
    while True:
        value = float(M10_processing_delay.rvs())
        if value >= 0:
            yield value