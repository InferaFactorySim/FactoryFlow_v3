"""Detection-quality metrics computed from a detector alert log + ground truth.

Ground truth for a trial is three sets (in hierarchical-id form):
    expected_add     : {(src, dst), ...}   edges that should raise new_transition
    expected_removed : {(src, dst), ...}   edges that should raise removed_transition
    expected_routing : {node, ...}         branch nodes that should raise routing_policy_change

An alert-log entry is a dict {sim_time, alert_type, event, severity, src, dst, node}
as produced by detector_io.run_detector.
"""
from statistics import mean, median

NEW = "new_transition"
REMOVED = "removed_transition"
ROUTING = "routing_policy_change"


def _active(alert_log, t_change, t_end=None):
    return [a for a in alert_log
            if a["event"] == "active"
            and a["sim_time"] >= t_change
            and (t_end is None or a["sim_time"] < t_end)]


def classify(expected_add, expected_removed, expected_routing, alert_log, t_change,
             t_end=None):
    """Return per-trial detection metrics for one trial."""
    active = _active(alert_log, t_change, t_end)

    detected_add, detected_rem, detected_route = set(), set(), set()
    fp_alerts = []
    for a in active:
        edge = (a["src"], a["dst"])
        if a["alert_type"] == NEW:
            (detected_add.add(edge) if edge in expected_add else fp_alerts.append(a))
        elif a["alert_type"] == REMOVED:
            (detected_rem.add(edge) if edge in expected_removed else fp_alerts.append(a))
        elif a["alert_type"] == ROUTING:
            (detected_route.add(a["src"]) if a["src"] in expected_routing else fp_alerts.append(a))

    tp = len(detected_add) + len(detected_rem) + len(detected_route)
    expected_total = len(expected_add) + len(expected_removed) + len(expected_routing)
    fn = expected_total - tp
    fp = len(fp_alerts)

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / expected_total if expected_total else 1.0

    # Earliest correct detection.
    matched_times = [a["sim_time"] for a in active
                     if (a["alert_type"] == NEW and (a["src"], a["dst"]) in expected_add)
                     or (a["alert_type"] == REMOVED and (a["src"], a["dst"]) in expected_removed)
                     or (a["alert_type"] == ROUTING and a["src"] in expected_routing)]
    latency = (min(matched_times) - t_change) if matched_times else None

    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": precision, "recall": recall,
        "expected_total": expected_total,
        "detection_latency": latency,
        "detected": tp == expected_total and fp == 0,
    }


def target_resolution_summary(expected_add, expected_removed, expected_routing,
                              alert_log, change_time, restoration_time):
    """Summarize exact target-condition resolution after baseline restoration."""
    targets = (
        {(NEW, src, dst) for src, dst in expected_add}
        | {(REMOVED, src, dst) for src, dst in expected_removed}
        | {(ROUTING, node, None) for node in expected_routing}
    )

    def canonical(alert):
        if alert["alert_type"] == ROUTING:
            return ROUTING, alert["src"], None
        return alert["alert_type"], alert["src"], alert["dst"]

    activated = {
        canonical(alert) for alert in alert_log
        if alert["event"] == "active"
        and change_time <= alert["sim_time"] < restoration_time
        and canonical(alert) in targets
    }

    resolved_times = {}
    for alert in alert_log:
        if alert["event"] != "resolved" or alert["sim_time"] < restoration_time:
            continue
        key = canonical(alert)
        if key in activated and key not in resolved_times:
            resolved_times[key] = alert["sim_time"]

    latencies = [time - restoration_time for time in resolved_times.values()]
    unresolved = activated - set(resolved_times)
    return {
        "target_conditions": len(targets),
        "activated_conditions": len(activated),
        "resolved_conditions": len(resolved_times),
        "unresolved_conditions": len(unresolved),
        "resolution_latency_mean": mean(latencies) if latencies else None,
        "resolution_latency_max": max(latencies) if latencies else None,
    }


def typed_latency(alert_log, expected_edges, alert_type, t_change):
    """Latency of the first correct alert of a specific type (e.g. removal latency)."""
    times = [a["sim_time"] for a in alert_log
             if a["event"] == "active" and a["sim_time"] >= t_change
             and a["alert_type"] == alert_type and (a["src"], a["dst"]) in expected_edges]
    return (min(times) - t_change) if times else None


def resolution_latency(alert_log, correction_time, alert_type=None):
    """Earliest 'resolved' event at/after correction_time, minus correction_time."""
    times = [a["sim_time"] for a in alert_log
             if a["event"] == "resolved" and a["sim_time"] >= correction_time
             and (alert_type is None or a["alert_type"] == alert_type)]
    return (min(times) - correction_time) if times else None


def alert_stability(alert_log, alert_type=None, src=None, dst=None):
    """Count active/resolved lifecycle transitions for a condition (flapping measure).

    Returns (n_active, n_resolved). A stable, correctly-handled condition yields
    (1, 0) while it persists or (1, 1) after recovery; larger counts = flapping.
    """
    def match(a):
        return ((alert_type is None or a["alert_type"] == alert_type)
                and (src is None or a["src"] == src)
                and (dst is None or a["dst"] == dst))
    n_active = sum(1 for a in alert_log if a["event"] == "active" and match(a))
    n_resolved = sum(1 for a in alert_log if a["event"] == "resolved" and match(a))
    return n_active, n_resolved


# ── aggregation helpers ───────────────────────────────────────────────────────

def summarise(trials):
    """Aggregate a list of per-trial classify() dicts."""
    lats = [t["detection_latency"] for t in trials if t["detection_latency"] is not None]
    n = len(trials)
    return {
        "trials": n,
        "recall": mean(t["recall"] for t in trials) if n else 0.0,
        "precision": mean(t["precision"] for t in trials) if n else 0.0,
        "fp_per_trial": mean(t["fp"] for t in trials) if n else 0.0,
        "fn_rate": mean(t["fn"] / t["expected_total"] if t["expected_total"] else 0.0
                        for t in trials) if n else 0.0,
        "detected_frac": mean(1.0 if t["detected"] else 0.0 for t in trials) if n else 0.0,
        "latency_mean": mean(lats) if lats else None,
        "latency_median": median(lats) if lats else None,
        "latency_worst": max(lats) if lats else None,
    }


def detection_rate_curve(latencies, taus):
    """P(detected by elapsed time tau) for a list of per-trial latencies (None = never)."""
    n = len(latencies)
    if not n:
        return [(tau, 0.0) for tau in taus]
    return [(tau, sum(1 for l in latencies if l is not None and l <= tau) / n) for tau in taus]


def min_detectable(magnitudes, recalls, target=0.95):
    """Smallest magnitude whose recall >= target (magnitudes/recalls are paired, sorted)."""
    for m, r in sorted(zip(magnitudes, recalls)):
        if r >= target:
            return m
    return None
