"""Repeat the S4 phase C run for several seeds and score structural detection.

For each seed:
  1. restart the structural drift monitor so it starts with no state and wait
     until it is consuming, with the S4 graph and the runtime settings loaded;
  2. run S4_GT_phaseC.py in real time (one time unit per second) inside the
     real_system_simpy container, to S4_SIM_TIME (default 600);
  3. save the monitor's log for the run and score its alerts.

Scoring. The target is the withdrawal of line 00 at T_CHANGE = 150: its 22
transitions (src -> B_IN_00, the 20 inside Line_00, B_OUT_00 -> sink) should be
raised as removed_transition. Every other alert activation is a false alert,
split into start-up (before every line has completed an item, i.e. raised
before T_STARTUP) and the rest.

Run from the repository root on the host:
    python real_system_simpy/s4/run_phaseC_repeats.py            # seeds 0-9
    python real_system_simpy/s4/run_phaseC_repeats.py --seeds 0 1 --sim-time 400
"""
import argparse
import datetime as dt
import json
import os
import re
import statistics
import subprocess
import sys
import time

MONITOR = "structural_drift_detection_app"
PLANT = "real_system_simpy"
PHASE_C = "/app/real_system_simpy/s4/S4_GT_phaseC.py"
RESULTS_HOST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "phaseC_repeats")
RESULTS_CONTAINER = "/app/real_system_simpy/s4/results/phaseC_repeats"
T_CHANGE = 150.0
T_STARTUP = 75.0        # every line has completed an item by ~71 (20 seeds, from empty)
N_TARGET = 22

ALERT_RE = re.compile(
    r"\[DriftDetector\] \[(?P<sev>[A-Z]+)\] (?P<state>active|resolved) (?P<type>[a-z_]+) "
    r"@ (?P<node>[^:]+): (?P<detail>.*?)\s+\(sim_time=(?P<t>[-\d.]+)\)"
)
PAIR_RE = re.compile(r"(TOP\.\S+) → (TOP\.\S+?)[:\s]")


def sh(args, timeout=None, check=True):
    """Run a command without a shell; return stdout+stderr as text."""
    env = dict(os.environ, MSYS_NO_PATHCONV="1")   # keep container paths intact under Git Bash
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed ({proc.returncode}): {proc.stdout}{proc.stderr}")
    return proc.stdout + proc.stderr


def utc_now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def plant_busy():
    out = sh(["docker", "exec", PLANT, "sh", "-c",
              'for p in /proc/[0-9]*; do tr "\\0" " " < $p/cmdline 2>/dev/null; echo; done'],
             timeout=60, check=False)
    return any("S4_GT" in line and "for p in" not in line for line in out.splitlines())


def restart_monitor(timeout=300):
    since = utc_now()
    sh(["docker", "restart", MONITOR], timeout=120)
    deadline = time.time() + timeout
    while time.time() < deadline:
        log = sh(["docker", "logs", "--since", since, MONITOR], timeout=60, check=False)
        graph = re.findall(r"\[DigitalTwinGraph\] Loaded: (\d+) nodes, (\d+) edges", log)
        if ("Consuming events" in log and graph and graph[-1][1] == "220"
                and "reconciliation interval=1.0s" in log):
            return since
        time.sleep(3)
    raise RuntimeError("monitor did not become ready (consuming, 220-edge graph, 1.0 s interval)")


def is_target(src, dst):
    ids = (src or "") + " " + (dst or "")
    return ".Line_00." in ids or "B_IN_00" in ids or "B_OUT_00" in ids


