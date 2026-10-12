# S3 case study — a large system

Material behind Section 6.2.3, *Case Study: A Large System*: a 100-machine plant
of ten parallel lines, run through three phases with unannounced changes. The
paper calls this system **S3**. It was S4 in earlier drafts. Files have been renamed to `S3`/`s3`; the
environment variables (`S4_SEED`, `S4_SIM_TIME`, …), the telemetry system name, and
`reference_system/run_phaseC_repeats.py`, which drives the original container copy,
keep the `S4` names.

| Phase | Interval | Change |
|---|---|---|
| A | 0 – 4,000 | none; baseline |
| B | 4,000 – 8,000 | `M04`: U(1,3) → U(3,5); `M00`: N(4, 0.1) → lognormal, mean 4; `M06`: U(2,3) → N(5, 0.4) |
| C | from 150 | phase B delays from the start; the source stops feeding line 00 at time 150 |

## Where each result in the paper comes from

| Paper | Files | Script that recomputes it |
|---|---|---|
| Table 9 (phases) | `reference_system/S3_GT.py`, `S3_GT_phaseB.py`, `S3_GT_phaseC.py` | — |
| Phase A: generated twin has the reference's 102 nodes and 110 edges | `descriptions/description_compact.md` (input), `twins/dt0_dt1_model.py` (output) | — |
| Table 10 (reference KPIs) | `results/s3_twin_kpis_phaseAB.json` (A, B), `results/s3_twin_kpis_phaseC.json` (C) | `results/make_tables_10_12.py` |
| Table 11 (parameter recovery) | `drift_monitoring/phaseA/rvg/`, `drift_monitoring/phaseB/refitted_baselines/rvg/` | `results/table11_parameter_recovery.py` (`A`, `B`) |
| Phase B change detection (30 changed machines flagged, one false KS alert) | `drift_monitoring/phaseB/parameter_drift_results.json` | `drift_monitoring/reproduce_parameter_monitoring.py` |
| Table 12 (Digital Twin KPIs: DT0, DT1, DT2, stale twins) | `results/s3_twin_kpis_phaseAB.json`, `results/s3_twin_kpis_phaseC.json` | `results/make_tables_10_12.py` |
| Long-run phase A comparison (50,000 units) | `results/s3_long_run_phaseA.json` | `results/s3_long_run_phaseA.py` |
| Phase C structural detection (22/22, latency 104–112) | `structural_monitoring/phaseC_repeats/summary.json` and one monitor log per run | `structural_monitoring/replay_phaseC_offline.py` |

## Folders

- **`reference_system/`** — the plant (ground truth). `S3_GT.py` defines the model,
  distributions, power ratings and change times; `S3_GT_phaseB.py` and
  `S3_GT_phaseC.py` are the phase runs of the live setup.
  `run_s3_experiment.py` holds the KPI code (`snapshot`, `window_kpis`) used for
  the plant and every twin. `run_phaseC_repeats.py` is the live phase C
  experiment (see below). `S3_README.md` is the detailed system description.
- **`descriptions/`** — the inputs to the FactoryFlow generator:
  `description_compact.md` (DT0/DT1), `description_phaseC_unfed.md` (DT2: line 00
  kept but unfed), and the longer and alternative variants.
- **`twins/`** — the FactoryFlow-generated models: `dt0_dt1_model.py` (ten fed
  lines; DT0 with the phase A plugins, DT1 with the phase B plugins) and
  `dt2_model.py` (regenerated after the structural change).
- **`drift_monitoring/`** — the parameter monitor's state: monitoring
  configurations (500-reading windows, SD-shift threshold 25%), the fitted
  baselines with the readings they were fitted from, the phase B drift results
  with the windows they examined, and the delay plugins (`rvg/`) the twins load.
  `phaseB/refitted_baselines/` holds the 30 phase B baselines accepted for `M00`,
  `M04` and `M06` and the plugin set used by DT1, DT2 and the phase C stale twin.
- **`structural_monitoring/`** — `drift_detection.py` (the structural monitor as
  deployed), its settings (removal window 60, routing threshold 0.15,
  reconciliation every 1 time unit), the S3 reference graph (220 transitions,
  10 path types), and the phase C run logs.
- **`results/`** — the published twin comparisons (JSON), the live-run time
  series of the reference, and the comparison scripts. `s3_paths.py` holds the
  paths and twin seeding shared by the scripts.
- **`lib/`** — the parameter monitor (`parameter_drift/core.py`) and the DataFITR
  fitting engine (`IM/`) it uses.
- **`src/factorysimpy/`** — the FactorySimPy version used for the paper.

## Reproducing

Requires Python 3.9 or later and the packages in `requirements.txt`. Everything
below runs offline from this folder: no Docker, broker, database or LLM.

```
pip install -r requirements.txt

# Tables 10 and 12 as published, and Table 11
cd results
python make_tables_10_12.py
python table11_parameter_recovery.py A
python table11_parameter_recovery.py B

# Twin and reference runs behind Tables 10 and 12 (written to results/rerun/)
python s3_twin_kpis.py           # reference A and B, DT0, DT1
python s3_stale_twin.py          # stale twin in phase B (needs the previous step)
python s3_twin_kpis_phaseC.py    # reference C, DT2, stale twin in phase C
python make_tables_10_12.py rerun
python s3_long_run_phaseA.py     # 50,000-unit comparison

# Parameter fits and phase B change detection, from the stored readings
cd ../drift_monitoring
python reproduce_parameter_monitoring.py

# Phase C structural detection
cd ../structural_monitoring
python replay_phaseC_offline.py
```

**What is exact.** The reference plant is seeded (`seed_base` = run index), so the
reference columns of Tables 10 and 12 are reproduced exactly. Table 11 is
recomputed from the stored plugins and reproduces every row. The parameter
monitor's fits and checks are recomputed from the readings stored with them:
every fitted family and every phase B drift decision is reproduced, and fitted
parameters agree to within 1e-7 relative (the optimiser's last digits vary
between numpy/scipy builds). The offline phase C replay reproduces the ten live
runs exactly (transitions detected, detection time, start-up and false alerts).

**What varies.** The delay plugins draw from unseeded generators, so the twin runs
behind the published Table 12 were not seeded. The scripts now seed every twin
run (`TWIN_SEED_BASE` in `results/s3_paths.py`) so that reruns are repeatable;
their twin columns will differ from the published ones by run-to-run variation.
These runs need several GB of memory with four worker processes; lower the
`Pool(4)` in the scripts on a smaller machine.

**Live setup.** In the experiment the plant published telemetry through RabbitMQ
to InfluxDB, and the parameter and structural monitors ran as services of the
FactoryFlow Docker stack; `reference_system/run_phaseC_repeats.py` drove the live
phase C runs in that stack (one time unit per second). The offline scripts above
replace the broker and database with the readings and events those services
received. `run_phaseC_repeats.py --score-log <log>` rescoring a stored log needs
no stack.
