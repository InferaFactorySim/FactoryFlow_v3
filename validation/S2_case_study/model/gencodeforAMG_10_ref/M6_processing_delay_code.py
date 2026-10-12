def generate_data():
    import scipy
    loc=0.1256985797502718
    lamda=1.005155176762821
    num_datapoints=100
    M6_processing_delay=scipy.stats.expon(loc=loc,scale=lamda)
    while True:
        value = float(M6_processing_delay.rvs())
        if value >= 0:
            yield value