"""Shared evaluation harness for structural drift detection experiments.

Puts the in-repo ``factorysimpy`` library and the ``structural_drift_detection``
package on ``sys.path`` so experiment modules can simply do::

    from tests.harness import reference_model, detector_io, metrics

or import the sibling modules directly after importing this package.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))  # tests/harness -> tests -> repo root

for _p in ("src", "structural_drift_detection"):
    _fp = os.path.join(_ROOT, _p)
    if _fp not in sys.path:
        sys.path.insert(0, _fp)

REPO_ROOT = _ROOT
RESULTS_DIR = os.path.join(_ROOT, "results")
