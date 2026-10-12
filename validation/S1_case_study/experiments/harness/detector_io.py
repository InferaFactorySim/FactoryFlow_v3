"""Glue between captured telemetry and the deployed detector.

Provides: sink-event extraction, DT-graph construction from a baseline run,
running the detector over a sink-event stream and normalising its alerts, and
programmatic ground-truth derivation (added / removed edges, shifted routing
branches) by diffing an observed graph against the DT graph.
"""
import os
import re
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (_HERE, os.path.join(_ROOT, "src"), os.path.join(_ROOT, "structural_drift_detection")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from drift_detection import (
    DigitalTwinGraph,
    MultiSinkDriftDetector,
    StructuralDriftDetector,
)

# Alerts carry the edge as "src → dst" in their detail string.
_EDGE_RE = re.compile(r"(\S+)\s*→\s*(\S+)")


def sink_entries(events):
    """Return Sink-entry events sorted by sim_time (the only events the detector uses)."""
    evs = [e for e in events
           if e.get("entity_type") == "Sink" and e.get("event_type") == "entry"]
    evs.sort(key=lambda e: e.get("sim_time", 0.0))
    return evs


def _paths(events):
    for e in sink_entries(events):
        path = (e.get("attributes") or {}).get("item_path") or []
        if len(path) >= 2:
            yield path


def graph_edges(events):
    """Set of directed (src, dst) transitions observed across all completed paths."""
    edges = set()
    for path in _paths(events):
        for a, b in zip(path, path[1:]):
            edges.add((a, b))
    return edges


def routing_dist(events):
    """Per-node successor distribution {node: {succ: fraction}}."""
    counts = defaultdict(lambda: defaultdict(int))
    for path in _paths(events):
        for a, b in zip(path, path[1:]):
            counts[a][b] += 1
    dist = {}
    for node, succ in counts.items():
        total = sum(succ.values())
        if total:
            dist[node] = {v: c / total for v, c in succ.items()}
    return dist


def build_dt_graph(baseline_events):
    """Construct a DigitalTwinGraph from a baseline run (mirrors dt_graph_exporter)."""
    dt = DigitalTwinGraph(path="")   # never reads from disk
    dt.loaded = True
    for path in _paths(baseline_events):
        for a, b in zip(path, path[1:]):
            dt.transitions[a].add(b)
            dt.counts[a][b] += 1
    return dt


def expected_changes(dt_events, modified_events, theta=0.15):
    """Derive ground truth by diffing a steady modified run against the DT run.

    Returns (added_edges:set, removed_edges:set, routing_nodes:set) where
    routing_nodes are branch nodes whose successor distribution shifted by more
    than ``theta`` on an edge that exists in both graphs (i.e. a routing change,
    not a structural add/remove).
    """
    e_dt = graph_edges(dt_events)
    e_mod = graph_edges(modified_events)
    added = e_mod - e_dt
    removed = e_dt - e_mod

    d_dt = routing_dist(dt_events)
    d_mod = routing_dist(modified_events)
    routing_nodes = set()
    for node, succ_dt in d_dt.items():
        # The detector only evaluates routing at nodes that branch in the DT
        # (>= 2 successors); a node that merely gains a successor is an addition.
        if len(succ_dt) < 2:
            continue
        succ_mod = d_mod.get(node, {})
        for succ, p_dt in succ_dt.items():
            if (node, succ) in added or (node, succ) in removed:
                continue  # structural change, not a routing shift
            if abs(succ_mod.get(succ, 0.0) - p_dt) > theta:
                routing_nodes.add(node)
                break
    return added, removed, routing_nodes


def alert_edge(alert):
    """Extract (src, dst) from a DriftAlert via its detail string."""
    m = _EDGE_RE.search(alert.detail or "")
    if m:
        return m.group(1), m.group(2)
    return alert.node, None


def run_detector(dt_graph, sink_evts, removal_window=60.0,
                 routing_threshold=0.15, min_routing_samples=10):
    """Feed sink events to a fresh detector; return (detector, normalised alert log).

    Each alert log entry: {sim_time, alert_type, event, severity, src, dst, node}.
    """
    det = StructuralDriftDetector(
        dt_graph,
        routing_threshold=routing_threshold,
        min_routing_samples=min_routing_samples,
        removal_window=removal_window,
    )
    log = []
    for e in sink_evts:
        for a in det.ingest_event(e):
            src, dst = alert_edge(a)
            log.append({
                "sim_time": a.sim_time,
                "alert_type": a.alert_type,
                "event": a.event.value,
                "severity": a.severity,
                "src": src,
                "dst": dst,
                "node": a.node,
            })
    return det, log


def run_multi_sink_detector(dt_graph, sink_evts, removal_window=60.0,
                            routing_threshold=0.15, min_routing_samples=10):
    """Replay sink events through the deployed multi-sink coordination path.

    Periodic reconciliation is invoked after every replayed event. This mirrors
    the production background reconciler while allowing the shared simulation-
    time watermark from active sinks to resolve a different sink that goes quiet.
    """
    det = MultiSinkDriftDetector(
        dt_graph,
        routing_threshold=routing_threshold,
        min_routing_samples=min_routing_samples,
        removal_window=removal_window,
    )
    log = []

    def record(alerts):
        for alert in alerts:
            src, dst = alert_edge(alert)
            log.append({
                "sim_time": alert.sim_time,
                "alert_type": alert.alert_type,
                "event": alert.event.value,
                "severity": alert.severity,
                "src": src,
                "dst": dst,
                "node": alert.node,
                "sink_id": alert.sink_id,
            })

    for event in sink_evts:
        record(det.ingest_event(event))
        record(det.reconcile_periodic())
    return det, log
