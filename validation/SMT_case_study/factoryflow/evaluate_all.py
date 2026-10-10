"""Evaluate every generated SMT model and report the distribution.

One generation is an existence proof; the distribution over repeated generations is the
result. For each candidate this records whether it executes unrepaired, what failed if
not, and how far its KPIs sit from the published values.

Run:  python evaluate_all.py [generated_dir]
"""

import ast
import contextlib
import glob
import io
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "..", "src")))

import simpy

import smt_line_factorysimpy as smt


def load_system_model(path):
    """Execute only the definitions, dropping whatever driver the candidate wrote.

    Candidates vary in how they launch the simulation: some at module level, some
    under `if __name__ == "__main__":`. Keeping only the module-level imports, classes
    and functions removes the driver whatever shape it takes, without text surgery
    that depends on its wording.
    """
    with open(path, encoding="utf-8") as handle:
        source = handle.read()

    tree = ast.parse(source)
    tree.body = [
        node for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    namespace = {}
    exec(compile(tree, path, "exec"), namespace)
    cls, _name = find_system_model(namespace, tree)
    return cls, source


def find_system_model(namespace, tree):
    """Return the model class and its name, whatever the candidate called it.

    The examples name it SystemModel and the prompt asks for that, but a candidate
    may choose another name. Locating it by inspection rather than by name keeps a
    naming deviation from being reported as a failure to generate a model.
    """
    from factorysimpy.nodes.node import Node
    defined = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
    classes = [
        (name, namespace[name]) for name in defined
        if isinstance(namespace.get(name), type)
        and issubclass(namespace[name], Node) and namespace[name] is not Node
    ]
    if not classes:
        raise KeyError("no Node subclass defined in the candidate")
    for name, cls in classes:
        if name == "SystemModel":
            return cls, name
    return classes[0][0] and classes[0][1], classes[0][0]


def structural_summary(source):
    """Count the components and connections the candidate declares."""
    return {
        "nodes": len(re.findall(
            r"=\s*(?:BatchSource|Source|Machine|ServicedMachine|"
            r"PowerCycledMachine|HumanOperator|Sink)\(", source)),
        "buffers": len(re.findall(r"=\s*Buffer\(", source)),
        "belts": len(re.findall(r"=\s*ConveyorBelt\(", source)),
        "connects": len(re.findall(r"\.connect\(", source)),
        "ratings": len(re.findall(r"set_power_ratings\(", source)),
    }


def evaluate(path):
    # Label by folder/file, since each description's candidates are numbered
    # independently and would otherwise collide in the report.
    label = os.path.join(os.path.basename(os.path.dirname(path)),
                         os.path.basename(path))
    result = {"file": label}
    try:
        SystemModel, source = load_system_model(path)
        result.update(structural_summary(source))
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}".split("\n")[0][:90]
        return result

    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            env = simpy.Environment()
            top = SystemModel(env, "TOP")
            top.fill_hierarchical_id()
            sink = getattr(top, "sink", None) or getattr(top, "sink_1")

            defaults = {
                "screen_printer": smt.machine_power(smt.P_PRINTER_IDLE,
                                                    smt.P_PRINTER_PRINTING),
                "pick_and_place_1": smt.machine_power(smt.P_PP_IDLE,
                                                      smt.P_PP_PROCESSING),
                "pick_and_place_2": smt.machine_power(smt.P_PP_IDLE,
                                                      smt.P_PP_PROCESSING),
                "buffering_module": smt.machine_power(smt.P_BUFFERING_MODULE,
                                                      smt.P_BUFFERING_MODULE),
                "reflow_oven": smt.machine_power(smt.P_OVEN_MAINTAIN,
                                                 smt.P_OVEN_MAINTAIN,
                                                 setup=smt.P_OVEN_SETUP),
            }
            for name, ratings in defaults.items():
                machine = getattr(top, name, None)
                if machine is not None and not machine.power_ratings:
                    machine.set_power_ratings(ratings)

            end_time = smt.run_until_batch(env, top, sink)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}".split("\n")[0][:90]
        return result

    finished = sink.stats["num_item_received"]
    if not finished:
        result["error"] = "no boards finished"
        return result

    boards = top.screen_printer.stats["num_item_processed"]
    cleaning = smt.CLEANING_DELAY * (boards // smt.PCBS_PER_CLEANING)

    def component(machine):
        return sum(machine.get_energy_consumption().get(s, 0.0)
                   for s in smt.EXCLUSIVE_MACHINE_STATES)

    def pp(machine):
        wait = machine.stats.get("total_time_waiting_for_operator", 0.0)
        return component(machine) - (smt.P_PP_PROCESSING - smt.P_PP_IDLE) * wait

    printer_wait = top.screen_printer.stats.get("total_time_waiting_for_operator", 0.0)
    printer = (component(top.screen_printer)
               + (smt.P_PRINTER_CLEANING - smt.P_PRINTER_PRINTING) * cleaning
               - (smt.P_PRINTER_PRINTING - smt.P_PRINTER_IDLE) * printer_wait)
    module = component(top.buffering_module)

    # A PowerCycledMachine records warm-up as WARMUP_STATE and off time as OFF_STATE,
    # while the base class counts the same intervals as IDLE_STATE. Those states are
    # not in the exclusive set, so the intervals are re-priced here using whatever
    # rating the model itself declared for them.
    oven = top.reflow_oven
    states = oven.stats["total_time_spent_in_states"]
    ratings = oven.power_ratings
    idle_rate = ratings.get("IDLE_STATE", 0.0)
    warm_rate = ratings.get("WARMUP_STATE", ratings.get("SETUP_STATE", idle_rate))
    off_rate = ratings.get("OFF_STATE", idle_rate)
    warm_time = states.get("WARMUP_STATE", 0.0)
    off_time = states.get("OFF_STATE", 0.0)
    oven_energy = (component(oven)
                   + (warm_rate - idle_rate) * warm_time
                   + (off_rate - idle_rate) * off_time)

    total = (printer + pp(top.pick_and_place_1) + pp(top.pick_and_place_2)
             + module + oven_energy)

    warm = states.get("SETUP_STATE", 0.0) + warm_time
    ref = smt.REFERENCE

    def dev(model, paper):
        return (model - paper) / paper * 100.0 if paper else 0.0

    result.update({
        "ok": True,
        "throughput": dev(finished / end_time * 3600.0,
                          ref["throughput_pcb_per_hour"]),
        "max_cycle": dev(sink.stats["max_cycle_time"] / 3600.0,
                         ref["max_cycle_time_hours"]),
        "energy": dev((total - module) / finished / 1e3,
                      ref["avg_energy_per_pcb_kj"]),
        "warmup": dev(warm / end_time * 100.0, ref["rfo_setup_percent"]),
    })
    return result


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_HERE, "generated")
    paths = sorted(
        p for p in glob.glob(os.path.join(folder, "**", "generated_*.py"),
                             recursive=True)
        if "_repaired" not in p)
    if not paths:
        print(f"no candidates in {folder}")
        return 1

    results = [evaluate(p) for p in paths]

    print("=" * 96)
    print(f"{len(results)} generated candidates, deviation from the published KPIs (%)")
    print("=" * 96)
    print(f"{'candidate':16s}{'nodes':>6s}{'buf':>5s}{'belt':>5s}{'conn':>5s}"
          f"{'pwr':>5s}{'thrupt':>9s}{'maxcyc':>9s}{'energy':>9s}{'warmup':>9s}")
    print("-" * 96)
    for r in results:
        if r.get("ok"):
            print(f"{r['file']:16s}{r['nodes']:6d}{r['buffers']:5d}{r['belts']:5d}"
                  f"{r['connects']:5d}{r['ratings']:5d}"
                  f"{r['throughput']:8.2f}%{r['max_cycle']:8.2f}%"
                  f"{r['energy']:8.2f}%{r['warmup']:8.2f}%")
        else:
            counts = (f"{r.get('nodes','-'):>6}{r.get('buffers','-'):>5}"
                      f"{r.get('belts','-'):>5}{r.get('connects','-'):>5}"
                      f"{r.get('ratings','-'):>5}")
            print(f"{r['file']:16s}{counts}  FAILED: {r['error']}")
    print("-" * 96)

    ok = [r for r in results if r.get("ok")]
    print(f"executed unrepaired: {len(ok)}/{len(results)}")
    if ok:
        for key, label in (("throughput", "throughput"), ("max_cycle", "max cycle"),
                           ("energy", "energy"), ("warmup", "warm-up")):
            vals = [abs(r[key]) for r in ok]
            print(f"  |{label:11s}| deviation: min {min(vals):5.2f}%  "
                  f"median {sorted(vals)[len(vals)//2]:5.2f}%  max {max(vals):5.2f}%")
    for r in results:
        if not r.get("ok"):
            print(f"  {r['file']}: {r['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
