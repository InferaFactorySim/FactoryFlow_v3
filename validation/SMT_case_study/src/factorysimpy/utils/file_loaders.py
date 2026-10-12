import os, sys, types
from .utils import get_edge_selector
#sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), './IM')))
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

GENCODE_PATH = os.path.abspath(
    os.path.join(CURRENT_DIR, "..", "..", "..", "IM", "gencodeforAMG")
)

# Directory where DataFITR-generated random variate generator (RVG) modules live at runtime.
# Defaults to the historical FactoryFlow location (GENCODE_PATH); the Digital Twin sets
# DELAY_PLUGIN_DIR=/plugins (a read-only mount of param_calib/data) to consume calibrated plugins.
DEFAULT_PLUGIN_DIR = os.environ.get("DELAY_PLUGIN_DIR", GENCODE_PATH)

# Supported plugin API versions (modules may declare PLUGIN_API_VERSION).
SUPPORTED_API_VERSIONS = (1,)

# Cache of imported plugin modules keyed by absolute file path (parsed once per process).
_module_cache = {}

# DataFITR filename suffix -> FactorySimPy node attribute. Keep longer/more
# specific suffixes first when adding aliases.
DATAFITR_PARAMETER_SUFFIXES = {
    "_process_delay.py": "processing_delay",
    "_processing_delay.py": "processing_delay",
    "_selected_in_edge.py": "in_edge_selection",
    "_selected_out_edge.py": "out_edge_selection",
    "_in_edge_selection.py": "in_edge_selection",
    "_out_edge_selection.py": "out_edge_selection",
    "_inter_arrival_time.py": "inter_arrival_time",
}

# NOTE: Keep this file byte-for-byte identical with the copy in the other factorysimpy
# source tree (src/ and FactoryFlow/src/) until factorysimpy is packaged.


def _import_module(file_path):
    """Import an RVG module by path exactly once and cache the module object (or None)."""
    if file_path in _module_cache:
        return _module_cache[file_path]
    module = None
    if os.path.exists(file_path):
        try:
            safe = "".join(c if c.isalnum() else "_" for c in os.path.basename(file_path))
            module = types.ModuleType(f"rvg_{safe}")
            module.__file__ = file_path
            with open(file_path, "r", encoding="utf-8") as stream:
                source = stream.read()
            exec(compile(source, file_path, "exec"), module.__dict__)
            if getattr(module, "PLUGIN_API_VERSION", 1) not in SUPPORTED_API_VERSIONS:
                print(f"[file_loaders] {file_path}: unsupported PLUGIN_API_VERSION="
                      f"{getattr(module, 'PLUGIN_API_VERSION', None)}")
                module = None
        except Exception as exc:          # never let a bad plugin crash the caller
            print(f"[file_loaders] failed to load {file_path}: {exc}")
            module = None
    _module_cache[file_path] = module
    return module


def load_func_from_file(file_name, default=None, delay_folder=None):
    """Return a FRESH delay generator/callable from a generated RVG module, else ``default``.

    This is the single indirection point that hides plugin discovery from callers: the
    generated simulation model just names the file it wants and supplies a constant fallback
    (``default``); this helper knows the plugin directory, does the dynamic import, resolves
    the plugin interface, and handles missing/broken files. It NEVER raises.

    Interface resolution inside the module:
        create_generator()  (new standard)  ->  generate_data() / generate_*()  (legacy)

    The plugin MODULE is cached (parsed once), but the factory is called on every request so
    each machine / trial receives its own generator instance (no shared RNG state).

    Args:
        file_name:    RVG module filename, e.g. "TOP.M[0]_process_delay.py".
        default:      value returned when the plugin is absent, invalid, or errors.
        delay_folder: override the search directory (defaults to DEFAULT_PLUGIN_DIR).
    """
    folder = delay_folder or DEFAULT_PLUGIN_DIR
    module = _import_module(os.path.join(folder, file_name))
    if module is None:
        return default
    try:
        factory = getattr(module, "create_generator", None)             # new standard
        if factory is None:                                             # legacy fallback
            for name in ("generate_data", *(n for n in dir(module) if n.startswith("generate_"))):
                candidate = getattr(module, name, None)
                if callable(candidate):
                    factory = candidate
                    break
        if factory is None:
            print(f"[file_loaders] {file_name}: no create_generator/generate_* — using default")
            return default
        gen = factory()
        if hasattr(gen, "__next__") or callable(gen):     # iterator or callable (get_delay-compatible)
            return gen
        print(f"[file_loaders] {file_name}: factory returned non-iterator — using default")
        return default
    except Exception as exc:
        print(f"[file_loaders] error creating generator from {file_name}: {exc}")
        return default


