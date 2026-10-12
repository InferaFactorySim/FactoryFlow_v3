"""Table 11: compare the fitted process-delay plugins with the S3 ground truth.

For every plugin in a folder this script reports whether the fitted family is
the true one, whether the fit lies within sampling noise of the true
distribution, and the relative errors of its mean and standard deviation. A fit
is within sampling noise when its largest CDF distance from the true
distribution is no larger than the 95th percentile of the same distance for
100 correct-family scipy fits to 500 readings.

Usage, from this folder:
    python table11_parameter_recovery.py            # phase A plugins (Table 11, phase A)
    python table11_parameter_recovery.py B          # phase B plugins (Table 11, changed machines)
"""
import glob
import importlib.util
import os
import re
import sys
import warnings

import numpy as np
from scipy import stats

warnings.filterwarnings("ignore")
from s3_paths import PLUGINS_A, PLUGINS_B  # noqa: E402  (also puts reference_system on the path)
import S3_GT as g  # noqa: E402

PHASE = sys.argv[1].upper() if len(sys.argv) > 1 else "A"
RVG = {"A": PLUGINS_A, "B": PLUGINS_B}[PHASE]
N_FIT = 500
rng = np.random.default_rng(0)


def grid(dist):
    return np.linspace(dist.ppf(1e-6), dist.ppf(1 - 1e-6), 4000)


def plugin_cdf(mod, x):
    """CDF of what the plugin actually emits (positive-only resampling included)."""
    name = getattr(mod, "DISTRIBUTION", None)
    if name and name != "deterministic" and hasattr(stats, name):
        d = getattr(stats, name)(*mod.PARAMETERS)
        p0 = d.cdf(0.0)
        return np.clip((d.cdf(x) - p0) / (1 - p0), 0, 1), d, name
    sample = np.sort(mod.generate(size=200_000, seed=1))
    return np.searchsorted(sample, x, side="right") / sample.size, None, name or "kde/other"


# Sampling-noise floor: how far a 500-reading scipy fit of the right family lands.
noise = {}
def noise_floor(dist):
    key = (dist.dist.name, tuple(np.round(dist.args, 6)), tuple(sorted(dist.kwds.items())))
    if key not in noise:
        x = grid(dist)
        gaps = []
        for _ in range(100):
            p = dist.dist.fit(dist.rvs(N_FIT, random_state=rng))
            gaps.append(np.max(np.abs(dist.cdf(x) - dist.dist(*p).cdf(x))))
        noise[key] = float(np.percentile(gaps, 95))
    return noise[key]


rows = []
for path in sorted(glob.glob(os.path.join(RVG, "TOP.Line_*.M*_process_delay.py"))):
    m = re.search(r"Line_(\d+)\.M(\d+)_", path)
    line, col = int(m.group(1)), int(m.group(2))
    spec = importlib.util.spec_from_file_location(f"p{line}_{col}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    phases = [("A", g.phase_distribution(1, col)[1])]
    if col in g.STAGE2_DISTRIBUTIONS:
        phases.append(("B", g.phase_distribution(2, col)[1]))
    best = None
    for label, ref in phases:
        x = grid(ref)
        cdf, fitted, name = plugin_cdf(mod, x)
        gap = float(np.max(np.abs(cdf - ref.cdf(x))))
        if best is None or gap < best[1]:
            if fitted is not None:
                fm, fv = [float(t) for t in fitted.stats("mv")]
            else:
                s = mod.generate(size=200_000, seed=2)
                fm, fv = float(s.mean()), float(s.var())
            rm, rv = [float(t) for t in ref.stats("mv")]
            best = (label, gap, ref, name, fm, fv ** .5, rm, rv ** .5)
    label, gap, ref, name, fm, fsd, rm, rsd = best
    floor = noise_floor(ref)
    rows.append(dict(entity=f"Line_{line:02d}.M{col:02d}", col=col, phase=label,
                     true=ref.dist.name, fitted=name, family_ok=name == ref.dist.name,
                     gap=gap, floor=floor, ok=gap <= floor,
                     mean_err=100 * (fm - rm) / rm, sd_err=100 * (fsd - rsd) / rsd,
                     n=getattr(mod, "SAMPLE_COUNT", None)))

print(f"phase {PHASE} plugins from {os.path.relpath(RVG, os.path.dirname(os.path.abspath(__file__)))}")
print(f"plugins checked: {len(rows)}  sample counts: {sorted(set(r['n'] for r in rows))}")
print("\ncol true        phase  family-ok  within-noise  CDF gap mean/max  noise95  mean err% (max abs)  sd err% (max abs)  fitted families")
for col in range(10):
    sub = [r for r in rows if r["col"] == col]
    fams = {}
    for r in sub:
        fams[r["fitted"]] = fams.get(r["fitted"], 0) + 1
    print(f"M{col:02d} {sub[0]['true']:11s} {''.join(sorted(set(r['phase'] for r in sub))):5s}  "
          f"{sum(r['family_ok'] for r in sub):2d}/{len(sub)}      {sum(r['ok'] for r in sub):2d}/{len(sub)}       "
          f"{np.mean([r['gap'] for r in sub]):.3f}/{max(r['gap'] for r in sub):.3f}     {sub[0]['floor']:.3f}    "
          f"{max(abs(r['mean_err']) for r in sub):6.2f}            {max(abs(r['sd_err']) for r in sub):6.2f}       {fams}")
bad = [r for r in rows if not r["ok"]]
print(f"\nALL: family right {sum(r['family_ok'] for r in rows)}/{len(rows)}, within sampling noise {len(rows) - len(bad)}/{len(rows)}, "
      f"max |mean err| {max(abs(r['mean_err']) for r in rows):.2f}%, max |sd err| {max(abs(r['sd_err']) for r in rows):.2f}%")
for r in sorted(bad, key=lambda r: -r["gap"]):
    print(f"  OUTSIDE NOISE {r['entity']} phase {r['phase']} true={r['true']} fitted={r['fitted']} gap={r['gap']:.3f} (noise95 {r['floor']:.3f}) mean {r['mean_err']:+.2f}% sd {r['sd_err']:+.2f}%")
