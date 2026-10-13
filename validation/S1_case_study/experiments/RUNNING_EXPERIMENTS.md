# Running the Structural Drift Detection Experiments

A practical guide to the evaluation suite: what each experiment does, the model
and inputs it uses, why it exists, what output to expect, and how much you can
trust a number.

> **Note for this folder.** This is the guide to the full suite as it was written
> in the development tree, where the scripts lived in `tests/` and the harness in
> `tests/harness/`; here they are in `experiments/` and `experiments/harness/`.
> Only the experiments reported in the paper (E0–E6) are included in this
> folder. Here and in the paper the independent-path case is E5 (E7 in the
> development suite) and the recovery cases are E6 (E5 in the development suite);
> the sensitivity sweep (E6 in the development suite), the continuous, robustness and end-to-end KPI experiments described below are not. See `../README.md` for how to run them.

---

## How to run

```bash
# one experiment at a time (writes results/<name>.csv and prints a summary)
python tests/eval_addition.py
python tests/eval_removal.py
python tests/eval_routing.py
python tests/eval_combined.py
python tests/eval_recovery.py
python tests/eval_sensitivity.py     # slow (~2-3 min: 200+ sims)
python tests/eval_continuous.py
python tests/eval_independent_path.py  # complete path addition and withdrawal, 20 trials
python tests/eval_robustness.py      # Level 2 (implementation) - pending
python tests/eval_end_to_end_kpi.py  # stale-vs-updated DT KPI validation
python tests/eval_datafitr_kpi_10000.py  # DataFITR fit -> DT, 10,000 KPI trials
```

- No Docker needed. Everything runs **in-process**: the reference SimPy model is
  built, its telemetry is captured in memory, a Digital Twin graph is built from a
  baseline run, and the **deployed detector** (`structural_drift_detection/
  drift_detection.py`) is fed the sink-event stream. The one end-to-end run through
  the real RabbitMQ pipeline is `tests/e2e_validation.py` (pending).
- The factorysimpy library prints heavy trace output; `run_capture` silences it, so
  console output is just the per-condition summary lines.
- Each experiment writes exactly one CSV to `results/`.

### The shared model (used by every experiment)

`tests/harness/reference_model.py` — a **6-machine multi-branch line**:

```
src -> M0 -> M1 --(70%)-> M2 -> M3 -> M5 -> sink
                \-(30%)-> M4 --------> M5
          M3 --(15% QC)-> M4
dormant (absent from the DT): M2->M6->M3 (add-machine), M0->M5 (add-bypass)
independent dormant line: src_new -> B_new1 -> M_new -> B_new2 -> sink_new
```

Fixed inputs (defaults in `reference_model.py` / `eval_common.py`):