def load_policy_from_file(file_name, delay_folder=None):
    """Return a validated edge policy stored in a DataFITR plugin, else None."""
    folder = delay_folder or DEFAULT_PLUGIN_DIR
    module = _import_module(os.path.join(folder, file_name))
    policy = getattr(module, "POLICY", None) if module is not None else None
    return policy if policy in {"RANDOM", "FIRST_AVAILABLE", "ROUND_ROBIN"} else None


def materialize_edge_policy(policy, node, attribute):
    """Convert a stored policy name to the value expected by a live node."""
    if policy == "FIRST_AVAILABLE":
        return policy
    edge_type = "IN" if attribute == "in_edge_selection" else "OUT"
    return get_edge_selector(policy, node, node.env, edge_type)


def apply_datafitr_generators(top, delay_folder=None):
    """Attach every recognized DataFITR plugin to its matching node attribute.

    Files use ``<hierarchical_id><parameter suffix>`` names. This helper is
    intended to run after ``top.fill_hierarchical_id()`` and before simulation.
    Unknown files, nodes, and attributes are ignored.

    Returns a list of hierarchical IDs that were updated.
    """
    folder = delay_folder or DEFAULT_PLUGIN_DIR
    try:
        file_names = os.listdir(folder)
    except OSError:
        return []

    try:
        nodes, _ = top.get_all_nodes_edges()
    except Exception as exc:
        print(f"[file_loaders] cannot inspect model nodes: {exc}")
        return []

    applied = []
    for file_name in sorted(file_names):
        match = next(
            ((suffix, attribute) for suffix, attribute in DATAFITR_PARAMETER_SUFFIXES.items()
             if file_name.endswith(suffix)),
            None,
        )
        if match is None:
            continue
        suffix, attribute = match
        hierarchical_id = file_name[:-len(suffix)]
        node = nodes.get(hierarchical_id)
        if node is None or not hasattr(node, attribute):
            continue
        current = getattr(node, attribute)
        if attribute in {"in_edge_selection", "out_edge_selection"}:
            policy = load_policy_from_file(file_name, delay_folder=folder)
            if policy is not None:
                loaded = materialize_edge_policy(policy, node, attribute)
                setattr(node, attribute, loaded)
                applied.append(f"{hierarchical_id}.{attribute}")
                continue
        loaded = load_func_from_file(file_name, default=current, delay_folder=folder)
        if loaded is not current:
            if attribute in {"in_edge_selection", "out_edge_selection"}:
                edge_count = len(node.in_edges if attribute == "in_edge_selection" else node.out_edges)
                loaded = (
                    (int(value) % edge_count for value in loaded)
                    if hasattr(loaded, "__next__")
                    else (lambda fn=loaded, count=edge_count: int(fn()) % count)
                )
            setattr(node, attribute, loaded)
            applied.append(f"{hierarchical_id}.{attribute}")
    return applied


def clear_cache():
    """Drop cached plugin modules (e.g. after DataFITR re-fits during a long-lived UI session)."""
    _module_cache.clear()


#load_func_from_file("M1_processing_delay_code.py")
