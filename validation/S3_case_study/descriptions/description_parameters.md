# S4 — system description with parameter values

A plant of ten identical production lines working in parallel, fed by one
source and emptied by one sink. Every component, every connection and every
constant parameter is given below. The names are the ones the running plant
reports in its telemetry, so they must be used exactly as written. A
component's full identifier is its name prefixed by its containers: the third
machine of line 05 is `TOP.Line_05.M02`.

The processing delay of each machine is not a constant and is not given here.
It is obtained from telemetry as a fitted distribution, one per machine, and is
attached to the machine with that machine's full identifier. Every other
parameter below is a constant.

## Containment

- The whole plant is one top-level container named `TOP`.
- `TOP` contains ten line containers named `Line_00` through `Line_09`, the
  source `src`, the sink `sink`, and the twenty buffers that join the lines to
  the source and the sink.
- Each line contains its ten machines `M00` through `M09` and the nine edges
  that join them.
- All ten lines have exactly the same internal structure, edge names and
  parameter values, so a line should be built once as a reusable container and
  instantiated ten times.

## Source

One source named `src`.

| Parameter | Value |
|---|---|
| `inter_arrival_time` | 0.75 (constant; one item every 0.75 time units) |
| `blocking` | `True` (waits when the selected line cannot accept an item) |
| `out_edge_selection` | `"ROUND_ROBIN"` over its ten outgoing edges, in the order `B_IN_00`, `B_IN_01`, …, `B_IN_09` |

The source has ten outgoing edges, one per line. Round-robin selection sends
each successive item to the next line, so every line receives an equal share.

## Machines

One hundred machines, ten per line, named `M00` through `M09` in flow order.
All machines have the same constant parameters:

| Parameter | Value |
|---|---|
| `work_capacity` | 1 |
| `blocking` | `True` (holds a finished item until its outgoing edge has space) |
| `node_setup_time` | 0 |
| `in_edge_selection` | `"FIRST_AVAILABLE"` |
| `out_edge_selection` | `"ROUND_ROBIN"` |
| `processing_delay` | Not constant: supplied per machine by its fitted distribution |

Each machine has exactly one incoming and one outgoing edge, so the edge
selection policies have no choice to make; they are stated only so that no
default has to be assumed.

## Sink

One sink named `sink`. It has ten incoming edges, `B_OUT_00` through
`B_OUT_09`, one per line, and accepts items from all of them. It has no other
parameters.

## Connections within a line

The ten machines of a line are connected in series. Four of the nine
connections are conveyor belts and five are buffers. Names and parameters are
the same in every line:

| From | To | Edge type | Name | `capacity` | `delay` |
|---|---|---|---|---|---|
| `M00` | `M01` | ConveyorBelt | `CV00_01` | 2 slots | 0.5 per slot |
| `M01` | `M02` | Buffer | `B01_02` | 3 | 0 |
| `M02` | `M03` | Buffer | `B02_03` | 3 | 0 |
| `M03` | `M04` | ConveyorBelt | `CV03_04` | 2 slots | 0.5 per slot |
| `M04` | `M05` | Buffer | `BMID04_05` | 10 | 0 |
| `M05` | `M06` | ConveyorBelt | `CV05_06` | 2 slots | 0.5 per slot |
| `M06` | `M07` | Buffer | `B06_07` | 3 | 0 |
| `M07` | `M08` | Buffer | `B07_08` | 3 | 0 |
| `M08` | `M09` | ConveyorBelt | `CV08_09` | 2 slots | 0.5 per slot |

`BMID04_05` is a larger decoupling bank in the middle of the line. A conveyor
belt advances an item one slot every 0.5 time units, so an item that is not
held up spends 1.0 time unit on a belt; a belt stalls as a whole when its
output is not collected.

## Connections to the source and the sink

These twenty buffers belong to `TOP`, not to the lines. For each line `rr`
(`00` through `09`):

| From | To | Edge type | Name | `capacity` | `delay` |
|---|---|---|---|---|---|
| `src` | `Line_rr.M00` | Buffer | `B_IN_rr` | 5 | 0 |
| `Line_rr.M09` | `sink` | Buffer | `B_OUT_rr` | 5 | 0 |

So `TOP` holds `B_IN_00` … `B_IN_09` and `B_OUT_00` … `B_OUT_09`.

All buffers in the plant use `mode="FIFO"` and introduce no delay of their own.

## Power ratings

Power is set per state, in watts, with `set_power_ratings`. Only the four
states that partition a machine's time are rated: processing
(`ATLEAST_ONE_PROCESSING_STATE`), idle (`IDLE_STATE`), blocked
(`ALL_ACTIVE_BLOCKED_STATE`) and setup (`SETUP_STATE`). The rating depends only
on the machine's position in its line, so `M04` in every line has the same
ratings:

| Machine | Processing | Idle | Blocked | Setup |
|---|---|---|---|---|
| `M00` | 7000 | 800 | 900 | 3000 |
| `M01` | 6500 | 750 | 850 | 2800 |
| `M02` | 5500 | 700 | 800 | 2500 |
| `M03` | 8000 | 900 | 1000 | 3500 |
| `M04` | 12000 | 1500 | 1700 | 5000 |
| `M05` | 5000 | 600 | 700 | 2200 |
| `M06` | 4000 | 500 | 600 | 1800 |
| `M07` | 6000 | 700 | 800 | 2600 |
| `M08` | 5800 | 700 | 800 | 2500 |
| `M09` | 7500 | 850 | 950 | 3200 |

Every conveyor belt draws 400 W while moving (`MOVING_STATE`), 120 W while
stalled (`STALLED_STATE`) and 50 W while empty (`EMPTY_STATE`). Buffers consume
no energy.

## Summary

| Component | Count | Names |
|---|---|---|
| Source | 1 | `src` |
| Sink | 1 | `sink` |
| Line containers | 10 | `Line_00` … `Line_09` |
| Machines | 100 | `Line_rr.M00` … `Line_rr.M09` |
| Conveyor belts | 40 | `CV00_01`, `CV03_04`, `CV05_06`, `CV08_09` in each line |
| Inter-machine buffers | 50 | `B01_02`, `B02_03`, `BMID04_05`, `B06_07`, `B07_08` in each line |
| Entry and exit buffers | 20 | `B_IN_00` … `B_IN_09`, `B_OUT_00` … `B_OUT_09` |
