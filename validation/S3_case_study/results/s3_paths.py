"""Locations used by the S3 comparison scripts, and the seeding of twin runs.

All paths are relative to this case-study folder, so the scripts run from any
checkout. FactorySimPy (``src/``) and the reference system
(``reference_system/``) are put on the Python path on import.
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REFERENCE = os.path.join(ROOT, "reference_system")
TWIN_AB = os.path.join(ROOT, "twins", "dt0_dt1_model.py")   # DT0, DT1, and the stale twins
TWIN_C = os.path.join(ROOT, "twins", "dt2_model.py")        # DT2, regenerated after phase C
PLUGINS_A = os.path.join(ROOT, "drift_monitoring", "phaseA", "rvg")
PLUGINS_B = os.path.join(ROOT, "drift_monitoring", "phaseB", "refitted_baselines", "rvg")
RESULTS = os.path.join(ROOT, "results")
RERUN = os.path.join(RESULTS, "rerun")   # reruns write here, never over the published files

for _path in (os.path.join(ROOT, "src"), REFERENCE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

_default_rng = np.random.default_rng


def seed_twin(seed):
    """Make a twin run reproducible.

    The delay plugins draw from ``np.random.default_rng(None)``, which is seeded
    from the operating system. After this call, every generator created without a
    seed takes the next child of ``SeedSequence(seed)`` instead; the plugins are
    loaded in a fixed order, so each machine receives the same stream on every
    rerun.
    """
    sequence = np.random.SeedSequence(seed)

    def default_rng(seed=None):
        return _default_rng(sequence.spawn(1)[0] if seed is None else seed)

    np.random.default_rng = default_rng

# Twin runs are seeded with TWIN_SEED_BASE[kind] + run index; the reference uses
# seed_base = run index, as in S3_GT.
TWIN_SEED_BASE = {"dt0": 10_000, "dt1": 20_000, "stale_B": 30_000, "dt2": 40_000, "stale_C": 50_000}
