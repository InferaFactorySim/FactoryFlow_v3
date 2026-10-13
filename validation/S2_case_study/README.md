# S2 case study — parameter assimilation and KPI agreement

Material behind Section 6.2.2, *Parameter Assimilation and KPI Agreement*, whose
KPI comparison is given in the text: processing-time distributions fitted by DataFITR are given to a
structurally identical FactorySimPy Digital Twin, and the twin's KPIs are compared
with those of the reference system over 1,000 replications of 10,000 time units
each.

The paper calls this system **S2**; the scripts and result files call it
"Model A" (`modelA_*`). It is unrelated to the system called Model A in the S1
folder.

## The system

`model/modelA_10_machine_series.py` — ten machines (`M1`–`M10`) in series between
one source and one sink. Each machine's processing delay is loaded as a plugin
from a folder of delay generators:

- `model/gencodeforAMG_10_ref/` — the generating distributions of the reference
  system (withheld from FactoryFlow);
- `model/gencodeforAMG_10/` — the delay plugins fitted by DataFITR, with the
  fitted family, parameters, mean, SD and goodness-of-fit score of each machine in
  `dist_baselines.json`.

The fitted family equals the generating family for nine of the ten machines; `M8`
is gamma in the reference and was fitted as Weibull. The paper says only that one
stage was fitted with a different family with similar effective timing.

## Where each result in the paper comes from

| Paper | File |
|---|---|
| Reference throughput and mean cycle time (0.1979, 73.36) | `results/modelA_1000_runs_summary.csv` (per run: `modelA_1000_runs_kpis.csv`) |
| DT throughput and mean cycle time (0.1981, 73.73) | `results/modelA_1000_runs_summary_matched.csv` (per run: `modelA_1000_runs_kpis_matched.csv`) |
| Relative differences (+0.11%, +0.51%) | `results/modelA_paired_precision_summary.csv`, rows with `n = 1000` (per run pair: `modelA_1000_runs_paired_differences.csv`) |
| Nine of ten families recovered | `model/gencodeforAMG_10/dist_baselines.json` against `model/gencodeforAMG_10_ref/` |

Run *i* of the reference and run *i* of the twin use the same seed
(1000 + *i* − 1). `modelA_paired_precision_summary.csv` also records the smallest
number of runs at which the confidence interval of the difference met the 2% and
5% precision criteria (21 for both KPIs).

## Folders

- **`model/`** — the line, its two sets of delay plugins, and the scripts:
  `run_modela_2000_times.py` (repeated runs, KPI export),
  `analyze_paired_kpis.py` (differences between matching runs and their
  confidence intervals), `modela_without_telemetry.py` and `modela_telemetry.py`
  (a single run of the line, the latter publishing telemetry to RabbitMQ as in
  the live setup).
- **`src/factorysimpy/`** — the FactorySimPy version used for the paper.
- **`results/`** — the published per-run KPIs, summaries and comparison.

## Reproducing

Requires Python 3.9 or later and the packages in `requirements.txt`. Nothing
outside this folder is needed. From `model/`:

```
pip install -r ../requirements.txt
# reference system: generating distributions
python run_modela_2000_times.py --runs 1000 --sim-time 10000 --plugin-dir gencodeforAMG_10_ref --out-dir ../rerun
# Digital Twin: DataFITR-fitted plugins
python run_modela_2000_times.py --runs 1000 --sim-time 10000 --plugin-dir gencodeforAMG_10 --out-dir ../rerun --output-suffix _matched
# comparison
python analyze_paired_kpis.py --reference ../rerun/modelA_1000_runs_kpis.csv --matched ../rerun/modelA_1000_runs_kpis_matched.csv --differences-output ../rerun/modelA_1000_runs_paired_differences.csv --summary-output ../rerun/modelA_paired_precision_summary.csv
```

Every run is seeded, so the rerun is exact. Checked from a clean copy of this
folder on 12 October 2026: the 1,000 reference runs were identical to
`results/modelA_1000_runs_kpis.csv` (the summary differs only in the 17th
significant digit of one standard deviation, from the Python version), and the
first 20 twin runs were identical to the first 20 rows of
`results/modelA_1000_runs_kpis_matched.csv`. Each set of 1,000 runs takes about
an hour with eight worker processes (`--workers`); `--runs 20` gives a quick
check.

The fitting step itself is not rerun here. DataFITR fitted the plugins in
`gencodeforAMG_10/` on 25 July 2026 from about 7,000 telemetry readings per
machine, queried from the time-series database of the live setup; those readings
were not archived, so the fitted plugins are provided as the artefacts the
comparison evaluates.
