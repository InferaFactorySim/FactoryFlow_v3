# S4 — system description

Ten identical production lines in parallel, sharing one source and one sink.
Use every name exactly as given; these are the ids the plant reports in its
telemetry. Full ids are prefixed by their containers, e.g. `TOP.Line_05.M02`.

## Hierarchy

Top-level container `TOP` holds the source `src`, the sink `sink`, ten line
containers `Line_00` … `Line_09`, and the entry and exit buffers. All lines are
identical: build one line class and instantiate it ten times.

## Nodes

- `src`: Source, `inter_arrival_time=0.75`, `blocking=True`,
  `out_edge_selection="ROUND_ROBIN"` over `B_IN_00` … `B_IN_09` in that order.
- Machines `M00` … `M09` in each line, in flow order (100 in all), each with
  `work_capacity=1`, `blocking=True`, `in_edge_selection="FIRST_AVAILABLE"`,
  `out_edge_selection="ROUND_ROBIN"`. `processing_delay` is not given: it is
  inferred from telemetry per machine.
- `sink`: Sink receiving from all ten lines.

## Edges within each line

Machines are joined in series, `M00` → `M01` → … → `M09`:

- ConveyorBelt, `capacity=2`, `delay=0.5`: `CV00_01`, `CV03_04`, `CV05_06`,
  `CV08_09`.
- Buffer, `capacity=3`: `B01_02`, `B02_03`, `B06_07`, `B07_08`.
- Buffer, `capacity=10`: `BMID04_05` (mid-line bank).

Each edge joins the two machines its name gives, e.g. `CV03_04` joins `M03` to
`M04`.

## Edges owned by `TOP`

For each line `rr` = `00` … `09`:

- Buffer `B_IN_rr`, `capacity=5`: `src` → `Line_rr.M00`.
- Buffer `B_OUT_rr`, `capacity=5`: `Line_rr.M09` → `sink`.

All buffers are FIFO with no delay.

## Power ratings (W)

Set with `set_power_ratings` on four machine states only:
`ATLEAST_ONE_PROCESSING_STATE`, `IDLE_STATE`, `ALL_ACTIVE_BLOCKED_STATE`,
`SETUP_STATE`. Ratings depend only on position, given as processing / idle /
blocked / setup:

- `M00` 7000/800/900/3000; `M01` 6500/750/850/2800; `M02` 5500/700/800/2500
- `M03` 8000/900/1000/3500; `M04` 12000/1500/1700/5000; `M05` 5000/600/700/2200
- `M06` 4000/500/600/1800; `M07` 6000/700/800/2600; `M08` 5800/700/800/2500
- `M09` 7500/850/950/3200

Every ConveyorBelt: `MOVING_STATE` 400, `STALLED_STATE` 120, `EMPTY_STATE` 50.
Buffers draw no power.
