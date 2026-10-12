"""Generate a FactorySimPy model of the SMT line with FactoryFlow's AMG pipeline.

The generator's own PARAMS (IM/AMG_with_langgraph.py) declares only Source, Machine,
Sink, Node and Buffer, so it cannot express this line. Rather than edit that module -
which would change the component set seen by every other experiment - this script passes
its own extended schema to run_initial_from_description, which accepts params as an
argument. The generator module is left untouched.

The API key is read by the generator itself, via load_dotenv() finding IM/.env.

Run:  python generate_smt_model.py [n_runs]
Output: generated_<i>.py and assumptions_<i>.txt in ./generated/
"""

import os
import re
import sys
import json
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
# The FactoryFlow generator (IM/) as used in the paper, copied into ../generator;
# FACTORYFLOW_DIR can point at another FactoryFlow checkout instead.
_FACTORYFLOW = os.environ.get(
    "FACTORYFLOW_DIR", os.path.join(os.path.dirname(_HERE), "generator"))
if not os.path.isdir(os.path.join(_FACTORYFLOW, "IM")):
    sys.exit(f"no generator (IM/) in {_FACTORYFLOW}")
_EXAMPLES = os.path.join(_FACTORYFLOW, "IM", "examples")
_DESCRIPTION = os.path.join(_HERE, "smt_description.txt")
_OUT = os.path.join(_HERE, "generated")

for path in (os.path.join(os.path.dirname(_HERE), "src"), _FACTORYFLOW, os.path.join(_FACTORYFLOW, "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

# The generator resolves some paths relative to the working directory.
os.chdir(_FACTORYFLOW)

from IM.AMG_with_langgraph import run_initial_from_description
from IM.amg_extensions import extended_params, EXTENDED_SYSTEM_PROMPT

# The extended schema and prompt append the components this line needs to the
# generator's own definitions, leaving AMG_with_langgraph untouched.
SYSTEM_PROMPT = EXTENDED_SYSTEM_PROMPT
PARAMS = extended_params()


def read_description(which):
    """Return one named section of the description file.

    The file holds more than one wording of the same line, marked with
    '=== DESCRIPTION: <name> ==='. Only the requested section is returned, so a run
    is never fed two descriptions of the same system.
    """
    with open(_DESCRIPTION, encoding="utf-8") as handle:
        text = handle.read()

    sections = {}
    current = None
    for line in text.splitlines():
        marker = re.match(r"^===\s*DESCRIPTION:\s*(\S+)\s*===\s*$", line)
        if marker:
            current = marker.group(1)
            sections[current] = []
        elif current:
            sections[current].append(line)

    if not sections:  # an unmarked file is a single description
        return text
    if which not in sections:
        raise SystemExit(f"no section '{which}'; found {sorted(sections)}")
    return "\n".join(sections[which]).strip()


def main():
    n_runs = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    which = sys.argv[2] if len(sys.argv) > 2 else "dense"
    # An optional output folder keeps a batch from one fixed description apart from
    # earlier batches generated from older wordings.
    out_name = sys.argv[3] if len(sys.argv) > 3 else which

    # Candidates are kept per description and never overwritten: each batch continues
    # the numbering, so every candidate stays as re-runnable evidence.
    out_dir = os.path.join(_OUT, out_name)
    os.makedirs(out_dir, exist_ok=True)
    existing = [
        int(m.group(1))
        for name in os.listdir(out_dir)
        for m in [re.match(r"generated_(\d+)\.py$", name)]
        if m
    ]
    first_index = max(existing, default=0) + 1

    description = read_description(which)
    # The exact text each batch was generated from is stored with its candidates.
    with open(os.path.join(out_dir, f"description_used_{first_index}.txt"), "w",
              encoding="utf-8") as handle:
        handle.write(description)

    print(f"description: '{which}' section, {len(description)} chars "
          f"from {_DESCRIPTION}")
    print(f"examples   : {_EXAMPLES}")
    print(f"runs       : {n_runs}\n")

    summary = []
    for offset in range(n_runs):
        i = first_index + offset
        print(f"--- run {offset + 1}/{n_runs} (candidate {i}) ---")
        started = time.perf_counter()
        try:
            state = run_initial_from_description(
                system_prompt=SYSTEM_PROMPT,
                description=description,
                folder=_EXAMPLES,
                params=PARAMS,
            )
        except Exception as exc:
            print(f"  FAILED: {type(exc).__name__}: {exc}")
            summary.append({"run": i, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            continue

        elapsed = time.perf_counter() - started
        code = state.get("code") or state.get("code_from_amg") or ""
        assumptions = state.get("assumptions", "")

        code_path = os.path.join(out_dir, f"generated_{i}.py")
        with open(code_path, "w", encoding="utf-8") as handle:
            handle.write(code)
        with open(os.path.join(out_dir, f"assumptions_{i}.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write(assumptions)

        print(f"  {elapsed:.1f}s, {len(code)} chars of code -> {code_path}")
        summary.append({
            "run": i, "ok": bool(code), "seconds": round(elapsed, 1),
            "code_chars": len(code), "state_keys": sorted(state.keys()),
        })

    summary_path = os.path.join(out_dir, "summary.json")
    if os.path.exists(summary_path):
        with open(summary_path, encoding="utf-8") as handle:
            summary = json.load(handle) + summary
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
    print(f"\nsummary -> {summary_path}")


if __name__ == "__main__":
    main()
