"""
Structural Drift Detection for Manufacturing Systems — Digital Twin approach
============================================================================
The simulation model IS the Digital Twin (ground truth topology).
Compares live real-system paths (from RabbitMQ) against the DT graph
exported by the simulation model to /data/dt_graph.json.

No warmup phase — detection starts from the first real-system item.

Alert types:
  new_transition        — real path has A→B not in DT graph
  removed_transition    — DT has A→B but the real system has not used it for
                          removal_window simulation-time units
  routing_policy_change — real routing % at branching node deviates from DT routing % by > threshold

─────────────────────────────────────────────────────────────────────────────
Path reconstruction
─────────────────────────────────────────────────────────────────────────────
Each item carries an append-only `path_events` visit log populated whenever it
enters a component. Every visit has a component ID, simulation timestamp, and
monotonic per-item sequence number, so repeated visits and equal timestamps are
preserved. When the item reaches the Sink, sink.behaviour() projects that visit
log into the ordered list attached to the telemetry event:

    attributes = {
        "item_id":   "item_42",
        "item_path": ["TOP.src", "TOP.e[0]", "TOP.M[0]", ...],
        "path_events": [
            {"component_id": "TOP.src", "sim_time": 0.5, "sequence": 1},
            ...
        ]
    }

The drift detector continues to read "item_path" directly from sink events;
the detailed visit log is retained for evidence and traceability.
"""

import json
import logging
import os
import pprint
import time
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Deque, Dict, Iterator, List, Optional, Set, Tuple

import pika

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

# Alert type identifiers — these strings are part of the RabbitMQ/HTTP wire
# format, so they must not change. Kept as plain str constants (not an Enum) so
# DriftAlert.alert_type stays an ordinary str for __str__, JSON and comparisons.
NEW_TRANSITION        = "new_transition"
REMOVED_TRANSITION    = "removed_transition"
ROUTING_POLICY_CHANGE = "routing_policy_change"
PATH_FAILURE          = "path_failure"

# Alert severities.
WARNING  = "warning"
CRITICAL = "critical"


class AlertEvent(str, Enum):
    """State-change kind carried on the wire in the optional ``event`` field.

    Subclasses ``str`` so ``json.dumps`` serialises the value ("active"/"resolved")
    transparently and equality against plain strings still works for consumers.
    A typed enum prevents typos ("resolve", "Active") from creating invalid events.
    """
    ACTIVE   = "active"
    RESOLVED = "resolved"


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers (implementation details — not part of the public API)
# ──────────────────────────────────────────────────────────────────────────────

def _iter_edges(path: List[str]) -> Iterator[Tuple[str, str]]:
    """Yield consecutive (src, dst) node pairs along a path."""
    return zip(path, path[1:])


def _routing_percentages(
    counts: Dict[str, Dict[str, int]]
) -> Dict[str, Dict[str, float]]:
    """Convert per-node successor counts into normalized routing percentages.

    Nodes with no outgoing traffic (total == 0) are omitted.
    """
    result: Dict[str, Dict[str, float]] = {}
    for node, successors in counts.items():
        total = sum(successors.values())
        if total > 0:
            result[node] = {s: c / total for s, c in successors.items()}
    return result


def _sorted_transitions(
    transitions: Dict[str, Set[str]]
) -> Dict[str, List[str]]:
    """Return transitions with each successor set rendered as a sorted list."""
    return {k: sorted(v) for k, v in transitions.items()}


# ──────────────────────────────────────────────────────────────────────────────
# Data types
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ConditionKey:
    """Stable identity of a structural drift condition.

    A frozen dataclass (hashable) used as the key of the active-condition map, in place
    of raw tuples — clearer debugging, real type hints, and safe future extension.
    ``dst`` is None for node-scoped conditions.
    """
    alert_type: str
    node: str
    dst: Optional[str] = None


@dataclass
class DriftAlert:
    alert_type: str   # NEW_TRANSITION | ROUTING_POLICY_CHANGE | REMOVED_TRANSITION | PATH_FAILURE
    node: str
    detail: str
    sim_time: float
    severity: str     # WARNING | CRITICAL
    sink_id: str = ""
    # Whether this alert marks a condition becoming active or resolved. Serialised to
    # the optional wire field "event"; existing consumers simply ignore it.
    event: AlertEvent = AlertEvent.ACTIVE
    dst: Optional[str] = None

    def __str__(self) -> str:
        sink_tag = f" [sink={self.sink_id}]" if self.sink_id else ""
        return (
            f"[{self.severity.upper()}] {self.event.value} {self.alert_type} @ "
            f"{self.node}: {self.detail}  (sim_time={self.sim_time:.1f}){sink_tag}"
        )


@dataclass
class ActiveCondition:
    """Single source of truth for one live drift condition.

    Holds everything reconciliation needs (so updates never require a second lookup)
    and everything required to *derive* a DriftAlert via ``to_alert`` — severity/detail
    live here once, not duplicated on the alert. Timestamps are stored as ``datetime``;
    ISO formatting happens only at the presentation boundary (summary / publish).
    """
    alert_type: str
    node: str
    dst: Optional[str]         # None for node-scoped (routing-at-node) display
    since_sim_time: float
    since_wall_time: datetime
    last_seen: float           # sim_time of most recent observation (drives resolution)
    count: int                 # occurrences while active
    severity: str
    detail: str

    def to_alert(self, event: AlertEvent, sim_time: float) -> DriftAlert:
        """Build the wire alert from this canonical condition (no duplicated fields)."""
        return DriftAlert(
            alert_type=self.alert_type,
            node=self.node,
            detail=self.detail,
            sim_time=sim_time,
            severity=self.severity,
            event=event,
            dst=self.dst,
        )


# ──────────────────────────────────────────────────────────────────────────────
# DigitalTwinGraph  — ground-truth topology loaded from shared volume
# ──────────────────────────────────────────────────────────────────────────────

