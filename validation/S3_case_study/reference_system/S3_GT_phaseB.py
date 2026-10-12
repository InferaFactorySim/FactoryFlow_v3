# Description
#
# S4 phases A and B: the S4 ground truth (S3_GT.py) run from 0 to 8,000 as fast
# as possible, with telemetry. The three processing-delay distributions change
# at T_PARAM_CHANGE = 4,000. The run stops at T_ROUTE_CHANGE = 8,000:
# env.run(until=8000) executes no event at 8,000, so the routing change never
# fires and every line, including line 00, is fed throughout.
#
# With the fixed seed, 0 - 4,000 replays the phase A run draw for draw, so the
# phase A baselines fitted from S3_GT.py remain valid references.
#
# The model, distributions and timings are imported from S3_GT.py, so the two
# files cannot diverge. This file only fixes the run length and pacing.

import os

import simpy  # noqa: F401  (the real-system UI lists files that use simpy)

from S3_GT import T_PARAM_CHANGE, T_ROUTE_CHANGE, build_model, main  # noqa: F401

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


if __name__ == "__main__":
    # Forced rather than defaulted: this file exists to run exactly A + B.
    os.environ["S4_SIM_TIME"] = str(T_ROUTE_CHANGE)
    os.environ["S4_REALTIME"] = "0"
    os.environ.setdefault("S4_SAMPLE_PATH", os.path.join(RESULTS, "s3_phaseAB_timeseries.csv"))
    main()
