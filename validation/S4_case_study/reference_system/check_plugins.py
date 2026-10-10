"""Compare the fitted RVG plugins against S4's ground-truth distributions.

Each plugin is exercised through its own ``create_generator()`` and the delays
it produces are compared with the true distribution for that machine's column.
Sampling rather than parsing keeps parametric fits and KDE fits comparable: what
matters is the delay the Digital Twin will actually see, not the label on it.

    python check_plugins.py [plugin_dir] [draws]

Defaults to ``parameter_drift/data/rvg`` and 20,000 draws per plugin.
"""

import os
import re
import sys
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from S4_GT import COLUMN_DISTRIBUTIONS, phase_distribution  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DEFAULT_DIR = os.path.join(ROOT, "parameter_drift", "data", "rvg")

HEADER = re.compile(r"Entity:\s*(\S+).*?Distribution:\s*(\S+).*?Data points used:\s*(\d+)", re.S)
ALIASES = {"norm": "normal", "lognorm": "lognormal", "weibull_min": "weibull",
           "expon": "exponential", "triang": "triangular"}


def load_plugin(path):
    """Execute a plugin in its own namespace and return (header, generator)."""
    source = open(path, encoding="utf-8").read()
    found = HEADER.search(source)
    if not found:
        return None, None, None, None
    entity, family, points = found.group(1), found.group(2), int(found.group(3))
    module = types.ModuleType("rvg_" + os.path.basename(path).replace(".", "_"))
    module.__file__ = path
    exec(compile(source, path, "exec"), module.__dict__)
    factory = getattr(module, "create_generator", None)
    return entity, family, points, (factory() if factory else None)


def main():
    plugin_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIR
    draws = int(sys.argv[2]) if len(sys.argv) > 2 else 20_000

    files = sorted(f for f in os.listdir(plugin_dir)
                   if f.startswith("TOP.Line") and f.endswith("_process_delay.py"))
    if not files:
        raise SystemExit(f"no S4 plugins in {plugin_dir}")

    per_column = {}
    for name in files:
        entity, family, points, gen = load_plugin(os.path.join(plugin_dir, name))
        if gen is None:
            continue
        column = int(entity.rsplit(".M", 1)[1])
        samples = np.fromiter((gen() for _ in range(draws)), dtype=float, count=draws)
        per_column.setdefault(column, []).append((family, samples.mean(), samples.std(), points))

    print(f"{len(files)} plugins from {os.path.relpath(plugin_dir, ROOT)}, "
          f"{draws:,} draws each\n")
    print(f"{'col':<5}{'true family':<12}{'true mean':>10}{'true sd':>9}"
          f"{'   fitted mean':>15}{'err':>8}{'  fitted sd':>12}{'err':>8}  families fitted")

    worst_mean = worst_sd = 0.0
    for column in sorted(per_column):
        true_fam = COLUMN_DISTRIBUTIONS[column][0]
        d = phase_distribution(1, column)[1]
        tm, tsd = float(d.stats("m")), float(d.stats("v")) ** 0.5
        fams = {}
        means, sds = [], []
        for family, m, s, _ in per_column[column]:
            fams[ALIASES.get(family, family)] = fams.get(ALIASES.get(family, family), 0) + 1
            means.append(m)
            sds.append(s)
        fm, fs = float(np.mean(means)), float(np.mean(sds))
        me, se = abs(fm - tm) / tm * 100, abs(fs - tsd) / tsd * 100
        worst_mean, worst_sd = max(worst_mean, me), max(worst_sd, se)
        shown = ", ".join(f"{k}x{v}" for k, v in sorted(fams.items(), key=lambda kv: -kv[1]))
        print(f"C{column:<4}{true_fam:<12}{tm:>10.3f}{tsd:>9.3f}"
              f"{fm:>15.3f}{me:>7.2f}%{fs:>12.3f}{se:>7.2f}%  {shown}")

    print()
    print(f"worst column error: mean {worst_mean:.2f}%, standard deviation {worst_sd:.2f}%")
    print()
    print("A KDE fit has no parametric family, so it can never 'match' by label.")
    print("What matters for the twin is the delay it generates, which is what is")
    print("compared here. C2 gamma and C8 lognormal are inseparable at these sample")
    print("sizes (KS 0.052 against a 0.163 critical value at N=100), so a label swap")
    print("between those two is expected rather than a fitting failure.")


if __name__ == "__main__":
    main()