def score(log):
    events = []
    for m in ALERT_RE.finditer(log):
        pair = PAIR_RE.search(m["detail"] + " ")
        src, dst = (pair.group(1), pair.group(2)) if pair else (m["node"], None)
        events.append({"sev": m["sev"], "state": m["state"], "type": m["type"],
                       "node": m["node"], "src": src, "dst": dst, "t": float(m["t"])})
    active = [e for e in events if e["state"] == "active"]
    target = [e for e in active if e["type"] == "removed_transition"
              and e["t"] >= T_CHANGE and is_target(e["src"], e["dst"])]
    target_pairs = {(e["src"], e["dst"]) for e in target}
    other = [e for e in active if e not in target]
    startup = [e for e in other if e["t"] < T_STARTUP]
    false_after = [e for e in other if e["t"] >= T_STARTUP]
    first = min((e["t"] for e in target), default=None)
    last = max((e["t"] for e in target), default=None)
    by_type = lambda es: {t: sum(e["type"] == t for e in es) for t in sorted({e["type"] for e in es})}  # noqa: E731
    return {
        "target_detected": len(target_pairs), "target_expected": N_TARGET,
        "first_detection": first, "last_detection": last,
        "latency": None if first is None else first - T_CHANGE,
        "path_failure": sum(e["type"] == "path_failure" for e in active),
        "startup_alerts": len(startup), "startup_by_type": by_type(startup),
        "false_alerts": len(false_after), "false_by_type": by_type(false_after),
        "false_details": [f"{e['type']} {e['src']}->{e['dst']} @ {e['t']:.1f}" for e in false_after][:20],
    }


def run_seed(seed, sim_time):
    if plant_busy():
        raise RuntimeError("an S4 run is already active in real_system_simpy; stop it first")
    since = restart_monitor()
    start = time.time()
    print(f"[seed {seed}] monitor ready; running phase C to {sim_time:g} in real time...", flush=True)
    sh(["docker", "exec",
        "-e", f"S4_SEED={seed}", "-e", f"S4_SIM_TIME={sim_time:g}", "-e", "S4_REALTIME_FACTOR=1",
        "-e", f"S4_SAMPLE_PATH={RESULTS_CONTAINER}/seed_{seed:02d}_timeseries.csv",
        PLANT, "python", "-u", PHASE_C], timeout=sim_time * 3 + 600)
    time.sleep(5)   # one more reconciliation pass after the last event
    log = sh(["docker", "logs", "--since", since, MONITOR], timeout=120, check=False)
    with open(os.path.join(RESULTS_HOST, f"seed_{seed:02d}_structural.log"), "w", encoding="utf-8") as fh:
        fh.write(log)
    result = {"seed": seed, "sim_time": sim_time, "wall_seconds": round(time.time() - start), **score(log)}
    print(f"[seed {seed}] detected {result['target_detected']}/{N_TARGET}, first at {result['first_detection']}, "
          f"latency {result['latency']}, start-up {result['startup_alerts']}, false {result['false_alerts']}",
          flush=True)
    return result


def summarise(results):
    lat = [r["latency"] for r in results if r["latency"] is not None]
    full = sum(r["target_detected"] == N_TARGET for r in results)
    print("\nseed  detected  first    latency  start-up  false")
    for r in results:
        print(f"{r['seed']:4d}  {r['target_detected']:3d}/{N_TARGET}  "
              f"{r['first_detection'] if r['first_detection'] is not None else '-':>7}  "
              f"{r['latency'] if r['latency'] is not None else '-':>7}  {r['startup_alerts']:8d}  {r['false_alerts']:5d}")
    if lat:
        print(f"\nall 22 transitions detected in {full}/{len(results)} runs; latency after the change: "
              f"mean {statistics.mean(lat):.1f}, min {min(lat):.1f}, max {max(lat):.1f}")
    print(f"start-up alerts: {sum(r['startup_alerts'] for r in results)}; "
          f"false alerts after start-up: {sum(r['false_alerts'] for r in results)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, nargs="+", default=list(range(10)))
    ap.add_argument("--sim-time", type=float, default=600.0)
    ap.add_argument("--score-log", help="only score an existing monitor log file, then exit")
    args = ap.parse_args()
    if args.score_log:
        print(json.dumps(score(open(args.score_log, encoding="utf-8").read()), indent=1))
        return
    os.makedirs(RESULTS_HOST, exist_ok=True)
    summary_path = os.path.join(RESULTS_HOST, "summary.json")
    results = []
    for seed in args.seeds:
        try:
            results.append(run_seed(seed, args.sim_time))
        except Exception as exc:   # record and continue with the next seed
            print(f"[seed {seed}] FAILED: {exc}", flush=True)
            results.append({"seed": seed, "error": str(exc)})
        with open(summary_path, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=1)
    summarise([r for r in results if "error" not in r])
    print(f"\nper-run logs and {summary_path}")


if __name__ == "__main__":
    sys.exit(main())