class NewTransitionEvidenceStore:
    """Append-only durable JSONL evidence for new-transition activations."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def append(self, record: dict) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with self._lock:
            with open(self.path, "a", encoding="utf-8", newline="\n") as stream:
                stream.write(line + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    def read(self, limit: int = 1000) -> List[dict]:
        if not os.path.isfile(self.path):
            return []
        records: Deque[dict] = deque(maxlen=max(1, limit))
        with self._lock:
            with open(self.path, "r", encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, start=1):
                    if not line.strip():
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        log.warning(
                            "[NewTransitionEvidenceStore] Skipping malformed JSONL "
                            "line %s in %s",
                            line_number,
                            self.path,
                        )
        return list(records)


class DigitalTwinGraph:
    """
    Loads the Digital Twin graph from /data/dt_graph.json (written atomically
    by the simulation model container via DTGraphExporter).

    Retries on startup until the file appears or max_wait seconds elapse.
    Supports periodic background reload so updates from the DT container are
    picked up automatically.
    """

    def __init__(self, path: str = "/data/dt_graph.json"):
        self._path = path
        self.transitions: Dict[str, Set[str]] = defaultdict(set)
        self.counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._fingerprints: Set[Tuple] = set()
        self.loaded = False
        self._lock = threading.RLock()  # reentrant: summary_dict() calls get_routing_percentages() while holding the lock

    def load(self, max_wait: float = 120.0, poll_interval: float = 5.0) -> bool:
        """Poll until the file exists or max_wait seconds elapse. Returns True on success."""
        deadline = time.time() + max_wait
        while time.time() < deadline:
            if self._try_load():
                return True
            remaining = deadline - time.time()
            log.info(
                f"[DigitalTwinGraph] Waiting for {self._path} "
                f"({remaining:.0f}s remaining) ..."
            )
            time.sleep(poll_interval)
        log.warning(
            f"[DigitalTwinGraph] {self._path} not available after "
            f"{max_wait:.0f}s — detection continues without DT baseline."
        )
        return False

    def _try_load(self) -> bool:
        """Attempt a single load of the graph file; returns True if it succeeded."""
        try:
            with open(self._path) as f:
                data = json.load(f)
            new_transitions = defaultdict(set)
            for k, v in data.get("transitions", {}).items():
                new_transitions[k] = set(v)
            new_counts = defaultdict(lambda: defaultdict(int))
            for k, v in data.get("counts", {}).items():
                new_counts[k] = defaultdict(int, v)
            new_fingerprints = {tuple(p) for p in data.get("fingerprints", [])}
            with self._lock:
                self.transitions = new_transitions
                self.counts = new_counts
                self._fingerprints = new_fingerprints
                self.loaded = True
            log.info(
                f"[DigitalTwinGraph] Loaded: {len(self.transitions)} nodes, "
                f"{sum(len(v) for v in self.transitions.values())} edges, "
                f"{len(self._fingerprints)} path types"
            )
            return True
        except (FileNotFoundError, json.JSONDecodeError):
            return False

    def start_reload_thread(self, interval: float = 10.0) -> None:
        """Start a background daemon thread that reloads the graph file every `interval` seconds."""
        def _reload_loop() -> None:
            while True:
                time.sleep(interval)
                self._try_load()

        t = threading.Thread(target=_reload_loop, daemon=True, name="dt-graph-reload")
        t.start()
        log.info(f"[DigitalTwinGraph] Reload thread started (interval={interval}s)")

    def has_transition(self, src: str, dst: str) -> bool:
        with self._lock:
            return dst in self.transitions.get(src, set())

    def all_transitions(self) -> List[Tuple[str, str]]:
        with self._lock:
            return [
                (src, dst)
                for src, dsts in self.transitions.items()
                for dst in dsts
            ]

    def get_routing_percentages(self) -> Dict[str, Dict[str, float]]:
        with self._lock:
            return _routing_percentages(self.counts)

    def branching_nodes(self) -> List[str]:
        with self._lock:
            return [n for n, succs in self.transitions.items() if len(succs) > 1]

    def terminal_nodes(self) -> List[str]:
        """Return reference nodes that have incoming edges and no successors."""
        with self._lock:
            sources = set(self.transitions)
            destinations = {
                dst for successors in self.transitions.values() for dst in successors
            }
            return sorted(destinations - sources)

    def summary_dict(self) -> dict:
        with self._lock:
            return {
                "transitions": _sorted_transitions(self.transitions),
                "routing":     self.get_routing_percentages(),
                "path_types":  len(self._fingerprints),
                "loaded":      self.loaded,
            }


# ──────────────────────────────────────────────────────────────────────────────
# SinkScopedDigitalTwinGraph — per-sink live reference view
# ──────────────────────────────────────────────────────────────────────────────

class SinkScopedDigitalTwinGraph:
    """Live view of the reference graph containing only paths to one sink.

    Per-sink observed state needs a per-sink reference baseline. Otherwise a
    detector receiving paths for sink A interprets valid, disjoint paths to
    sink B as removed. The view is recomputed from the base graph on every
    call, so background graph reloads remain visible.

    A sink absent from the reference graph has no removal or routing baseline.
    Global transition membership is still used in that case so already-known
    shared path prefixes are not reported as additions.
    """

    def __init__(self, base: DigitalTwinGraph, sink_id: str):
        self._base = base
        self.sink_id = sink_id

    def _scope(self) -> Tuple[Set[Tuple[str, str]], bool]:
        edges = set(self._base.all_transitions())
        nodes = {node for edge in edges for node in edge}
        if self.sink_id not in nodes:
            return set(), False

        predecessors: Dict[str, Set[str]] = defaultdict(set)
        for src, dst in edges:
            predecessors[dst].add(src)

        reachable = {self.sink_id}
        pending = [self.sink_id]
        while pending:
            node = pending.pop()
            for predecessor in predecessors.get(node, set()):
                if predecessor not in reachable:
                    reachable.add(predecessor)
                    pending.append(predecessor)

        return {
            (src, dst) for src, dst in edges
            if src in reachable and dst in reachable
        }, True

    def has_transition(self, src: str, dst: str) -> bool:
        scoped_edges, sink_known = self._scope()
        if not sink_known:
            return self._base.has_transition(src, dst)
        return (src, dst) in scoped_edges

    def all_transitions(self) -> List[Tuple[str, str]]:
        scoped_edges, _sink_known = self._scope()
        return list(scoped_edges)

    def get_routing_percentages(self) -> Dict[str, Dict[str, float]]:
        scoped_edges, sink_known = self._scope()
        if not sink_known:
            return {}

        scoped_successors: Dict[str, Set[str]] = defaultdict(set)
        for src, dst in scoped_edges:
            scoped_successors[src].add(dst)

        global_routing = self._base.get_routing_percentages()
        result: Dict[str, Dict[str, float]] = {}
        for src, successors in scoped_successors.items():
            weights = {
                dst: global_routing.get(src, {}).get(dst, 0.0)
                for dst in successors
            }
            total = sum(weights.values())
            if total > 0:
                result[src] = {
                    dst: weight / total for dst, weight in weights.items()
                }
        return result

    def branching_nodes(self) -> List[str]:
        successors: Dict[str, Set[str]] = defaultdict(set)
        for src, dst in self.all_transitions():
            successors[src].add(dst)
        return [node for node, dsts in successors.items() if len(dsts) > 1]

    def summary_dict(self) -> dict:
        transitions: Dict[str, Set[str]] = defaultdict(set)
        for src, dst in self.all_transitions():
            transitions[src].add(dst)
        return {
            "sink_id": self.sink_id,
            "transitions": _sorted_transitions(transitions),
            "routing": self.get_routing_percentages(),
            "loaded": self._base.loaded,
        }


# RealSystemGraph — live graph built from real-system events
class RealSystemGraph:
    """Directed graph of transitions built from real-system item paths."""

    def __init__(self):
        self.transitions: Dict[str, Set[str]] = defaultdict(set)
        self.counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._path_fingerprints: Set[Tuple[str, ...]] = set()
        self.total_paths: int = 0

    def record_path(self, path: List[str]) -> None:
        """Record a completed item path, updating transitions, counts and fingerprints."""
        if len(path) < 2:
            return
        self.total_paths += 1
        self._path_fingerprints.add(tuple(path))
        for src, dst in _iter_edges(path):
            self.transitions[src].add(dst)
            self.counts[src][dst] += 1

    def get_routing_percentages(self) -> Dict[str, Dict[str, float]]:
        return _routing_percentages(self.counts)

    def summary_dict(self) -> dict:
        return {
            "transitions":      _sorted_transitions(self.transitions),
            "routing":          self.get_routing_percentages(),
            "total_paths":      self.total_paths,
            "unique_path_types": len(self._path_fingerprints),
        }


# ──────────────────────────────────────────────────────────────────────────────
# RoutingMonitor  — pure calculator: live vs. DT baseline percentages
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RoutingDeviation:
    """One (node → succ) branch's live-vs-baseline routing deviation."""
    node: str
    succ: str
    delta: float      # |live_pct - base_pct|
    live_pct: float
    base_pct: float
    samples: int      # total live items routed out of `node`


