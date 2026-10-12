"""Observation-level perturbations for the R1 robustness experiment.

The detector consumes complete, in-item-ordered Sink paths, so realistic sensing
faults are modelled by corrupting the sink-event stream (not the transport). Each
function takes a list of sink-entry event dicts and returns a NEW list; originals
are not mutated. Throughput changes are not here — they are applied at the model
level via ``run_capture(inter_arrival=...)``.
"""
import copy


def _clone(ev):
    e = copy.copy(ev)
    e["attributes"] = copy.copy(ev.get("attributes") or {})
    if "item_path" in e["attributes"]:
        e["attributes"]["item_path"] = list(e["attributes"]["item_path"])
    return e


def drop_observations(sink_evts, p, rng):
    """Missing sensor reads: drop one random intermediate node from each path w.p. p.

    Endpoints (first/last) are preserved so the path stays a valid sequence; the
    gap produces a skip-edge in the reconstructed adjacency.
    """
    out = []
    for ev in sink_evts:
        e = _clone(ev)
        path = e["attributes"].get("item_path") or []
        if len(path) > 3 and rng.random() < p:
            idx = rng.randint(1, len(path) - 2)  # never an endpoint
            del path[idx]
            e["attributes"]["item_path"] = path
        out.append(e)
    return out


def duplicate_observations(sink_evts, rate, rng):
    """Duplicate messages: re-deliver a fraction of sink events (copy kept adjacent)."""
    out = []
    for ev in sink_evts:
        out.append(ev)
        if rng.random() < rate:
            out.append(_clone(ev))
    return out


def reorder_observations(sink_evts, window, rng):
    """Timestamp disorder: locally shuffle arrival order within a sliding window.

    sim_time on each event is unchanged; only the order in which the detector sees
    them is perturbed (the detector processes in arrival order with no monotonicity
    guard). Do NOT re-sort downstream, or this is undone.
    """
    out = []
    buf = []
    for ev in sink_evts:
        buf.append(ev)
        if len(buf) >= window:
            rng.shuffle(buf)
            out.extend(buf)
            buf = []
    rng.shuffle(buf)
    out.extend(buf)
    return out
