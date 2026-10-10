# Description
#
# S4 phase C as a fresh run: the S4 ground truth (S4_GT.py) started empty at
# t = 0 with the phase B processing delays already in force, and the routing
# change at t = 150, when the source stops feeding line 00 and round-robins
# over the other nine lines. Run in real time (one time unit per second, about
# 69 minutes), with telemetry, to 4,150, so phase C lasts 4,000 time units like
# phases A and B and the structural monitor observes it as a live plant.
#
# 150 leaves the Digital Twin time to observe every path before the change:
# starting empty, all 220 transitions (10 lines x 22) had appeared in completed
# items by t = 58.8 - 70.6 over 20 seeds, and by t = 150 each line has
# completed about 12 items.
#
# The model and distributions are imported from S4_GT.py; only the two timing
# constants it reads when building are changed here, before the model is built.

import os

import simpy  # noqa: F401  (the real-system UI lists files that use simpy)

import S4_GT
from S4_GT import build_model, main  # noqa: F401

PHASE_C_ROUTE_CHANGE = 150.0
PHASE_C_LENGTH = 4000.0

# Phase B delays from the start: make_delay switches when env.now >= 0.
S4_GT.T_PARAM_CHANGE = 0.0
S4_GT.T_ROUTE_CHANGE = PHASE_C_ROUTE_CHANGE
S4_GT.T_END = PHASE_C_ROUTE_CHANGE + PHASE_C_LENGTH

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


if __name__ == "__main__":
    os.environ.setdefault("S4_SIM_TIME", str(S4_GT.T_END))
    # Real time, paced against the wall clock; S4_REALTIME_FACTOR scales it.
    os.environ["S4_REALTIME"] = "1"
    os.environ.setdefault("S4_REALTIME_FACTOR", "1")
    os.environ.setdefault("S4_SAMPLE_PATH", os.path.join(RESULTS, "s4_phaseC_timeseries.csv"))
    main()
