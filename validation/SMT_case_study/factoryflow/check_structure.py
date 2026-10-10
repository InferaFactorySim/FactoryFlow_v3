"""Compare a generated model's structure with the reference reconstruction.

Counting components is not a structural check: a model can have the right number of
buffers and the wrong wiring. This builds both models and compares the actual graphs -
node ids and types, edge ids and types, and each edge's endpoints - then reports every
difference.

Run:  python check_structure.py generated/generated_1.py [...]
"""

import ast
import contextlib
import glob
import io
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "..", "src")))

import simpy

import smt_line_factorysimpy as smt

# Components whose class differs between the two constructs that express a
# warm-up-once heated chamber. They are behaviourally equivalent since
# PowerCycledMachine gained its intake gate, so a substitution here is reported as
# equivalent rather than as a structural difference.
EQUIVALENT_TYPES = {frozenset({"Machine", "PowerCycledMachine"})}


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


def graph_of(top):
    """Return (nodes, edges) as {id: type} and {id: (type, src, dest)}."""
    nodes, edges = top.get_all_nodes_edges()
    node_map = {}
    for node in nodes.values():
        if node is top:
            continue
        node_map[node.id] = type(node).__name__
    edge_map = {}
    for edge in edges.values():
        src = edge.src_node.id if edge.src_node is not None else None
        dest = edge.dest_node.id if edge.dest_node is not None else None
        edge_map[edge.id] = (type(edge).__name__, src, dest)
    return node_map, edge_map


def load(path):
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    tree = ast.parse(source)
    tree.body = [n for n in tree.body
                 if isinstance(n, (ast.Import, ast.ImportFrom, ast.ClassDef,
                                   ast.FunctionDef, ast.AsyncFunctionDef))]
    namespace = {}
    exec(compile(tree, path, "exec"), namespace)
    cls, name = find_system_model(namespace, tree)
    return cls, name


def build(factory):
    with contextlib.redirect_stdout(io.StringIO()):
        env = simpy.Environment()
        top = factory(env)
        top.fill_hierarchical_id()
    return top


def compare(path):
    print("=" * 78)
    print(os.path.basename(path))
    print("=" * 78)

    reference = build(lambda env: smt.SMTLine(env, "TOP"))
    ref_nodes, ref_edges = graph_of(reference)

    try:
        SystemModel, class_name = load(path)
        candidate = build(lambda env: SystemModel(env, "TOP"))
        if class_name != "SystemModel":
            print(f"  naming deviation: model class is '{class_name}', "
                  f"the examples use 'SystemModel'")
    except Exception as exc:
        print(f"  could not build: {type(exc).__name__}: {exc}".split("\n")[0])
        return False

    cand_nodes, cand_edges = graph_of(candidate)
    problems = []

    missing = sorted(set(ref_nodes) - set(cand_nodes))
    extra = sorted(set(cand_nodes) - set(ref_nodes))
    if missing:
        problems.append(f"missing nodes: {missing}")
    if extra:
        problems.append(f"unexpected nodes: {extra}")

    equivalent = []
    for node_id in sorted(set(ref_nodes) & set(cand_nodes)):
        ref_type, cand_type = ref_nodes[node_id], cand_nodes[node_id]
        if ref_type != cand_type:
            if frozenset({ref_type, cand_type}) in EQUIVALENT_TYPES:
                equivalent.append(f"{node_id}: {ref_type} -> {cand_type}")
            else:
                problems.append(
                    f"node '{node_id}' type {cand_type}, expected {ref_type}")

    missing_e = sorted(set(ref_edges) - set(cand_edges))
    extra_e = sorted(set(cand_edges) - set(ref_edges))
    if missing_e:
        problems.append(f"missing edges: {missing_e}")
    if extra_e:
        problems.append(f"unexpected edges: {extra_e}")

    for edge_id in sorted(set(ref_edges) & set(cand_edges)):
        ref_type, ref_src, ref_dest = ref_edges[edge_id]
        cand_type, cand_src, cand_dest = cand_edges[edge_id]
        if (ref_src, ref_dest) != (cand_src, cand_dest):
            problems.append(
                f"edge '{edge_id}' wires {cand_src}->{cand_dest}, "
                f"expected {ref_src}->{ref_dest}")
        if ref_type != cand_type:
            problems.append(
                f"edge '{edge_id}' type {cand_type}, expected {ref_type}")

    print(f"  reference : {len(ref_nodes)} nodes, {len(ref_edges)} edges")
    print(f"  candidate : {len(cand_nodes)} nodes, {len(cand_edges)} edges")
    for note in equivalent:
        print(f"  equivalent substitution: {note}")
    if problems:
        for problem in problems:
            print(f"  DIFFERENCE: {problem}")
        print("  structurally different")
        return False
    print("  structurally identical to the reference")
    return True


def main():
    paths = sys.argv[1:] or sorted(
        p for p in glob.glob(os.path.join(_HERE, "generated", "**",
                                          "generated_*.py"), recursive=True)
        if "_repaired" not in p)
    results = [compare(p) for p in paths]
    print("=" * 78)
    print(f"structurally identical: {sum(results)}/{len(results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