class RoutingMonitor:
    """
    Pure calculator. Maintains rolling routing counts and reports the deviation of
    every evaluable branch from the DT baseline. It holds **no alert state** — the
    detector owns raise/resolve reconciliation, so the same math can drive both.
    """

    def __init__(
        self,
        baseline: Dict[str, Dict[str, float]],
        threshold: float = 0.15,
        min_samples: int = 10,
        window_size: int = 50,
    ):
        if window_size < min_samples:
            raise ValueError("routing window size must be >= minimum routing samples")
        self.baseline = baseline
        self.threshold = threshold
        self.min_samples = min_samples
        self.window_size = window_size

        self.live_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._windows: Dict[str, Deque[str]] = defaultdict(
            lambda: deque(maxlen=self.window_size)
        )

    def update_baseline(self, baseline: Dict[str, Dict[str, float]]) -> None:
        """Replace the reference proportions after DT graph load or reload."""
        self.baseline = baseline

    def update_settings(
        self, *, threshold: float, min_samples: int, window_size: int
    ) -> None:
        """Apply routing settings while retaining the newest observations."""
        if window_size < min_samples:
            raise ValueError("routing window size must be >= minimum routing samples")
        self.threshold = threshold
        self.min_samples = min_samples
        if window_size == self.window_size:
            return
        self.window_size = window_size
        resized: Dict[str, Deque[str]] = defaultdict(
            lambda: deque(maxlen=self.window_size)
        )
        counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for node, old_window in self._windows.items():
            kept = list(old_window)[-window_size:]
            resized[node].extend(kept)
            for destination in kept:
                counts[node][destination] += 1
        self._windows = resized
        self.live_counts = counts

    def record_path(self, path: List[str]) -> None:
        """Add one path, evicting departures older than each node's rolling window."""
        for src, dst in _iter_edges(path):
            window = self._windows[src]
            if len(window) == self.window_size:
                expired = window[0]
                self.live_counts[src][expired] -= 1
                if self.live_counts[src][expired] <= 0:
                    del self.live_counts[src][expired]
            window.append(dst)
            self.live_counts[src][dst] += 1

    def deviations(self) -> List[RoutingDeviation]:
        """Return a RoutingDeviation for every evaluable (node, succ) branch.

        Emits a row for *all* branches at a branching node with enough live samples
        (``samples >= min_samples``), regardless of whether the deviation exceeds the
        threshold — the detector decides raise vs. resolve. The deviation math is
        unchanged from the previous implementation.
        """
        result: List[RoutingDeviation] = []
        for node, baseline_pcts in self.baseline.items():
            if len(baseline_pcts) < 2:
                continue
            live = self.live_counts.get(node, {})
            total_live = sum(live.values())
            if total_live < self.min_samples:
                continue
            for succ, base_pct in baseline_pcts.items():
                live_pct = live.get(succ, 0) / total_live
                delta = abs(live_pct - base_pct)
                result.append(RoutingDeviation(
                    node=node, succ=succ, delta=delta,
                    live_pct=live_pct, base_pct=base_pct, samples=total_live,
                ))
        return result

    def get_live_routing(self) -> Dict[str, Dict[str, float]]:
        return _routing_percentages(self.live_counts)


# ──────────────────────────────────────────────────────────────────────────────
# StructuralDriftDetector  — DT-based, no warmup
# ──────────────────────────────────────────────────────────────────────────────

