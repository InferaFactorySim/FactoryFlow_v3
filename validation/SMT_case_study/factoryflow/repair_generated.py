"""Apply the minimum repairs the generated SMT model needs, and record them.

Each repair is a single, named defect. The count and nature of the repairs is the
result of interest, so they are applied mechanically rather than by hand-editing.

Run:  python repair_generated.py generated/generated_1.py
"""

import ast
import re
import sys

REPAIRS = [
    (
        "import path: factorysimpy.edges.conveyor_belt does not exist; the module "
        "file is misspelled converyorbelt.py in the library",
        re.compile(r"factorysimpy\.edges\.conveyor_belt"),
        "factorysimpy.edges.converyorbelt",
    ),
    (
        "delay callable signature: Node.get_delay calls the callable with no "
        "arguments, and the board counter is stats['num_item_processed'], not "
        "stats['num_parts_processed']",
        re.compile(
            r"def (_?\w*processing_delay)\(machine\):\s*\n"
            r"(\s+)if \(machine\.stats\[\"num_parts_processed\"\] \+ 1\) % 2 == 0:"
        ),
        lambda m: (
            f"_printed = [0]\n"
            f"{' ' * (len(m.group(2)) - 4)}def {m.group(1)}():\n"
            f"{m.group(2)}_printed[0] += 1\n"
            f"{m.group(2)}if _printed[0] % 2 == 0:"
        ),
    ),
]


def main():
    path = sys.argv[1]
    with open(path, encoding="utf-8") as handle:
        code = handle.read()

    applied = []
    for label, pattern, replacement in REPAIRS:
        new_code, n = pattern.subn(replacement, code, count=1)
        if n:
            applied.append(label)
            code = new_code

    out = path.replace(".py", "_repaired.py")
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(code)

    ast.parse(code)
    print(f"{len(applied)} repair(s) applied -> {out}")
    for i, label in enumerate(applied, 1):
        print(f"  {i}. {label}")
    print("syntax ok")


if __name__ == "__main__":
    main()
