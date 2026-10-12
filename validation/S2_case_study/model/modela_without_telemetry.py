"""Run the 10-machine Model A without telemetry, using its existing RVGs."""

import os
import runpy
from pathlib import Path


model_dir = Path(__file__).resolve().parent

# Force telemetry off even if the caller has MODELA_TELEMETRY enabled.
os.environ["MODELA_TELEMETRY"] = "0"

# Use the same M1-M10 processing-delay generators as the telemetry version.
os.environ["DELAY_PLUGIN_DIR"] = str(model_dir / "gencodeforAMG_10")

runpy.run_path(
    str(model_dir / "modelA_10_machine_series.py"),
    run_name="__main__",
)