class StructuralDriftDetector:
    """
    Consumes sink-entry telemetry events and compares each item path against the
    Digital Twin graph. Detection starts from the very first event.

    State machine, not an alert firehose. Each drift condition has a lifecycle:
    it becomes **active** once, may be **updated** (occurrence count) many times
    without emitting anything, and becomes **resolved** once. Alerts are *derived*
    notifications of those two lifecycle transitions (via ``ActiveCondition.to_alert``);
    they never drive the lifecycle. This is the single source of duplicate suppression
    — there is no cooldown.

    Usage:
        detector = StructuralDriftDetector(dt_graph=dt_graph)
        alerts = detector.ingest_event(event_dict)   # only state-change alerts
    """

    def __init__(
        self,
        dt_graph: DigitalTwinGraph,
        routing_threshold: float = 0.15,
        min_routing_samples: int = 10,
        routing_window_size: int = 50,
        removal_window: float = 300.0,
        activity_timeout: float = 120.0,
        require_recent_activity: bool = True,
        path_failure_enabled: bool = False,
        resolved_retention_window: float = 3600.0,
        resolved_max_items: int = 50,
    ):
        self._dt_graph   = dt_graph
        self._real_graph = RealSystemGraph()
        self._routing_monitor = RoutingMonitor(
            baseline=dt_graph.get_routing_percentages(),
            threshold=routing_threshold,
            min_samples=min_routing_samples,
            window_size=routing_window_size,
        )
        self._removal_window = removal_window
        self._activity_timeout = activity_timeout
        self._require_recent_activity = require_recent_activity
        self._path_failure_enabled = path_failure_enabled
        self._resolved_retention_window = resolved_retention_window
        self._resolved_max_items = resolved_max_items

        # Observed-structure liveness: expected/observed edge → last sim_time seen.
        # This is *observed* state (feeds resolution timing), not alert state.
        self._edge_last_seen: Dict[Tuple[str, str], float] = {}
        self._first_path_wall_time: Optional[float] = None
        self._last_path_wall_time: Optional[float] = None
        self._latest_sim_time: float = 0.0

        # Alert state — the single source of truth, all in one place.
        self._active: Dict[ConditionKey, ActiveCondition] = {}
        self._recently_resolved: Deque[dict] = deque()

        # History of state-change alerts only (small: one per active/resolve).
        self._alerts: List[DriftAlert] = []
        self._lock = threading.RLock()

    # ── public ────────────────────────────────────────────────────────────────

    def ingest_event(self, event: dict) -> List[DriftAlert]:
        """Process one telemetry event; return the state-change alerts it produced."""
        if event.get("entity_type") != "Sink" or event.get("event_type") != "entry":
            return []
        attrs     = event.get("attributes") or {}
        item_path = attrs.get("item_path")
        sim_time  = float(event.get("sim_time", 0))
        if not item_path:
            return []
        return self._handle_path(item_path, sim_time)

    def get_all_alerts(self) -> List[DriftAlert]:
        with self._lock:
            return list(self._alerts)

    def update_runtime_settings(self, settings: dict) -> None:
        """Apply detector tuning without discarding observations or alert state."""
        with self._lock:
            self._routing_monitor.update_settings(
                threshold=settings["routing_threshold"],
                min_samples=settings["min_routing_samples"],
                window_size=settings["routing_window_size"],
            )
            self._removal_window = settings["removal_window"]
            self._activity_timeout = settings["activity_timeout"]
            self._require_recent_activity = settings["require_recent_activity"]
            self._path_failure_enabled = settings.get(
                "path_failure_enabled", self._path_failure_enabled
            )
            self._resolved_retention_window = settings["resolved_retention_window"]
            self._resolved_max_items = settings["resolved_max_items"]

    def reconcile_periodic(
        self,
        now_monotonic: Optional[float] = None,
        sim_time_watermark: Optional[float] = None,
        activity_wall_time: Optional[float] = None,
        system_path_count: int = 0,
    ) -> List[DriftAlert]:
        """Evaluate silence conditions independently of local path arrivals.

        ``sim_time_watermark`` and ``activity_wall_time`` allow a multi-sink
        coordinator to supply system-wide progress. Thus, one sink can become
        silent while other sinks prove that the shared event-time clock and
        telemetry stream are still advancing. Direct single-sink callers retain
        the previous local-watermark behaviour when these arguments are omitted.
        """
        now = time.monotonic() if now_monotonic is None else now_monotonic
        with self._lock:
            global_evidence_ready = (
                sim_time_watermark is not None and system_path_count >= 5
            )
            if (
                self._last_path_wall_time is None
                and not global_evidence_ready
            ) or (
                self._real_graph.total_paths < 5
                and not global_evidence_ready
            ):
                return []
            reconciliation_time = self._latest_sim_time
            if sim_time_watermark is not None:
                reconciliation_time = max(
                    reconciliation_time,
                    float(sim_time_watermark),
                )

            # Unlike edge-removal reconciliation, a complete path failure must be
            # evaluated even when the telemetry stream itself has gone quiet.
            alerts = self._reconcile_path_failure(now, reconciliation_time)

            latest_activity = (
                self._last_path_wall_time
                if activity_wall_time is None
                else activity_wall_time
            )
            if (
                self._require_recent_activity
                and now - latest_activity > self._activity_timeout
            ):
                self._alerts.extend(alerts)
                return alerts
            # Wall time controls polling and stream-health gating only. Structural
            # silence is evaluated against event time so execution speed cannot
            # change detector semantics. In a multi-sink system, the coordinator's
            # watermark may advance after this particular sink becomes silent.
            alerts.extend(self._reconcile_removed(
                reconciliation_time,
                allow_never_seen=global_evidence_ready,
            ))
            alerts.extend(self._reconcile_new_resolutions(reconciliation_time))
            self._alerts.extend(alerts)
        for alert in alerts:
            log.info(f"[DriftDetector] {alert}")
        return alerts

    def summary(self) -> dict:
        breakdown: Dict[str, int] = defaultdict(int)
        for a in self._alerts:
            breakdown[a.alert_type] += 1

        # DT transitions not yet seen in real system
        dt_only = [
            f"{src} → {dst}"
            for src, dst in self._dt_graph.all_transitions()
            if dst not in self._real_graph.transitions.get(src, set())
        ]
        # Real transitions not in DT
        real_only = [
            f"{src} → {dst}"
            for src, dsts in self._real_graph.transitions.items()
            for dst in dsts
            if not self._dt_graph.has_transition(src, dst)
        ]

        with self._lock:
            active_drifts = [self._active_dict(c) for c in self._active.values()]
            recently_resolved = self._pruned_recently_resolved()

        return {
            # DT graph info
            "dt_graph":            self._dt_graph.summary_dict(),
            "dt_routing":          self._dt_graph.get_routing_percentages(),
            # Real system graph info
            "real_graph":          self._real_graph.summary_dict(),
            "live_routing":        self._routing_monitor.get_live_routing(),
            "routing_window_size": self._routing_monitor.window_size,
            "routing_window_samples": {
                node: sum(counts.values())
                for node, counts in self._routing_monitor.live_counts.items()
            },
            "removal_window":      self._removal_window,
            "activity_timeout":    self._activity_timeout,
            "require_recent_activity": self._require_recent_activity,
            "path_failure_enabled": self._path_failure_enabled,
            # Diff
            "dt_only_transitions": dt_only,
            "real_only_transitions": real_only,
            # Active / resolved drift state (the state-viewer source)
            "active_drifts":         active_drifts,
            "recently_resolved":     recently_resolved,
            "active_drift_count":    len(active_drifts),
            "resolved_drift_count":  len(recently_resolved),
            # Legacy fields kept for Streamlit compat
            "warmup_complete":     True,
            "total_paths_learned": self._real_graph.total_paths,
            "unique_path_types":   len(self._real_graph._path_fingerprints),
            "branching_nodes":     self._dt_graph.branching_nodes(),
            "graph_transitions":   _sorted_transitions(self._real_graph.transitions),
            "baseline_routing":    self._dt_graph.get_routing_percentages(),
            "total_alerts":        len(self._alerts),
            "alert_breakdown":     dict(breakdown),
        }

    # ── internal: reconciliation ────────────────────────────────────────────────

    def _handle_path(self, path: List[str], sim_time: float) -> List[DriftAlert]:
        """Update observed structure, reconcile drift conditions, emit state changes."""
        wall_now = time.monotonic()
        with self._lock:
            self._routing_monitor.update_baseline(
                self._dt_graph.get_routing_percentages()
            )
            self._real_graph.record_path(path)
            self._routing_monitor.record_path(path)
            if self._first_path_wall_time is None:
                self._first_path_wall_time = wall_now
            self._last_path_wall_time = wall_now
            self._latest_sim_time = max(self._latest_sim_time, sim_time)
            alerts: List[DriftAlert] = []
            path_failure_key = ConditionKey(PATH_FAILURE, "completed_path")
            if path_failure_key in self._active:
                alerts.append(self._resolve(
                    path_failure_key,
                    sim_time,
                    detail="Completed sink path observed again",
                ))
            # New-transition raises must run first — they refresh _edge_last_seen,
            # which the removed-transition and new-resolution sweeps then read.
            alerts.extend(self._reconcile_new_transitions(path, sim_time))
            alerts.extend(self._reconcile_routing(sim_time))
            alerts.extend(self._reconcile_removed(sim_time))
            alerts.extend(self._reconcile_new_resolutions(sim_time))
            self._alerts.extend(alerts)
        for a in alerts:
            log.info(f"[DriftDetector] {a}")
        return alerts

    def _reconcile_path_failure(
        self, wall_now: float, sim_time: float
    ) -> List[DriftAlert]:
        """Raise once when a previously active sink path is silent in wall time."""
        if not self._path_failure_enabled or self._last_path_wall_time is None:
            return []

        silence = wall_now - self._last_path_wall_time
        key = ConditionKey(PATH_FAILURE, "completed_path")
        if silence <= self._activity_timeout or key in self._active:
            return []

        return [self._activate(
            key,
            node="completed_path",
            dst=None,
            sim_time=sim_time,
            severity=CRITICAL,
            detail=(
                f"No completed sink path observed for {silence:.1f} wall-clock "
                f"seconds (timeout={self._activity_timeout:.1f}s); possible path, "
                "process, or telemetry failure"
            ),
        )]

    def _reconcile_new_transitions(self, path: List[str], sim_time: float) -> List[DriftAlert]:
        """Raise on first sight of a path edge absent from the DT graph; count repeats.

        Also refreshes observed-edge liveness for every path edge (used elsewhere for
        removed-transition and new-transition-resolution timing).
        """
        alerts: List[DriftAlert] = []
        for src, dst in _iter_edges(path):
            key = ConditionKey(NEW_TRANSITION, src, dst)
            if not self._dt_graph.has_transition(src, dst):
                if key in self._active:
                    self._touch(key, sim_time)          # observed again → count++, no alert
                else:
                    alerts.append(self._activate(
                        key, node=src, dst=dst, sim_time=sim_time, severity=CRITICAL,
                        detail=f"New transition {src} → {dst} (not in Digital Twin)",
                    ))
            elif key in self._active:
                alerts.append(self._resolve(
                    key,
                    sim_time,
                    detail=(
                        f"Transition {src} → {dst} is now present in the "
                        "Digital Twin baseline"
                    ),
                ))
            self._edge_last_seen[(src, dst)] = sim_time
        return alerts

    def _reconcile_removed(
        self, sim_time: float, allow_never_seen: bool = False
    ) -> List[DriftAlert]:
        """Raise/resolve REMOVED_TRANSITION for DT edges by their observed silence.

        Silence beyond ``removal_window`` raises (or counts, if already active); the
        edge reappearing (silence back within the window) resolves immediately.
        """
        alerts: List[DriftAlert] = []
        for src, dst in self._dt_graph.all_transitions():
            key = ConditionKey(REMOVED_TRANSITION, src, dst)
            last = self._edge_last_seen.get((src, dst))
            if last is None:
                # never seen in real system — wait for some data before judging
                if self._real_graph.total_paths < 5 and not allow_never_seen:
                    continue
                silence = sim_time
            else:
                silence = sim_time - last

            if silence > self._removal_window:
                if key in self._active:
                    self._touch(key, sim_time)
                else:
                    alerts.append(self._activate(
                        key, node=src, dst=dst, sim_time=sim_time, severity=WARNING,
                        detail=(
                            f"Transition {src} → {dst} is in Digital Twin "
                            "but not seen in real system for "
                            f"{silence:.0f} simulation-time units "
                            f"(window={self._removal_window:.0f} simulation-time units)"
                        ),
                    ))
            elif key in self._active:
                # edge reappeared → condition clears
                alerts.append(self._resolve(
                    key, sim_time,
                    detail=f"Transition {src} → {dst} seen again in real system",
                ))
        return alerts

    def _reconcile_new_resolutions(self, sim_time: float) -> List[DriftAlert]:
        """Resolve an unexpected edge once it has been silent for ``removal_window``.

        Symmetric to removed transitions but opposite polarity: for a NEW_TRANSITION,
        silence is what *clears* the condition (the unexpected edge stopped being used).
        """
        alerts: List[DriftAlert] = []
        for key, cond in list(self._active.items()):
            if key.alert_type != NEW_TRANSITION:
                continue
            if sim_time - cond.last_seen > self._removal_window:
                alerts.append(self._resolve(
                    key, sim_time,
                    detail=(
                        f"New transition {cond.node} → {cond.dst} no longer seen "
                        f"for {sim_time - cond.last_seen:.0f} simulation-time units"
                    ),
                ))
        return alerts

    def _reconcile_routing(self, sim_time: float) -> List[DriftAlert]:
        """Raise/resolve ROUTING_POLICY_CHANGE from the monitor's pure deviations."""
        alerts: List[DriftAlert] = []
        threshold = self._routing_monitor.threshold
        for dev in self._routing_monitor.deviations():
            key = ConditionKey(ROUTING_POLICY_CHANGE, dev.node, dev.succ)
            if dev.delta > threshold:
                detail = (
                    f"{dev.node} → {dev.succ}: DT={dev.base_pct:.1%}, "
                    f"live={dev.live_pct:.1%}, shift={dev.delta:+.1%} (n={dev.samples})"
                )
                if key in self._active:
                    self._touch(key, sim_time, detail=detail)
                else:
                    severity = CRITICAL if dev.delta > 2 * threshold else WARNING
                    alerts.append(self._activate(
                        key, node=dev.node, dst=dev.succ, sim_time=sim_time,
                        severity=severity, detail=detail,
                    ))
            elif key in self._active:
                alerts.append(self._resolve(
                    key, sim_time,
                    detail=(
                        f"{dev.node} → {dev.succ}: routing back within tolerance "
                        f"(live={dev.live_pct:.1%}, DT={dev.base_pct:.1%})"
                    ),
                ))
        return alerts

    # ── internal: lifecycle transitions (only these emit alerts) ────────────────

    def _activate(self, key: ConditionKey, *, node: str, dst: Optional[str],
                  sim_time: float, severity: str, detail: str) -> DriftAlert:
        """Insert a new active condition and derive its ACTIVE alert."""
        cond = ActiveCondition(
            alert_type=key.alert_type, node=node, dst=dst,
            since_sim_time=sim_time, since_wall_time=datetime.now(timezone.utc),
            last_seen=sim_time, count=1, severity=severity, detail=detail,
        )
        self._active[key] = cond
        return cond.to_alert(AlertEvent.ACTIVE, sim_time)

    def _touch(self, key: ConditionKey, sim_time: float, detail: Optional[str] = None) -> None:
        """Record another observation of an active condition — no alert emitted."""
        cond = self._active[key]
        cond.count += 1
        cond.last_seen = sim_time
        if detail is not None:
            cond.detail = detail

    def _resolve(self, key: ConditionKey, sim_time: float,
                 detail: Optional[str] = None) -> DriftAlert:
        """Remove an active condition, log it to recently-resolved, derive RESOLVED alert."""
        cond = self._active.pop(key)
        if detail is not None:
            cond.detail = detail
        resolved_wall = datetime.now(timezone.utc)
        self._recently_resolved.append({
            "alert_type":         cond.alert_type,
            "node":               cond.node,
            "dst":                cond.dst,
            "detail":             cond.detail,
            "count":              cond.count,
            "resolved_sim_time":  sim_time,
            "resolved_wall_time": resolved_wall,
        })
        return cond.to_alert(AlertEvent.RESOLVED, sim_time)

    # ── internal: presentation helpers ──────────────────────────────────────────

    @staticmethod
    def _active_dict(cond: ActiveCondition) -> dict:
        """Serialise an ActiveCondition for /summary (datetimes → ISO here only)."""
        return {
            "alert_type":     cond.alert_type,
            "node":           cond.node,
            "dst":            cond.dst,
            "detail":         cond.detail,
            "severity":       cond.severity,
            "count":          cond.count,
            "since_sim_time": cond.since_sim_time,
            "since_ts":       cond.since_wall_time.isoformat(),
        }

    def _pruned_recently_resolved(self) -> List[dict]:
        """Apply retention lazily and return the display list (call under lock).

        Kept only while BOTH within the most recent ``resolved_max_items`` AND newer
        than ``resolved_retention_window`` — dropped as soon as either bound excludes
        it. Mutates the deque in place (no background timer).
        """
        # Cap to the most recent N (drop oldest from the left).
        while len(self._recently_resolved) > self._resolved_max_items:
            self._recently_resolved.popleft()
        # Drop anything older than the retention window (oldest are leftmost).
        now = datetime.now(timezone.utc)
        while self._recently_resolved:
            age = (now - self._recently_resolved[0]["resolved_wall_time"]).total_seconds()
            if age > self._resolved_retention_window:
                self._recently_resolved.popleft()
            else:
                break
        return [
            {
                "alert_type":        r["alert_type"],
                "node":              r["node"],
                "dst":               r["dst"],
                "detail":            r["detail"],
                "count":             r["count"],
                "resolved_sim_time": r["resolved_sim_time"],
                "resolved_ts":       r["resolved_wall_time"].isoformat(),
            }
            for r in self._recently_resolved
        ]


