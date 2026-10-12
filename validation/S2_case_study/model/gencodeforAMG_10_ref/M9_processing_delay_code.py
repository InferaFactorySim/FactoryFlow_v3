def generate_data():
    import scipy
    s=0.031035335861174324
    mean=-16.075907444126255
    variance=20.055917672732214
    num_datapoints=100
    M9_processing_delay=scipy.stats.lognorm(s=s,loc=mean,scale=variance)
    while True:
        value = float(M9_processing_delay.rvs())
        if value >= 0:
            yield value