| Input | Value | Meaning |
|---|---|---|
| inter-arrival | 1.0 | source emits ~1 item / time unit |
| processing_delay | 0.5 | per machine |
| buffer capacity | 5 | per edge |
| baseline M1 split | 70/30 | main vs secondary branch |
| baseline QC rate | 15% | M3 -> M4 low-traffic edge |
| `removal_window` | 60 | silence before a DT edge is called removed |
| `routing_threshold` (theta) | 0.15 | routing-shift alert threshold |
| `min_routing_samples` | 10 | samples before routing is judged |
| trials/condition | 20 | randomized `t_change` in [50,100], seeded |
| trial duration | 300 (500 for the sensitivity sweep's structural axis) | time units |

Structural change is induced **only** by changing `out_edge_selection` on the branch
machines (the topology is static) — so "add", "remove", and "routing change" are all
expressed as flow redirected over pre-wired edges. Ground truth per condition is
derived automatically by diffing a steady modified run against the DT graph.

---

## E1 — Edge Addition  (`eval_addition.py`)

- **Model / inputs:** reference model. Cases: `add_machine` (route 50% of M2 via the
  dormant M6), `add_bypass` (30% of M0 via the dormant M0->M5, main path kept),
  `temporary_addition` (enable the new machine over [t1, t1+80], then restore).
- **Why:** additions are the detector's strongest case — an unknown edge is flagged
  on first traversal. `temporary_addition` also exercises the resolve path.
- **Expected output:** recall ≈ 1.0; precision ≈ 0.95–1.0; addition latency ~3–4 u
  mean, ~10–15 u worst (≈ one item's transit time); temporary case resolves ~60–80 u
  after restore. CSV: `results/eval_addition.csv`.
- **One run enough?** No. Latency is the transit time of the *first* item to take the
  new edge, which is random; a single run is one draw. 20 trials estimate the mean.
- **Confidence:** latency std is small (~3 u), so 20 trials give a tight 95% CI
  (mean ± ~1.3 u). Recall 20/20 → Wilson 95% CI ≈ [0.84, 1.0]; report the CI, or use
  N=40 if you need a lower bound above 0.9.

## E2 — Edge Removal  (`eval_removal.py`)

- **Model / inputs:** reference model. Cases: `remove_machine` (isolate M4: cut both
  its inflows), `remove_branch` (30% M1->M4 path dies), `remove_low_freq` (remove the
  QC edge, run against a **low-QC 3% baseline** so it is genuinely rare).
- **Why:** removals are silence-based, so latency is dominated by `removal_window` and
  the edge's traffic; the low-frequency case probes where that breaks down.
- **Expected output:** `remove_machine`/`remove_branch` recall ≈ 1.0, removal latency
  ~48–60 u (≈ the window). `remove_low_freq` recall ~0.7 with FN ~0.3 — a low-traffic
  edge is hard to distinguish from an idle one under a fixed 60-u window (motivates a
  per-edge window). Removing a branch also legitimately raises a routing alert on the
  surviving edge (its share jumps to 100%). CSV column `removal_latency` isolates the
  removal.
- **One run enough?** No — the low-frequency case especially is a coin-flip per run.
- **Confidence:** removal latency has low variance (window-dominated) → tight CI at
  N=20. The 0.7 recall of `remove_low_freq` has a wide 95% CI (~[0.48, 0.86]); to
  claim a rate here to ±0.1 you need N ≈ 80.

## E3 — Routing Policy Change  (`eval_routing.py`)

- **Model / inputs:** reference model, topology fixed. Cases: `moderate` (70/30→50/50),
  `large` (70/30→20/80), `gradual` (70/30→40/60 linearly over 120 u).
- **Why:** confirm probability shifts are caught by the routing path and **not**
  misreported as structural add/remove.
- **Expected output:** `large` recall ≈ 1.0 but precision low (~0.5) because the shift
  starves the other branch, producing genuine structural false alarms there;
  `moderate`/`gradual` recall 0.7–0.9 with **large latency (~120–170 u)**. The routing
  monitor uses a lifetime-cumulative estimate (no window), so small shifts move slowly
  past theta — a key, reportable weakness. CSV: `results/eval_routing.csv`.
- **One run enough?** Definitely not — this is the highest-variance experiment.
- **Confidence:** latency variance is large (worst ~210 u vs mean ~120 u), so the
  95% CI at N=20 is wide (roughly mean ± 25 u); recall near 0.7 also has a wide CI.
  Use N=40–50 for stable routing numbers, and always report the latency distribution
  (median + worst), not just the mean.

## E4 — Combined Structural Drift  (`eval_combined.py`)

- **Model / inputs:** reference model. Cases: `add+routing` (new machine + M1 70/30→
  50/50), `add+remove` (new machine + QC edge removed), both at the same `t_change`.
- **Why:** check that concurrent changes are raised as **distinct, correctly-typed**
  conditions rather than conflated.
- **Expected output:** each change detected independently; `add+remove` recall ≈ 1.0,
  precision ≈ 1.0; `add+routing` recall ≈ 0.96 (routing is the slow/uncertain part).
  CSV has per-type `add_latency`, `removal_latency`, `routing_detected`.
- **One run enough?** No — inherits the variance of its component changes (esp. routing).
- **Confidence:** addition part is tight; routing/removal parts carry E3/E2 variance.
  Report per-type, since a single "combined recall" hides which change was missed.

## E6 — Recovery & Alert Lifecycle  (`eval_recovery.py`)

(E5 in the development suite.)

- **Model / inputs:** reference model. Cases: `permanent_recovery` (remove the branch
  over [t1, t1+120], then restore) and `intermittent_recovery` (toggle the branch every
  100 u for 4 cycles).
- **Why:** evaluate the resolve half of the lifecycle and quantify flapping.
- **Expected output:** permanent: resolves 20/20, resolution latency ~3 u (edge
  reappears → immediate), stability = 1 active + 1 resolved (no flapping). Intermittent:
  multiple active/resolved transitions per edge = flapping (there is no cooldown); note
  that toggles shorter than `removal_window` are smoothed out and not detected. CSV:
  `n_active`, `n_resolved`, `resolution_latency`, `residual_fp`.
- **One run enough?** No, but recovery is low-variance, so it converges quickly.
- **Confidence:** resolution latency has small variance → tight CI at N=20. Flapping
  counts are near-deterministic given the toggle schedule; N=20 is ample.

## Detection Sensitivity (E6 in the development suite; not included)  (`eval_sensitivity.py`)  *(slow)*

- **Model / inputs:** reference model. Routing axis: M1 shift ∈ {0.05,0.10,0.15,0.20,
  0.50}. Structural axis: new edge carrying ∈ {1,5,10,20,50}% of M2 flow (500-u runs).
- **Why:** answer "how much change is required before it is reliably detected" — the
  single most useful axis for comparing algorithms.
- **Expected output:** a detection-rate-vs-magnitude curve. Routing detection rate
  climbs sharply around theta ≈ 0.15–0.20; structural detection reaches ~1.0 by a 5%
  flow fraction (1% ≈ 0.85, because a rarely-used edge may not be exercised within the
  run). Reports the minimum reliably detectable magnitude (rate ≥ 0.95). CSV:
  `results/eval_sensitivity.csv` with a `magnitude` column.
- **One run enough?** No — sensitivity is *about* the rate over many trials; a single
  run at a given magnitude is one Bernoulli sample.
- **Confidence:** each curve point is a proportion over N=20 (Wilson 95% CI ≈ ±0.15
  near p=0.5, tighter near 0/1). For a clean threshold curve use N=30–50 per magnitude;
  the boundary points (near theta) are the ones that need the most trials.

## Continuous Operation  (`eval_continuous.py`)

- **Model / inputs:** one detector instance over a single long run (5 seeds), phases
  normal → routing → addition → removal → recovery (150 u each), each change withdrawn
  at the next boundary. No detector reset.
- **Why:** deployment realism — checks accumulated FP/FN and that stale state from one
  phase does not corrupt later detection.
- **Expected output:** per-phase recall, accumulated FP and FN per run, and the
  active/resolved lifecycle counts. The routing phase may bleed detection into the next
  phase (slow cumulative estimator) — visible as phase-level FN/FP. CSV:
  `results/eval_continuous.csv`.
- **One run enough?** A single timeline is qualitatively informative, but FP/FN counts
  vary by seed — 5 seeds is a minimum; 10+ for reportable accumulated rates.
- **Confidence:** treat accumulated FP/FN as counts with Poisson-like spread
  (95% CI ≈ mean ± 1.96·√mean/√seeds); widen N to tighten.

## R1 — Robustness to Imperfect Observations  (`eval_robustness.py`)  *(Level 2, pending)*

- **Model / inputs:** reference model + observation perturbations at the sink-event
  level (missing/duplicate/out-of-order observations, low/high throughput, startup
  after drift). Compared against a clean baseline.
- **Why:** characterize **the current implementation** (not the algorithm) under
  realistic IoT sensing; report measured degradations, causes discussed in prose.
- **Expected output:** precision/recall/FP/FN/latency under each fault vs. clean; e.g.
  "under out-of-order observations precision fell from X% to Y%". CSV per fault level.
- **One run enough?** No — perturbations are stochastic; sweep the fault rate.
- **Confidence:** proportions over N=20 per fault level (report Wilson CIs); sweep 3–5
  fault intensities to show a trend rather than a single point.

---

## Statistical confidence — the short version

**Why one run is never enough.** Every run is a single random realization: item
arrivals, the 70/30 and QC routing draws, and the exact time the first item takes a
new edge are all stochastic. A single run gives one point that can move a lot,
especially near a threshold (E3 moderate shift, sensitivity-sweep boundary magnitudes, E2 low-freq).
Reproducibility comes from per-trial seeds + a randomized `t_change`, so 20 trials
sample the distribution deterministically.

**Two kinds of number, two ways to bound them:**

1. **Rates** (recall, detection rate, precision as a fraction of trials) are binomial
   proportions. Use a **Wilson 95% interval**, not mean ± std. Rules of thumb at N=20:
   - 20/20 → [0.84, 1.00]      - 18/20 (0.90) → [0.70, 0.97]
   - 14/20 (0.70) → [0.48, 0.86]   - 10/20 (0.50) → [0.30, 0.70]
   So N=20 proves "works nearly always" only down to ~0.84. For a rate you want to pin
   to ±0.1 near 0.5, you need **N ≈ 80–100**.

2. **Latencies** (continuous, over detected trials) → report **mean, median, worst**
   and a 95% CI = mean ± 1.96·s/√n. This is tight when variance is small (additions,
   removals: s is a few units → CI ±1–2 u at N=20) and wide when it is not (routing:
   s ≈ 40 u → CI ±18 u at N=20). Always show the worst case; a mean alone hides the
   tail that matters operationally.

**Practical recommendation.** Keep N=20 for development and for low-variance metrics
(additions, removals, recovery). Bump to **N=40–50** for the headline numbers that go
in the paper's routing and sensitivity results, and **always report the interval**
(Wilson for rates, ± CI for latencies) rather than a bare point estimate. To change N,
edit `N_TRIALS` in `tests/harness/eval_common.py` (and `SENS_TRIALS`/loop bounds for the sensitivity sweep).

---

## E5 - Complete Independent Path Addition and Withdrawal (`eval_independent_path.py`)

(E7 in the development suite.)

- **Model / inputs:** the dormant one-machine system
  `src_new -> B_new1 -> M_new -> B_new2 -> sink_new`. Its source is activated for
  80 simulation-time units and then disabled. The original model continues running,
  so the multi-sink detector receives time progress after `sink_new` becomes silent.
- **Why:** validates discovery and lifecycle handling when every component and edge
  in a path is absent from the baseline DT, including a newly active sink.
- **Expected output:** all four unknown transitions are detected on the first item,
  then all four active conditions resolve after the 60-unit removal window. Resolution
  means withdrawal of the newly observed path; it is not a `REMOVED_TRANSITION` alert,
  because the path was never part of the baseline DT.
- **Trials/output:** 20 seeded trials. The script fails if either addition or
  withdrawal is not detected in every trial and writes
  `results/eval_independent_path.csv`.
- **Measured result after the `CollectingSink` path-recording replacement:** complete
  path addition detected in **20/20** trials and complete path withdrawal detected in
  **20/20** trials. Transition recall and precision were both **1.00**, with **0 false
  positives** and **0 false negatives**. Mean addition latency was **0.50** simulation
  units; mean withdrawal-resolution latency was **60.24** and the worst was **61.33**.

---

## K1 - End-to-End KPI Validation (`eval_end_to_end_kpi.py`)

- **Model / inputs:** `tests/harness/end_to_end_kpi_model.py`, a five-station cell
  with one upstream station, a routing node, three parallel branches, a final
  station, and a sink. The third branch is pre-wired but dormant in the baseline.
- **Scenarios:** parameter drift at M2, activation of the dormant branch, removal
  of the M3 branch, and a routing-probability shift between the remaining branches.
- **Method:** for each scenario and seed, run the changed physical configuration,
  the stale baseline DT, and an updated DT. Common random-number streams isolate
  configuration error from ordinary simulation noise.
- **KPIs:** completed items; throughput computed as all sink receipts divided by
  total simulation time; mean and 95th-percentile cycle time; productive processing
  utilization (processing plus blocked time), its separate processing and blocked
  fractions, mean buffer content, and
  branch-routing shares.
- **Outputs:** `results/eval_end_to_end_kpi.csv` contains every replication and KPI;
  `results/eval_end_to_end_kpi_summary.csv` contains mean stale error, mean updated
  error, and error reduction by scenario and KPI.
- **Run:** `python tests/eval_end_to_end_kpi.py`. Optional arguments are
  `--replications` and `--duration`. The practical default is 10 replications over
  300 time units; use `--replications 20 --duration 600` for the larger paper-scale run.
- **Interpretation:** this is an in-process behavioral validation. It checks whether
  deploying the correct parameter or structural configuration restores KPI agreement;
  it does not exercise RabbitMQ, InfluxDB, DataFITR fitting, or operator timing.

## K2 - DataFITR-to-Digital-Twin KPI Validation (`eval_datafitr_kpi_10000.py`)

- **Calibration:** a real-system run emits M2 machine-exit telemetry. A normal
  DataFITR fit estimates the processing-delay mean and standard deviation.
- **Generation:** the fitted parameters are converted into a generated Digital Twin
  configuration; the DT is not given the real-system parameter values directly.
- **Validation:** the real configuration and fitted-parameter DT are each simulated
  for 10,000 independent trials. Throughput, cycle time, productive utilization,
  blocking, buffer content, and routing shares are aggregated and compared.
- **Run:** `python tests/eval_datafitr_kpi_10000.py`. The default trial duration is
  30 time units in the script; the reported 10,000-run result used `--duration 20`.
- **Outputs:** `results/eval_datafitr_kpi_10000.csv` contains per-replication KPI
  pairs, and `results/datafitr_fitted_parameters.csv` contains the fitted parameter
  module values.
