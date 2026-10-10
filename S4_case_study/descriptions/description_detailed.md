# S4 — detailed system description

A plant of ten parallel production lines sharing one source and one sink. Every
component and every connection is named below, and these names are the ones the
running system reports in its telemetry: a component's full identifier is its
own name prefixed by its container, so the third machine of line 05 is
`TOP.Line_05.M02`. Processing delays are not given here; they are inferred from
telemetry. Everything else is a configuration attribute of the system.

## Flow nodes

One source named `src`. It releases items one at a time and distributes them
over the ten lines in round-robin order, sending each successive item to the
next line. Its inter-arrival time is 0.75 time units. It blocks when the line it
has selected cannot accept an item.

One hundred machines. Each line contains ten, named `M00` through `M09` in flow
order. Each machine has a work capacity of one and blocks when its outgoing edge
is full.

One sink named `sink`, which receives finished items from all ten lines.

## Containment

The ten lines are containers named `Line_00` through `Line_09`. A line owns its
ten machines and the nine edges that join them. The buffers that connect a line
to the source and to the sink belong to the top-level system, not to the line.

## Connections within a line

Within each line the ten machines are connected in series. Four of the nine
connections are conveyor belts and five are buffers:

| From | To | Edge type | Name |
|---|---|---|---|
| `M00` | `M01` | conveyor | `CV00_01` |
| `M01` | `M02` | buffer | `B01_02` |
| `M02` | `M03` | buffer | `B02_03` |
| `M03` | `M04` | conveyor | `CV03_04` |
| `M04` | `M05` | buffer | `BMID04_05` |
| `M05` | `M06` | conveyor | `CV05_06` |
| `M06` | `M07` | buffer | `B06_07` |
| `M07` | `M08` | buffer | `B07_08` |
| `M08` | `M09` | conveyor | `CV08_09` |

Every line has the same internal arrangement and the same edge names.

## Connections to the source and the sink

For each line there are two further buffers, owned by the top-level system and
named after the line they serve. For line `rr`:

- `B_IN_rr` connects `src` to that line's `M00`
- `B_OUT_rr` connects that line's `M09` to `sink`

So the top level holds `B_IN_00` … `B_IN_09` and `B_OUT_00` … `B_OUT_09`. The
source has ten outgoing edges and the sink ten incoming edges.

## Edge parameters

Every conveyor belt has two slots and takes 0.5 time units to advance an item by
one slot, so an item spends 1.0 time unit on a belt when it is not held up. A
belt stalls as a whole when its output is not collected.

Buffer capacities are 5 for each `B_IN_rr`, 3 for each inter-machine buffer,
10 for each mid-line buffer `BMID04_05`, and 5 for each `B_OUT_rr`. All buffers
release items in first-in-first-out order and introduce no delay of their own.

## Power ratings

Each machine draws power that depends on its state, in watts. The rating depends
only on the machine's position in its line, so `M04` in every line shares the
same ratings:

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

Rate only the four states that partition a machine's time: processing, idle,
blocked and setup. Every conveyor belt draws 400 W while moving, 120 W while
stalled and 50 W while empty. Buffers consume no energy.

## What is inferred rather than described

The processing delay of each of the hundred machines is obtained from telemetry,
not from this description. Each machine has its own delay distribution. Machines
at the same position in different lines were built to the same specification, so
`M04` in every line follows the same distribution as every other `M04`.
