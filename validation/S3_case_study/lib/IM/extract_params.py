#!/usr/bin/env python3
"""
extract_params.py
─────────────────
Walk a folder, read every *.py* file, and build a list of tuples

    (filename, variable_name, kind, params_dict)

kind == '<distribution>' → params like {'args': (...,), 'loc': 4.1, 'scale': 1.09}
kind == 'constant'       → params like {'value': 100}

• Works inside functions (e.g. your generate_data()) as well as module level.
• Substitutes constant names (lowerlimit, upperlimit, …) with their numeric value.
• Uses `importlib.util` only to *optionally* import the file (not required to run).
• No `from __future__` import needed – fully 3.7-compatible.
"""
import ast
import pathlib
import re
from typing import Any, Dict, List, Tuple
import os
from pathlib import Path

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union   # ← add Union

# ─────────────────────────────── CONFIG ────────────────────────────────
SOURCE_DIR = pathlib.Path("./data_files")     # ◀️  change to your folder
FILE_GLOBS = ("*.py",)

LIB_PREFIX_RE = r"(?:\w+\.)*?"               # accepts scipy.stats.   or stats.
ASSIGN_DIST_RE = re.compile(
    rf"""
    ^\s*(?P<var>[A-Za-z_]\w*)\s*=\s*
    {LIB_PREFIX_RE}(?P<dist>[A-Za-z_]\w+)\s*\(
        (?P<args>
            [^()]* (?:\([^()]*\)[^()]*)*
        )
    \)
    """,
     re.VERBOSE | re.MULTILINE,   # ← add re.MULTILINE
)

ASSIGN_CONST_RE = re.compile(
    r"""
    ^\s*(?P<var>[A-Za-z_]\w*)\s*=\s*
    (?P<value>
        [+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?      # number
      | ["'].*?["']                                  # string
      | \([^)]+\) | \[[^\]]+\] | \{[^\}]+\}          # tuple | list | dict
    )
    \s*$
    """,
    re.VERBOSE | re.MULTILINE,   # ← add re.MULTILINE
)

KWARG_RE  = re.compile(r"\s*(\w+)\s*=\s*([^,]+)\s*")
SPLIT_RE  = re.compile(r",(?![^(]*\))")              # split commas not in ()

# ──────────────────────────── CORE LOGIC ───────────────────────────────
def safe_eval(txt: str) -> Any:
    """Return literal value or the raw string if literal_eval fails."""
    try:
        return ast.literal_eval(txt)
    except Exception:                              # not a literal
        return txt.strip()

def parse_arglist(arglist: str, const_map: Dict[str, Any]) -> Dict[str, Any]:
    """Convert 'x, loc=a, scale=b' → dict, substituting a/b if they are constants."""
    params: Dict[str, Any] = {}
    positional: List[Any] = []

    for token in filter(None, SPLIT_RE.split(arglist)):
        token = token.strip()
        m_kw = KWARG_RE.fullmatch(token)
        if m_kw:
            key, value_txt = m_kw.groups()
            value = safe_eval(value_txt)
            if isinstance(value, str) and value in const_map:
                value = const_map[value]
            params[key] = value
        else:
            val = safe_eval(token)
            if isinstance(val, str) and val in const_map:
                val = const_map[val]
            positional.append(val)

    if positional:
        params["args"] = tuple(positional)
    return params


def extract_from_text(text: str) -> List[Tuple[str, str, str, Dict[str, Any]]]:
    """
    Pass 1 – collect constants           ➜ const_map
    Pass 2 – collect distributions/const ➜ tuples, using const_map for substitution
    """
    tuples: List[Tuple[str, str, str, Dict[str, Any]]] = []
    const_map: Dict[str, Any] = {}

    # Pass 1: constants everywhere (top-level + inside functions)
    for mc in ASSIGN_CONST_RE.finditer(text):
        var  = mc.group("var")
        val  = safe_eval(mc.group("value"))
        const_map[var] = val
        tuples.append(("", var, "constant", {"value": val}))  # filename filled later

    # Pass 2: distributions
    for md in ASSIGN_DIST_RE.finditer(text):
        var   = md.group("var")
        dist  = md.group("dist")
        arglist = md.group("args")
        params = parse_arglist(arglist, const_map)
        tuples.append(("", var, dist, params))

    return tuples


def scan_files(src: pathlib.Path, patterns=FILE_GLOBS) -> List[Tuple[str, str, str, Dict[str, Any]]]:
    """Read each file once, run regex extraction, and tag with filename."""
    results: List[Tuple[str, str, str, Dict[str, Any]]] = []
    for pat in patterns:
        for file in src.rglob(pat):
            try:
                text = file.read_text(encoding="utf-8")
            except Exception as exc:
                print(f"⚠️  Skipping {file}: {exc}")
                continue

            for _, var, kind, params in extract_from_text(text):
                results.append((file.name, var, kind, params))
    return results






def tuples_to_csv(
    tuples: List[Tuple[str, str, str, Dict[str, Any]]],
    out_path: Union[str, Path] = "extracted_params.csv",   # ← use Union[…]
) -> Path:
    """
    Write (file, var, kind, params_dict) tuples to *out_path* as CSV.
    The *params_dict* column is JSON-encoded.
    Returns the absolute Path of the created file.
    """
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)   # create folders if needed
    with out_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(["file", "variable", "kind", "params_json"])
        for file, var, kind, params in tuples:
            writer.writerow([file, var, kind, json.dumps(params, ensure_ascii=False)])
    return out_path


SOURCE_DIR = (
    Path(__file__).resolve().parent / "../../../../gencodeforAMG_100_c").resolve()

tuples = scan_files(SOURCE_DIR)
print(f"Found {len(tuples)} items in {SOURCE_DIR}:")


tuples_csv = tuples_to_csv(tuples, r"C:/Users/leksh/Downloads/Langchaintrial-20240920T083025Z-001/Langchaintrial/DataFITR/DataFITR4amg/output/extracted_params_model_c.csv")

SOURCE_DIR = (
    Path(__file__).resolve().parent / "../../../../gencodeforAMG_10").resolve()

tuples = scan_files(SOURCE_DIR)
print(f"Found {len(tuples)} items in {SOURCE_DIR}:")


tuples_csv = tuples_to_csv(tuples, r"C:/Users/leksh/Downloads/Langchaintrial-20240920T083025Z-001/Langchaintrial/DataFITR/DataFITR4amg/output/extracted_params_modela.csv")

SOURCE_DIR = (
    Path(__file__).resolve().parent / "../../../../gencodeforAMG_100_b").resolve()

tuples = scan_files(SOURCE_DIR)
print(f"Found {len(tuples)} items in {SOURCE_DIR}:")


tuples_csv = tuples_to_csv(tuples, r"C:/Users/leksh/Downloads/Langchaintrial-20240920T083025Z-001/Langchaintrial/DataFITR/DataFITR4amg/output/extracted_params_modelb.csv")