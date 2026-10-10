# S4 — Parallel conveyor-linked production lines (reference system)

Ten parallel production lines sharing one source and one sink. Each line has ten
machines joined by four conveyor belts and five buffers. Used as the large-system
reference for FactoryFlow's parameter and structural synchronisation study.

## Structure

| | |
|---|---|
| Nodes | 102 — 1 source, 100 machines, 1 sink |
| Containers | 10 `Line` + 1 `SystemModel` (one hierarchy level) |
| Edges | 110 — 40 `ConveyorBelt`, 70 `Buffer` |
| Source | `inter_arrival_time=0.75`, `ROUND_ROBIN` over the ten entry buffers |
| Sink | one shared sink collecting all ten exit buffers |

Per line, with `rr` the line index and `cc` the column index:

```
Source ─B_IN_[rr]─► M00 ─CV─ M01 ─B─ M02 ─B─ M03 ─CV─ M04
                                                       │
                                                  B_MID
                                                       │
                     M09 ─CV─ M08 ─B─ M07 ─B─ M06 ─CV─ M05
                      │
                 B_OUT_[rr] ─► Sink
```

Identifiers: machines `M[rr][cc]`, conveyors `CV_[rr][cc]_[rr][cc+1]`, inter-machine
buffers `B_[rr][cc]_[rr][cc+1]`, decoupling buffers `B_MID_...`, and `B_IN_[rr]` /
`B_OUT_[rr]`. Every edge is named, because unnamed edges are the dominant source of
identifier errors in generated models.

## Processing delays

The family is fixed per column and identical across all ten lines, so the system
holds **ten distinct distributions with ten independent replicates each** — fit
repeatability is measurable from a single run.

| Col | Family | scipy | Mean | CV | Skew |
|---|---|---|---|---|---|
| C0 | normal | `norm(4.0, 0.10)` | 4.000 | 0.025 | 0.00 |
| C1 | lognormal | `lognorm(0.45, scale=4.5157)` | 5.000 | 0.474 | 1.53 |
| C2 | gamma | `gamma(9, scale=0.5)` | 4.500 | 0.333 | 0.67 |
| C3 | weibull | `weibull_min(5.0, scale=5.6627)` | 5.200 | 0.229 | −0.25 |
| C4 | uniform | `uniform(1.0, 2.0)` | 2.000 | 0.289 | 0.00 |
| C5 | triangular | `triang(0.25, loc=3.0, scale=4.0)` | 4.667 | 0.182 | 0.42 |
| C6 | uniform | `uniform(2.0, 1.0)` | 2.500 | 0.115 | 0.00 |
| C7 | gamma | `gamma(16, scale=0.3)` | 4.800 | 0.250 | 0.50 |
| C8 | lognormal | `lognorm(0.30, scale=4.3989)` | 4.600 | 0.307 | 0.95 |
| C9 | weibull | `weibull_min(2.5, scale=5.6350)` | 5.000 | 0.428 | 0.36 |

**No sample is ever negative and no clipping is applied.** `loc` is fixed at 0 for
every skewed family, so a fitter cannot mimic one family with another by floating the
shift, the normal is held to CV = 0.025 and the uniforms have positive supports, so
P(X < 0) is zero to machine precision. Clipping
is avoided deliberately: truncating a distribution makes it fit as a different family.

The closest pair of columns separates at KS distance 0.052, against a critical value
of 0.0195 at n = 7000, alpha = 0.01.

## Conveyors, buffers, energy

Conveyors are `capacity=2` slots with `delay=0.5` per slot, giving 1.0 transit time
each; the delay is deterministic because belt speed is a physical constant. Buffer
capacities are 5 at line entry and exit, 10 at the mid-line decoupling bank, 3 between
machines.

