"""Run the 10-machine Model A with RabbitMQ telemetry enabled."""

import os
import runpy
from pathlib import Path


os.environ["MODELA_TELEMETRY"] = "1"
runpy.run_path(
    str(Path(__file__).with_name("modelA_10_machine_series.py")),
    run_name="__main__",
)
