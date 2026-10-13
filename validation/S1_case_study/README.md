# S1 case study — structural-change detection

Material behind Section 6.2.1, *Structural-Change Detection*, and **Table 7**:
detecting added, removed and rerouted material-flow paths from completed-item
evidence on a small multi-branch line, over fifteen cases with twenty trials
each (seeds 0–19).

The paper calls this system **S1**. The working files call it "Model A" or "the
6-machine line" (for example `model_a_6_machine_structural_drift_results.csv`).
It is unrelated to the folder `real_system_simpy/modelA`, which holds S2.

## The system

`experiments/harness/reference_model.py` — one source, six machines (`M0`–`M5`),
one sink and eight buffers:

```
src -> M0 -> M1 --(70%)-> M2 -> M3 -> M5 -> sink
                \-(30%)-> M4 --------> M5
          M3 --(15% QC)-> M4
held inactive at baseline: M2 -> M6 -> M3 (seventh machine), M0 -> M5 (bypass)
independent inactive line: src_new -> B_new1 -> M_new -> B_new2 -> sink_new
```

A change is made by switching the output-edge selection of the branching
machines over these pre-wired edges, at a random time in [50, 100]. The Digital
Twin's reference graph contains only the baseline structure. Monitor settings
(removal window 60, routing threshold 0.15, at least 10 departures before a
routing fraction is judged) are in `results/structural_monitor_settings.csv`.

## Where each result in the paper comes from

| Paper (Table 7) | Experiment script | Per-trial results |
|---|---|---|
| E0 No change | `experiments/eval_no_change.py` | `results/raw_trials/eval_no_change.csv` |
| E1 Addition (3 cases) | `experiments/eval_addition.py` | `results/raw_trials/eval_addition.csv` |
| E2 Removal (3 cases; "3% route" is `remove_low_freq`) | `experiments/eval_removal.py` | `results/raw_trials/eval_removal.csv` |
| E3 Routing (3 cases) | `experiments/eval_routing.py` | `results/raw_trials/eval_routing.csv` |
| E4 Combined (2 cases) | `experiments/eval_combined.py` | `results/raw_trials/eval_combined.csv` |
| E5 Independent path | `experiments/eval_independent_path.py` | `results/raw_trials/eval_independent_path.csv` |
| E6 Recovery (2 cases; in the text, not Table 7) | `experiments/eval_recovery.py` | `results/raw_trials/eval_recovery.csv` |

The paper and this folder number the cases differently from the development suite,
which had E5 recovery, E6 a detection-sensitivity sweep (`tests/eval_sensitivity.py`)
and E7 independent path. The sensitivity sweep is not reported in the paper and is not
included here; the independent-path case is E5 and the recovery cases are E6.

`results/summarise_table7.py` recomputes every row of Table 7 from the per-trial
files; its detected counts match the published table exactly. The script also prints
precision, detection latency and resolution for each case. The paper reports only the
detected counts in Table 7 and describes latency in the text. Precision is not reported
because `harness/metrics.py` counts each false alert per log entry (a routing alert is
logged once per outgoing branch and again each time it re-activates) while true alerts
are counted per condition, so the printed precision understates the per-condition value.
`results/table8_case_summary.csv` describes each case (initial system, change made,
schedule, expected target conditions, outcome); it covers E0–E4 and E6, and E5 is in the
per-trial file only.

The per-trial files are the runs of 5 August 2026 (E0: 30 July). Earlier runs
of 26 July were not used in the paper and are not included.

## Folders

- **`experiments/`** — one script per experiment group, plus
  `RUNNING_EXPERIMENTS.md`, the original guide to the suite (inputs, ground
  truth, metrics and how far each number can be trusted).
- **`experiments/harness/`** — the S1 reference model and the shared evaluation
  code: `reference_model.py`, `perturbations.py` (the changes), `detector_io.py`
  (the structural monitor interface), `metrics.py` (precision, recall, latency),
  `eval_common.py`, `mock_sender.py` (captures telemetry in memory).
- **`structural_drift_detection/`** — `drift_detection.py`, the structural drift
  monitor as deployed in FactoryFlow. The experiments feed it events directly,
  without RabbitMQ.
- **`src/factorysimpy/`** — the FactorySimPy version used for the paper.
- **`results/`** — `raw_trials/` (the published per-trial results), the case
  summary, the monitor settings and `summarise_table7.py`.

## Reproducing

Requires Python 3.9 or later and the packages in `requirements.txt`. Nothing
outside this folder is needed: no broker, database or LLM.

```
pip install -r requirements.txt
python experiments/eval_no_change.py          # E0, about 20 s
python experiments/eval_addition.py           # E1
python experiments/eval_removal.py            # E2
python experiments/eval_routing.py            # E3
python experiments/eval_combined.py           # E4
python experiments/eval_independent_path.py   # E5
python experiments/eval_recovery.py           # E6
python results/summarise_table7.py rerun      # Table 7 from the rerun
python results/summarise_table7.py            # Table 7 from the published trials
```

Each script writes its per-trial CSV to `results/` (next to `raw_trials/`, which
it does not touch). Every trial is seeded (seeds 0–19), so a rerun is exact: run
from a clean copy of this folder on 12 October 2026, all seven files were
byte-identical to those in `raw_trials/`, and both summaries printed the same
Table 7.