Per-state power is declared with `set_power_ratings` in watts, so energy is an output
of the model rather than a separate calculation. Machines rate only the four states
that partition their time — `SETUP_STATE`, `IDLE_STATE`, `ALL_ACTIVE_BLOCKED_STATE`
and `ATLEAST_ONE_PROCESSING_STATE`; the other two overlap with these and would
double-count. Processing power ranges from 4.0 kW at C6 to 12.0 kW at C4. The
bottleneck is C3 (mean 5.2): 69% utilisation with ten lines fed, 77% once line 00
goes dark.

## The experiment

One continuous run of 12,000 time units through three phases, paced against the
wall clock at one time unit per second. Nothing announces either change to the
framework; the intervention times, affected components and new parameter values
are ground truth held back for evaluation.

| Phase | Window | Change | Measured over |
|---|---|---|---|
| A | 0 - 4,000 | none, baseline | 0 - 4,000 |
| B | 4,000 - 8,000 | C4 `uniform(1, 2)` -> `uniform(3, 2)`, mean 2.0 -> 4.0; C0 `norm(4.0, 0.10)` -> `lognorm(0.35, scale=3.7623523)` at unchanged mean; C6 `uniform(2, 1)` -> `norm(5.0, 0.40)`, mean 2.5 -> 5.0 | 4,200 - 8,000 |
| C | 8,000 - 12,000 | the source stops feeding line 00 | 9,000 - 12,000 |

Measurement windows open after each change: 200 time units after the parameter
change and 1,000 after the routing change, which the plant needs to re-settle.

Each machine logs about 530 readings in phase A and 530 in phase B, and lines
01 - 09 about 590 in phase C, so a 300-value fitting window fits inside every
phase on every line.

C0 preserves the mean exactly, so a drift test based on means alone cannot
detect it. The phase C change is a routing policy,
not a topology edit: line 00's machines still exist, but every one of its
transitions stops appearing, which is what the structural monitor must notice.

### How each change is detected

The drift check fires if any of four criteria trip: KS p below 0.01, normalised
Wasserstein above 0.25, mean shift above 12%, or standard-deviation shift above
12%. Against those thresholds:

| Change | mean % | std % | KS p | norm. W | Fires on |
|---|---|---|---|---|---|
| C4 uniform mean 2.0 -> 4.0 | 100.0 | 0.0 | 1.5e-179 | 3.47 | KS, Wasserstein, mean |
| C0 normal -> lognormal | 0.0 | 1344 | 1.9e-34 | 10.4 | KS, Wasserstein, std |
| C6 uniform -> normal, mean 2.5 -> 5.0 | 100.0 | 38.6 | 1.5e-179 | 8.67 | KS, Wasserstein, mean, std |

Mean and std columns are the true population shifts; KS p and normalised W are
medians over 400 simulated pairs of 300-value windows. KS and Wasserstein catch
all three; the mean-shift test misses C0 and the std-shift test misses C4. The
structural change is detected by the removal window, 60 time units.

### Fitting windows

The signal is the same at any window size - it is a property of the
distributions - but the noise floor is not. Simulated with the four criteria
above, 400 window pairs per cell (fill time at 7.5 time units per reading):

| N | Fill time | Detection, C0 / C4 / C6 | False alarms with no change, C0 / C4 / C6 |
|---|---|---|---|
| 100 | 750 | 100% / 100% / 100% | 32.5% / 14.5% / 13.5% |
| 200 | 1,500 | 100% / 100% / 100% | 9.8% / 3.0% / 3.5% |
| 300 | 2,250 | 100% / 100% / 100% | 5.2% / 1.0% / 0.8% |

Every change is detected at every size, so the window is set by false alarms.
Nearly all of them come from the 12% std-shift test. The relative noise of a
sample standard deviation does not depend on scale but on tail weight, so the
uniforms stay near 1% while the heavier-tailed columns do not. At N = 300, with
no change at all, the share of windows that fire is:

