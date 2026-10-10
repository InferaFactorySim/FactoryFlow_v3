"""Evaluate a generated SMT model against the paper's KPIs.

The generated file builds and runs its own model at import time, so only the class
definition is executed here; the run is then driven to the reference's terminating
condition of 1024 boards using the same harness as the hand-built model.

Power ratings are applied here rather than in the generated model, because the
generator was given no way to declare them.

Run:  python evaluate_generated.py generated/generated_1_repaired.py
"""

import ast
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "..", "src")))

import simpy

import smt_line_factorysimpy as smt


def load_system_model(path):
    """Exec only the definitions, then locate the model class by inspection.

    Candidates differ in how they launch the simulation and in what they call the
    model class, so the driver is dropped via the AST and the class is found by
    looking for a Node subclass. This matches evaluate_all.py and
    check_structure.py, so the three scripts cannot disagree.
    """
    from factorysimpy.nodes.node import Node

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
            return cls
    print(f"naming deviation: model class is '{classes[0][0]}', "
          f"the examples use 'SystemModel'")
    return classes[0][1]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        _HERE, "generated", "generated_1_repaired.py")

    SystemModel = load_system_model(path)
    env = simpy.Environment()
    top = SystemModel(env, "TOP")
    top.fill_hierarchical_id()

    sink = getattr(top, "sink", None) or getattr(top, "sink_1")

    # Power ratings are used as generated when the model declares them; they are only
    # supplied here for a model that does not, so the two cases stay distinguishable.
    defaults = {
        "screen_printer": smt.machine_power(smt.P_PRINTER_IDLE,
                                            smt.P_PRINTER_PRINTING),
        "pick_and_place_1": smt.machine_power(smt.P_PP_IDLE, smt.P_PP_PROCESSING),
        "pick_and_place_2": smt.machine_power(smt.P_PP_IDLE, smt.P_PP_PROCESSING),
        "buffering_module": smt.machine_power(smt.P_BUFFERING_MODULE,
                                              smt.P_BUFFERING_MODULE),
        "reflow_oven": smt.machine_power(smt.P_OVEN_MAINTAIN, smt.P_OVEN_MAINTAIN,
                                         setup=smt.P_OVEN_SETUP),
    }
    supplied = []
    for name, ratings in defaults.items():
        machine = getattr(top, name)
        if not machine.power_ratings:
            machine.set_power_ratings(ratings)
            supplied.append(name)
    print("power ratings: "
          + ("declared by the generated model"
             if not supplied else f"supplied here for {', '.join(supplied)}"))

    end_time = smt.run_until_batch(env, top, sink)
    finished = sink.stats["num_item_received"]
    if not finished:
        print("no boards finished")
        return 1

    boards = top.screen_printer.stats["num_item_processed"]
    cleaning = smt.CLEANING_DELAY * (boards // smt.PCBS_PER_CLEANING)
    printer_wait = top.screen_printer.stats["total_time_waiting_for_operator"]

    def component(machine):
        return sum(machine.get_energy_consumption().get(state, 0.0)
                   for state in smt.EXCLUSIVE_MACHINE_STATES)

    printer = (component(top.screen_printer)
               + (smt.P_PRINTER_CLEANING - smt.P_PRINTER_PRINTING) * cleaning
               - (smt.P_PRINTER_PRINTING - smt.P_PRINTER_IDLE) * printer_wait)

    def pp(machine):
        wait = machine.stats["total_time_waiting_for_operator"]
        return component(machine) - (smt.P_PP_PROCESSING - smt.P_PP_IDLE) * wait

    module = component(top.buffering_module)

    # A PowerCycledMachine records warm-up as WARMUP_STATE and off time as OFF_STATE,
    # while the base class counts the same intervals as IDLE_STATE. Neither is in the
    # exclusive set, so those intervals are re-priced using the ratings the model
    # itself declared. This mirrors evaluate_all.py, so the two agree.
    oven = top.reflow_oven
    states = oven.stats["total_time_spent_in_states"]
    ratings = oven.power_ratings
    idle_rate = ratings.get("IDLE_STATE", 0.0)
    warm_rate = ratings.get("WARMUP_STATE", ratings.get("SETUP_STATE", idle_rate))
    off_rate = ratings.get("OFF_STATE", idle_rate)
    warm_time = states.get("WARMUP_STATE", 0.0)
    oven_energy = (component(oven)
                   + (warm_rate - idle_rate) * warm_time
                   + (off_rate - idle_rate) * states.get("OFF_STATE", 0.0))

    energy_total = (printer + pp(top.pick_and_place_1) + pp(top.pick_and_place_2)
                    + module + oven_energy)

    # A plain Machine records its start-up warm-up as SETUP_STATE; a
    # PowerCycledMachine records warm-ups as WARMUP_STATE. Either is the oven's
    # warm-up, so both count.
    setup = states.get("SETUP_STATE", 0.0) + warm_time
    ref = smt.REFERENCE

    rows = [
        ("Throughput (boards/h)", ref["throughput_pcb_per_hour"],
         finished / end_time * 3600.0),
        ("Max cycle time (h)", ref["max_cycle_time_hours"],
         sink.stats["max_cycle_time"] / 3600.0),
        ("Energy per board (kJ)", ref["avg_energy_per_pcb_kj"],
         energy_total / finished / 1e3),
        ("Energy, excl. bypass (kJ)", ref["avg_energy_per_pcb_kj"],
         (energy_total - module) / finished / 1e3),
        ("Oven setup (%)", ref["rfo_setup_percent"], setup / end_time * 100.0),
        ("Mean cycle time (h)", ref["avg_cycle_time_hours"],
         (sink.stats["total_cycle_time"] / finished) / 3600.0),
    ]

    print("\n" + "=" * 66)
    print(f"Generated model: {os.path.basename(path)}")
    print("=" * 66)
    print(f"boards {finished}, elapsed {end_time:.1f} s")
    print(f"operator {top.operator_1.stats['num_tasks_completed']} tasks, "
          f"queued wait {top.operator_1.stats['total_wait_time']:.1f} s")
    print("-" * 66)
    print(f"{'KPI':28s}{'paper':>12s}{'generated':>12s}{'dev':>11s}")
    print("-" * 66)
    for label, paper, model in rows:
        dev = (model - paper) / paper * 100.0 if paper else 0.0
        print(f"{label:28s}{paper:12.4f}{model:12.4f}{dev:10.2f}%")
    print("-" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
