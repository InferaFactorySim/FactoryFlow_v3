"""Reproduce the S3 parameter monitoring offline, from the stored readings.

During the experiment the parameter monitor read machine processing delays from
InfluxDB. Every baseline stores the readings it was fitted from
(``reference_values``) and every drift check stores the window it examined
(``current_values``), so the fits and checks can be recomputed here with the
monitor's own code (``lib/parameter_drift/core.py``) and no database.

The script recomputes
  1. the 100 phase A baselines (the DT0 delay plugins) from their readings,
     comparing the selected family, its parameters and the generated plugin
     with the stored ones (parameters to a relative tolerance of 1e-6, since
     the fitting optimiser can differ in the last digits between builds);
  2. the 100 phase B drift checks (phase A baseline readings against the stored
     phase B window), comparing the decision and the criteria that tripped;
  3. the 30 phase B baselines accepted for M00, M04 and M06 (the DT1 plugins).

``phaseB/parameter_baselines.json`` is identical to the phase A file: baselines
are not replaced until a refit is accepted. ``phaseA/parameter_drift_results.json``
is not recomputed: those checks ran a few minutes before the final phase A
baselines were fitted, against an earlier baseline that was then replaced, and
they are not used in the paper.

Usage, from this folder:
    python reproduce_parameter_monitoring.py
"""
import json
import os
import re
import sys
import tempfile
import types
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))

# core.py imports the InfluxDB client and the stored database credentials at
# module level; neither is used offline.
try:
    import influxdb_client  # noqa: F401
except ImportError:
    sys.modules["influxdb_client"] = types.SimpleNamespace(InfluxDBClient=None)
sys.modules["shared.data_sources"] = types.SimpleNamespace(active_influx_config=lambda: {})

os.environ["PARAM_DRIFT_DATA_DIR"] = tempfile.mkdtemp(prefix="s3_param_")
from parameter_drift import core  # noqa: E402

CONFIGS = "phaseA/monitoring_configurations.json"   # identical in phaseB/
BASELINES_A = "phaseA/parameter_baselines.json"
PLUGINS_A = "phaseA/rvg"
DRIFT_B = "phaseB/parameter_drift_results.json"
BASELINES_B = "phaseB/refitted_baselines/phase_B_baselines_M00_M04_M06.json"
PLUGINS_B = "phaseB/refitted_baselines/rvg"


def load(rel):
    with open(HERE / rel, encoding="utf-8") as fh:
        return json.load(fh)


# Fitting runs a numerical optimiser, so parameters can differ in the last digits
# between numpy/scipy builds; values are compared to this relative tolerance.
RTOL = 1e-6
NUMBER = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")


def plugin_body(text):
    """Plugin text without the comment header (it carries creation times and ids)."""
    return "\n".join(line for line in text.replace("\r", "").splitlines()
                     if not line.startswith("#")).strip()


def same_plugin_code(a, b):
    """Plugin texts equal apart from numbers that agree to RTOL."""
    a, b = plugin_body(a), plugin_body(b)
    if NUMBER.split(a) != NUMBER.split(b):
        return False
    x = np.array(NUMBER.findall(a), dtype=float)
    y = np.array(NUMBER.findall(b), dtype=float)
    return x.shape == y.shape and np.allclose(x, y, rtol=RTOL, atol=1e-12)


class StoredWindow:
    """Stands in for the InfluxDB reader: returns the window stored with the check."""

    def __init__(self, values):
        self._values = np.asarray(values, dtype=float)

    def values(self, config, window, limit=None):
        return self._values


def check_baselines(configs, baselines, rvg_dir, label):
    """Refit each baseline from its readings and compare with the stored fit."""
    plugin_dir = Path(os.environ["PARAM_DRIFT_DATA_DIR"]) / "rvg"
    family = params = plugin = 0
    notes = []
    for config in configs:
        stored = baselines[config["id"]]
        fit = core.fit_distribution(np.asarray(stored["reference_values"]), config)
        same_family = fit["best_distribution"] == stored["best_distribution"]
        new, old = np.asarray(fit["parameters"]), np.asarray(stored["parameters"])
        same_params = same_family and new.shape == old.shape and np.allclose(new, old, rtol=RTOL, atol=1e-12)
        written = Path(core.write_rvg_file(config, {**stored, **fit}))
        original = HERE / rvg_dir / Path(stored["rvg_file"]).name
        same_plugin = same_plugin_code(written.read_text(), original.read_text())
        for leftover in plugin_dir.glob("*"):
            leftover.unlink()
        family += same_family
        params += same_params
        plugin += same_plugin
        if not same_params:
            rel = (np.max(np.abs(new - old) / np.maximum(np.abs(old), 1e-12))
                   if same_family and new.shape == old.shape else float("nan"))
            notes.append(f"    {config['entity_id']}: {stored['best_distribution']} -> {fit['best_distribution']}, "
                         f"largest relative parameter difference {rel:.1e}")
        elif not same_plugin:
            notes.append(f"    {config['entity_id']}: parameters equal, plugin text differs")
    n = len(configs)
    print(f"{label}: family {family}/{n}, parameters {params}/{n}, delay plugin {plugin}/{n} reproduced")
    print("\n".join(notes) if notes else "", end="\n" if notes else "")
    return family == n


def check_drift(configs, baselines, results, label):
    """Rerun each drift check on the stored windows and compare the decision."""
    decision = criteria = 0
    flagged = []
    for config in configs:
        stored = results[config["id"]]
        rerun = core.check_drift(StoredWindow(stored["current_values"]), config, baselines[config["id"]])
        decision += rerun["drift"] == stored["drift"]
        criteria += rerun["reasons"] == stored["reasons"]
        if rerun["drift"]:
            flagged.append(f"    {config['entity_id']} ({', '.join(rerun['reasons'])})")
    n = len(configs)
    print(f"{label}: drift decision {decision}/{n}, criteria {criteria}/{n} reproduced; "
          f"{len(flagged)} machines flagged")
    print("\n".join(sorted(flagged, key=core.natural_key)))
    return decision == criteria == n


def main():
    configs = load(CONFIGS)
    by_entity = {c["entity_id"]: c for c in configs}
    baselines_a = load(BASELINES_A)
    ok = check_baselines(configs, baselines_a, PLUGINS_A, "Phase A baselines (DT0 plugins)")
    ok &= check_drift(configs, baselines_a, load(DRIFT_B), "Phase B drift checks")
    refitted = load(BASELINES_B)
    ok &= check_baselines([by_entity[e] for e in refitted],
                          {by_entity[e]["id"]: b for e, b in refitted.items()},
                          PLUGINS_B, "Phase B baselines for M00, M04, M06 (DT1 plugins)")
    print("\nevery family and drift decision reproduced" if ok else "\nSOME RESULTS DIFFER")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
