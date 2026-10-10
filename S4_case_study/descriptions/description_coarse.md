# S4 — coarse system description

The plant runs ten identical production lines side by side. One source, `src`,
feeds all of them and one sink, `sink`, collects from all of them. The lines are
called `Line_00` through `Line_09`.

Each line is a series of ten machines, `M00` through `M09` in flow order. Items
enter at `M00` and leave from `M09`. Within a line the machines are linked by a
mixture of conveyor belts and buffers: belts carry items from `M00` to `M01`,
`M03` to `M04`, `M05` to `M06` and `M08` to `M09`, and the remaining five links
are buffers. Each edge is named for the machines it joins, so the belt from
`M00` to `M01` is `CV00_01` and the buffer from `M01` to `M02` is `B01_02`. The
buffer in the middle of the line, from `M04` to `M05`, is called `BMID04_05`; it
is larger than the others and acts as a bank that lets the two halves of the
line run independently for a while.

Every line also has a buffer where the source feeds it and a buffer where it
discharges to the sink, named after the line: `B_IN_00` and `B_OUT_00` serve
`Line_00`, and so on through `B_IN_09` and `B_OUT_09`. These belong to the plant
rather than to the line.

The source releases one item every 0.75 time units and sends items to the lines
in turn, so the ten lines receive equal shares of the work.

Conveyor belts have two slots and move an item one slot every half a time unit.
The entry and discharge buffers hold five items each, the mid-line bank holds
ten, and the buffers between machines hold three.

Machines draw different amounts of power depending on what they are doing: most
while processing, much less while idle, slightly more than idle while blocked,
and an intermediate amount during setup. Machines at the same position in
different lines are the same model and have the same ratings. `M04` is the
largest, drawing 12 kW while processing; `M06` is the smallest at 4 kW; the rest
fall between 5 and 8 kW. Belts draw 400 W when moving, 120 W stalled and 50 W
empty.

The processing times of the machines are measured from the running plant rather
than specified here.