| C0 | C1 | C2 | C3 | C4 | C5 | C6 | C7 | C8 | C9 |
|---|---|---|---|---|---|---|---|---|---|
| 5.0% | 22.7% | 8.3% | 4.9% | 1.2% | 2.6% | 1.1% | 5.6% | 12.6% | 3.1% |

With the std-shift threshold at 25% every column falls to 3.4% or below and all
three changes are still detected (KS alone catches each of them 100% of the
time). Consecutive checks share most of their window, so the confirmation count
suppresses little of this.

## Running it

```bash
# default (also what the real-system UI runs): phase A only, 0 - 4,000,
# as fast as possible, with telemetry; stops before the parameter change
python S4_GT.py

# full live run, paced at one time unit per second
S4_SIM_TIME=12000 S4_REALTIME=1 S4_REALTIME_FACTOR=1 python S4_GT.py

# fast characterisation, no telemetry
S4_TELEMETRY=0 S4_SIM_TIME=12000 python S4_GT.py
```

Environment: `S4_SIM_TIME`, `S4_SEED`, `S4_REALTIME`, `S4_REALTIME_FACTOR`,
`S4_TELEMETRY`, `S4_SAMPLE_INTERVAL` (default 50), `S4_SAMPLE_PATH`.

The run records its own cumulative series while it publishes telemetry, so the
reference KPIs over any window follow by difference without a separate harness.
`discover_models()` finds this file automatically, and the diagram builder never
reaches `main()`, so drawing the model opens no telemetry connection.

### Digital Twin runs

Each twin version runs only for its own phase, but for **about 5,000 time units**
rather than the phase length. `kpi_validation.py` reports over a fixed trailing
horizon of 3,600 simulation units, so a 5,000-unit run gives a window of
1,000-5,000 - which excludes the twin's cold-start fill automatically. A twin run
of only 2,000 units would be nearly half transient and would under-report cycle
time and queue occupancy against a reference that was never empty.

| Version | Generated from | Run for | Compared against |
|---|---|---|---|
| DT0 | baseline fit | 5,000 | reference 0 - 4,000 |
| DT1 | refit after the parameter change | 5,000 | reference 4,200 - 8,000 |
| DT2 | regeneration after the routing change | 5,000 | reference 9,000 - 12,000 |

The twin UI supplies total items, average cycle time and throughput directly, and
per-machine utilisation and state times. Energy follows from those state times and
the power ratings the twin declares. Queue occupancy has no twin-side equivalent.

## Measurement

Cumulative counters, state times and occupancy integrals are sampled every 50
time units, so KPIs over any interval follow by difference. `max_cycle_time` is
read and reset at each sample, so each interval carries its own maximum and a
window's maximum is the largest of its intervals.

Occupancy uses the integrals (`_weighted_sum`, `_weighted_occupancy`) rather than
the running averages the stats dict exposes, because only the integrals difference
correctly. Buffers never update their state times, so only their occupancy is
recorded.

System WIP is deliberately not reported: by Little's Law it is throughput times
mean cycle time, both already present. Queue occupancy - items waiting in buffers
and on belts, excluding those being processed - is independent of both and is
reported instead.

## Monitoring configurations

`make_monitoring_configs.py` writes 200 records to
`s4_monitoring_configurations.json`: 100 machines x two fitting windows, keyed by
the hierarchical ids above. Copy it over
`parameter_drift/data/monitoring_configurations.json` when ready, backing up the
existing file first - it holds 20 records for an older, flat-named model.

Energy cannot be drift-monitored: machines publish `process_delay` and item ids
only, and energy is derived from state times and declared power ratings rather
than measured. It appears in the KPI comparison, not in drift detection.

## Generating the twin

`description_detailed.md` and `description_coarse.md` are the two description
granularities for the model generator. Neither states the processing delays:
those are inferred from telemetry. Both name every component exactly as the
reference does, so the generated twin's component ids match the telemetry's
`entity_id`s, the fitted plugins bind to the right components, and the structural
graph comparison lines up.