# ──────────────────────────────────────────────────────────────────────────────
# MultiSinkDriftDetector  — one StructuralDriftDetector per sink
# ──────────────────────────────────────────────────────────────────────────────

class MultiSinkDriftDetector:
    """
    Routes sink-entry events to a per-sink StructuralDriftDetector.
    Each detector receives a live sink-reachable view of the shared reference
    graph, preventing disjoint paths to other sinks from becoming false removals.
    A shared event-time watermark lets active sinks advance silence detection for
    a previously observed sink that stops producing completed paths.
    """

    def __init__(
        self,
        dt_graph: DigitalTwinGraph,
        routing_threshold: float = 0.15,
        min_routing_samples: int = 10,
        routing_window_size: int = 50,
        removal_window: float = 300.0,
        activity_timeout: float = 120.0,
        require_recent_activity: bool = True,
        path_failure_enabled: bool = False,
        resolved_retention_window: float = 3600.0,
        resolved_max_items: int = 50,
        evidence_path: Optional[str] = None,
    ):
        self._dt_graph = dt_graph
        self._detector_kwargs = dict(
            routing_threshold=routing_threshold,
            min_routing_samples=min_routing_samples,
            routing_window_size=routing_window_size,
            removal_window=removal_window,
            activity_timeout=activity_timeout,
            require_recent_activity=require_recent_activity,
            path_failure_enabled=path_failure_enabled,
            resolved_retention_window=resolved_retention_window,
            resolved_max_items=resolved_max_items,
        )
        self._detectors: Dict[str, StructuralDriftDetector] = {}
        self._lock = threading.Lock()
        self._latest_sim_time: Optional[float] = None
        self._last_event_wall_time: Optional[float] = None
        self._total_completed_paths: int = 0
        self._evidence_store = (
            NewTransitionEvidenceStore(evidence_path) if evidence_path else None
        )

    def _get_or_create(self, sink_id: str) -> StructuralDriftDetector:
        with self._lock:
            if sink_id not in self._detectors:
                log.info(f"[MultiSinkDriftDetector] New sink registered: {sink_id}")
                scoped_graph = SinkScopedDigitalTwinGraph(self._dt_graph, sink_id)
                self._detectors[sink_id] = StructuralDriftDetector(
                    dt_graph=scoped_graph,
                    **self._detector_kwargs,
                )
            return self._detectors[sink_id]

    def ingest_event(self, event: dict) -> List[DriftAlert]:
        if event.get("entity_type") != "Sink" or event.get("event_type") != "entry":
            return []
        attrs = event.get("attributes") or {}
        if not attrs.get("item_path"):
            return []
        sim_time = float(event.get("sim_time", 0))
        with self._lock:
            self._total_completed_paths += 1
            if self._latest_sim_time is None:
                self._latest_sim_time = sim_time
            else:
                self._latest_sim_time = max(self._latest_sim_time, sim_time)
            self._last_event_wall_time = time.monotonic()
        sink_id = event.get("entity_id", "unknown_sink")
        detector = self._get_or_create(sink_id)
        alerts = detector.ingest_event(event)
        for a in alerts:
            a.sink_id = sink_id
            if (
                self._evidence_store is not None
                and a.alert_type == NEW_TRANSITION
                and a.event == AlertEvent.ACTIVE
            ):
                record = {
                    "alert_type": a.alert_type,
                    "event": a.event.value,
                    "severity": a.severity,
                    "source": a.node,
                    "destination": a.dst,
                    "sink_id": sink_id,
                    "item_id": attrs.get("item_id", ""),
                    "simulation_time": a.sim_time,
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                    "completed_item_path": list(attrs.get("item_path") or []),
                    "evidence": a.detail,
                }
                try:
                    self._evidence_store.append(record)
                except OSError:
                    log.exception(
                        "[MultiSinkDriftDetector] Could not persist new-transition "
                        "evidence"
                    )
        return alerts

    def get_evidence(self, limit: int = 1000) -> List[dict]:
        if self._evidence_store is None:
            return []
        try:
            return self._evidence_store.read(limit=limit)
        except OSError:
            log.exception(
                "[MultiSinkDriftDetector] Could not read new-transition evidence"
            )
            return []

    def update_runtime_settings(self, settings: dict) -> None:
        """Apply settings to current detectors and defaults for future sinks."""
        with self._lock:
            self._detector_kwargs.update(
                routing_threshold=settings["routing_threshold"],
                min_routing_samples=settings["min_routing_samples"],
                routing_window_size=settings["routing_window_size"],
                removal_window=settings["removal_window"],
                activity_timeout=settings["activity_timeout"],
                require_recent_activity=settings["require_recent_activity"],
                path_failure_enabled=settings.get("path_failure_enabled", True),
                resolved_retention_window=settings["resolved_retention_window"],
                resolved_max_items=settings["resolved_max_items"],
            )
            detectors = list(self._detectors.values())
        for detector in detectors:
            detector.update_runtime_settings(settings)

    def get_all_alerts(self) -> List[DriftAlert]:
        with self._lock:
            detectors = dict(self._detectors)
        alerts = []
        for sink_id, det in detectors.items():
            for a in det.get_all_alerts():
                a.sink_id = sink_id
                alerts.append(a)
        return alerts

    def reconcile_periodic(self, now_monotonic: Optional[float] = None) -> List[DriftAlert]:
        """Reconcile every sink against the shared event-time watermark."""
        # Register terminal nodes from the loaded reference even if a sink is
        # completely absent from real telemetry. This is required to detect a
        # full-path removal rather than silently ignoring an unseen sink.
        for sink_id in self._dt_graph.terminal_nodes():
            self._get_or_create(sink_id)
        with self._lock:
            detectors = dict(self._detectors)
            sim_time_watermark = self._latest_sim_time
            activity_wall_time = self._last_event_wall_time
            system_path_count = self._total_completed_paths
        alerts: List[DriftAlert] = []
        for sink_id, detector in detectors.items():
            for alert in detector.reconcile_periodic(
                now_monotonic,
                sim_time_watermark=sim_time_watermark,
                activity_wall_time=activity_wall_time,
                system_path_count=system_path_count,
            ):
                alert.sink_id = sink_id
                alerts.append(alert)
        return alerts

    def summary(self) -> dict:
        with self._lock:
            detectors = dict(self._detectors)
        return {sink_id: det.summary() for sink_id, det in detectors.items()}


# ──────────────────────────────────────────────────────────────────────────────
# RabbitMQ consumer
# ──────────────────────────────────────────────────────────────────────────────

class DriftDetectionConsumer:
    """
    Subscribes to the RabbitMQ "manufacturing" fanout exchange and feeds every
    event into the MultiSinkDriftDetector.
    """

    def __init__(self, detector: "MultiSinkDriftDetector", host: str = "rabbitmq",
                 alert_queue: str = "drift_alerts",
                 publish_max_retries: int = 3, publish_retry_delay: float = 1.0):
        self.detector = detector
        self.host = host
        self.alert_queue = alert_queue
        # Publish-side transport resilience — independent of drift semantics. Bounded
        # retry/backoff avoids hammering the broker during a transient outage.
        self.publish_max_retries = publish_max_retries
        self.publish_retry_delay = publish_retry_delay
        self._connection: Optional[pika.BlockingConnection] = None
        self._channel = None
        self._alert_channel = None
        self._stop_event = threading.Event()

    def connect(self, max_retries: int = 20, retry_delay: float = 5.0) -> None:
        """Connect to RabbitMQ, declaring the consume queue and alert queue. Retries on failure."""
        for attempt in range(1, max_retries + 1):
            try:
                self._connection = pika.BlockingConnection(
                    pika.ConnectionParameters(host=self.host)
                )
                self._channel = self._connection.channel()
                self._channel.exchange_declare(
                    exchange="manufacturing", exchange_type="fanout", durable=True
                )
                result = self._channel.queue_declare(queue="", exclusive=True)
                queue_name = result.method.queue
                self._channel.queue_bind(exchange="manufacturing", queue=queue_name)
                self._channel.basic_consume(
                    queue=queue_name,
                    on_message_callback=self._on_message,
                    auto_ack=True,
                )
                self._alert_channel = self._connection.channel()
                self._alert_channel.queue_declare(queue=self.alert_queue, durable=True)
                log.info(f"[DriftDetectionConsumer] Connected to RabbitMQ at {self.host}")
                return
            except pika.exceptions.AMQPConnectionError:
                log.warning(
                    f"[DriftDetectionConsumer] Attempt {attempt}/{max_retries} failed, "
                    f"retrying in {retry_delay}s..."
                )
                time.sleep(retry_delay)
        raise RuntimeError(
            f"Could not connect to RabbitMQ at {self.host} after {max_retries} attempts."
        )

    def start(self) -> None:
        log.info("[DriftDetectionConsumer] Consuming events...")
        self._channel.start_consuming()

    def start_periodic_reconciliation(
        self, interval: float, settings_path: Optional[str] = None
    ) -> None:
        """Run silence reconciliation periodically and publish state changes safely."""
        if interval <= 0:
            return

        def _loop() -> None:
            current_interval = interval
            next_run = time.monotonic() + current_interval
            last_settings_mtime: Optional[float] = None
            while not self._stop_event.wait(1.0):
                try:
                    if settings_path and os.path.exists(settings_path):
                        mtime = os.path.getmtime(settings_path)
                        if mtime != last_settings_mtime:
                            runtime = load_runtime_settings(settings_path)
                            self.detector.update_runtime_settings(runtime)
                            current_interval = runtime["reconciliation_interval"]
                            next_run = time.monotonic() + current_interval
                            last_settings_mtime = mtime
                            log.info(
                                "[DriftDetectionConsumer] Runtime settings applied "
                                f"(reconciliation interval={current_interval}s)"
                            )
                    now = time.monotonic()
                    if now < next_run:
                        continue
                    alerts = self.detector.reconcile_periodic()
                    next_run = now + current_interval
                    for alert in alerts:
                        if self._connection and self._connection.is_open:
                            self._connection.add_callback_threadsafe(
                                lambda current=alert: self._publish_alert(current)
                            )
                except Exception:
                    log.exception("[DriftDetectionConsumer] Periodic reconciliation failed")

        threading.Thread(
            target=_loop,
            daemon=True,
            name="structural-reconciliation",
        ).start()
        log.info(
            f"[DriftDetectionConsumer] Periodic reconciliation started "
            f"(interval={interval}s)"
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._channel:
            self._channel.stop_consuming()
        if self._connection and not self._connection.is_closed:
            self._connection.close()

    def _on_message(self, _ch, _method, _properties, body) -> None:
        try:
            event = json.loads(body)
            alerts = self.detector.ingest_event(event)
            for alert in alerts:
                self._publish_alert(alert)
        except json.JSONDecodeError as e:
            log.warning(f"[DriftDetectionConsumer] Bad JSON: {e}")
        except Exception as e:
            # log.exception attaches the traceback (called within the except block).
            log.exception(f"[DriftDetectionConsumer] Error: {e}")

    def _publish_alert(self, alert: DriftAlert) -> None:
        """Publish one state-change alert to the durable alert queue as JSON.

        Every existing field is byte-identical to before; the only schema evolution is
        the optional ``event`` field ("active"/"resolved"). On a transient broker
        failure this retries with backoff (bounded) and then gives up — the drift
        condition is already tracked in detector state, so a dropped publish never
        corrupts or duplicates state.
        """
        if not self._alert_channel:
            return
        payload = {
            "alert_type": alert.alert_type,
            "node":       alert.node,
            "detail":     alert.detail,
            "sim_time":   alert.sim_time,
            "severity":   alert.severity,
            "sink_id":    alert.sink_id,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "event":      alert.event.value,   # optional; existing consumers ignore it
        }
        body = json.dumps(payload)
        for attempt in range(1, self.publish_max_retries + 1):
            try:
                self._alert_channel.basic_publish(
                    exchange="",
                    routing_key=self.alert_queue,
                    body=body,
                    properties=pika.BasicProperties(delivery_mode=2),
                )
                return
            except (pika.exceptions.AMQPError, OSError) as e:
                log.warning(
                    f"[DriftDetectionConsumer] Publish attempt {attempt}/"
                    f"{self.publish_max_retries} failed: {e}"
                )
                if attempt < self.publish_max_retries:
                    time.sleep(self.publish_retry_delay * attempt)
        log.error(
            f"[DriftDetectionConsumer] Dropping alert after "
            f"{self.publish_max_retries} publish attempts: {alert}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# GraphStateServer  — HTTP endpoint so Streamlit can query graph state
# ──────────────────────────────────────────────────────────────────────────────

class GraphStateServer:
    """
    Lightweight HTTP server in a background thread.

        GET /summary   — MultiSinkDriftDetector.summary() as JSON
        GET /health    — {"status": "ok"}
    """

    def __init__(self, detector: "MultiSinkDriftDetector", port: int = 8000):
        self._detector = detector
        self._port = port

    def start(self) -> None:
        """Start the HTTP server on a background daemon thread."""
        detector = self._detector

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/summary":
                    body = json.dumps(detector.summary(), default=str).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/evidence":
                    body = json.dumps(
                        {"records": detector.get_evidence()}, ensure_ascii=False
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/health":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status": "ok"}')
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, fmt, *args) -> None:
                pass  # silence default request logging

        server = HTTPServer(("0.0.0.0", self._port), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        log.info(f"[GraphStateServer] Listening on port {self._port}")


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    """Collect runtime configuration, reading tunables from environment variables.

    Returned values match the constants previously hard-coded in the startup
    block; only the RabbitMQ/DT-graph timing values are environment-overridable.
    """
    def env_bool(name: str, default: bool) -> bool:
        value = os.environ.get(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    config = {
        # detector tuning
        "routing_threshold": float(os.environ.get("ROUTING_THRESHOLD", "0.15")),
        "min_routing_samples": int(os.environ.get("MIN_ROUTING_SAMPLES", "10")),
        "routing_window_size": int(os.environ.get("ROUTING_WINDOW_SIZE", "50")),
        "removal_window": float(os.environ.get("REMOVAL_WINDOW", "60.0")),
        "reconciliation_interval": float(
            os.environ.get("RECONCILIATION_INTERVAL", "5.0")
        ),
        "activity_timeout": float(os.environ.get("ACTIVITY_TIMEOUT", "120.0")),
        "require_recent_activity": env_bool("REQUIRE_RECENT_ACTIVITY", True),
        "path_failure_enabled": env_bool("PATH_FAILURE_ENABLED", True),
        # recently-resolved retention (dashboard): keep an item only while BOTH within
        # the most recent N AND newer than the window (whichever drops it first).
        "resolved_retention_window": float(os.environ.get("RESOLVED_RETENTION_WINDOW", "3600.0")),
        "resolved_max_items":        int(os.environ.get("RESOLVED_MAX_ITEMS", "50")),
        # rabbitmq
        "rabbitmq_host":       "rabbitmq",
        "alert_queue":         "drift_alerts",
        "rabbitmq_max_retries": int(os.environ.get("RABBITMQ_MAX_RETRIES", "30")),
        "rabbitmq_retry_delay": float(os.environ.get("RABBITMQ_RETRY_DELAY", "5.0")),
        # publish-side transport retry (independent of drift semantics)
        "publish_max_retries": int(os.environ.get("PUBLISH_MAX_RETRIES", "3")),
        "publish_retry_delay": float(os.environ.get("PUBLISH_RETRY_DELAY", "1.0")),
        # dt graph
        "dt_graph_path":       "/data/dt_graph.json",
        "dt_graph_max_wait":   float(os.environ.get("DT_GRAPH_MAX_WAIT", "120.0")),
        "dt_reload_interval":  float(os.environ.get("DT_GRAPH_RELOAD_INTERVAL", "10.0")),
        "evidence_path": os.environ.get(
            "NEW_TRANSITION_EVIDENCE_PATH",
            "/data/new_transition_evidence.jsonl",
        ),
        "poll_interval":       5.0,
        # http
        "http_port":           8000,
    }
    settings_path = os.environ.get(
        "DRIFT_SETTINGS_PATH", "/config/structural_drift_settings.json"
    )
    config.update(load_runtime_settings(settings_path, fallback=config))
    config["drift_settings_path"] = settings_path
    return config


def load_runtime_settings(path: str, fallback: Optional[dict] = None) -> dict:
    """Read a complete, validated tuning snapshot written by the Hub."""
    base = fallback or load_config()
    keys = (
        "routing_threshold", "min_routing_samples", "routing_window_size",
        "removal_window", "reconciliation_interval", "activity_timeout",
        "require_recent_activity", "path_failure_enabled", "resolved_retention_window",
        "resolved_max_items",
    )
    settings = {key: base[key] for key in keys}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        settings.update({key: saved[key] for key in keys if key in saved})
        settings["routing_threshold"] = float(settings["routing_threshold"])
        settings["min_routing_samples"] = int(settings["min_routing_samples"])
        settings["routing_window_size"] = int(settings["routing_window_size"])
        for key in (
            "removal_window", "reconciliation_interval", "activity_timeout",
            "resolved_retention_window",
        ):
            settings[key] = float(settings[key])
        settings["resolved_max_items"] = int(settings["resolved_max_items"])
        settings["require_recent_activity"] = bool(
            settings["require_recent_activity"]
        )
        settings["path_failure_enabled"] = bool(settings["path_failure_enabled"])
        if not 0 < settings["routing_threshold"] <= 1:
            raise ValueError("routing threshold must be in (0, 1]")
        if settings["min_routing_samples"] < 1:
            raise ValueError("minimum routing samples must be positive")
        if settings["routing_window_size"] < settings["min_routing_samples"]:
            raise ValueError("routing window must be >= minimum samples")
        if any(settings[key] <= 0 for key in (
            "removal_window", "reconciliation_interval", "activity_timeout",
            "resolved_retention_window",
        )):
            raise ValueError("timing settings must be positive")
        if settings["resolved_max_items"] < 1:
            raise ValueError("resolved alert limit must be positive")
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        log.exception("[DriftDetector] Ignoring invalid runtime settings file")
    return settings


def main() -> None:
    """Wire up the service and consume live events until interrupted."""
    logging.basicConfig(level=logging.INFO)
    cfg = load_config()

    # --- Step 1: create objects and start HTTP server immediately ---
    dt_graph = DigitalTwinGraph(path=cfg["dt_graph_path"])

    detector = MultiSinkDriftDetector(
        dt_graph                  = dt_graph,
        routing_threshold         = cfg["routing_threshold"],
        min_routing_samples       = cfg["min_routing_samples"],
        routing_window_size       = cfg["routing_window_size"],
        removal_window            = cfg["removal_window"],
        activity_timeout          = cfg["activity_timeout"],
        require_recent_activity   = cfg["require_recent_activity"],
        path_failure_enabled      = cfg["path_failure_enabled"],
        resolved_retention_window = cfg["resolved_retention_window"],
        resolved_max_items        = cfg["resolved_max_items"],
        evidence_path             = cfg["evidence_path"],
    )

    # Start HTTP server before blocking on DT graph load so Streamlit can connect immediately
    GraphStateServer(detector, port=cfg["http_port"]).start()

    # --- Step 2: load DT graph from shared volume ---
    dt_graph.load(max_wait=cfg["dt_graph_max_wait"], poll_interval=cfg["poll_interval"])
    dt_graph.start_reload_thread(interval=cfg["dt_reload_interval"])

    # --- Step 3: consume live events from RabbitMQ ---
    consumer = DriftDetectionConsumer(
        detector, host=cfg["rabbitmq_host"], alert_queue=cfg["alert_queue"],
        publish_max_retries=cfg["publish_max_retries"],
        publish_retry_delay=cfg["publish_retry_delay"],
    )
    consumer.connect(
        max_retries=cfg["rabbitmq_max_retries"], retry_delay=cfg["rabbitmq_retry_delay"]
    )
    consumer.start_periodic_reconciliation(
        cfg["reconciliation_interval"], cfg["drift_settings_path"]
    )

    try:
        consumer.start()
    except KeyboardInterrupt:
        consumer.stop()
        log.info("\n[DriftDetector] ══════ Final Summary ══════")
        pprint.pprint(detector.summary())


if __name__ == "__main__":
    main()
