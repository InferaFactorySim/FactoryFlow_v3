from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from influxdb_client import InfluxDBClient
from scipy import stats

from IM.modular_IM import getcontinuousdist, modelmatch

from shared.atomic_files import atomic_write_text
from shared.data_sources import active_influx_config


DATA_DIR = Path(os.getenv("PARAM_DRIFT_DATA_DIR", "/data"))
CONFIG_FILE = DATA_DIR / "monitoring_configurations.json"
BASELINE_FILE = DATA_DIR / "parameter_baselines.json"
RESULT_FILE = DATA_DIR / "parameter_drift_results.json"
RVG_DIR = DATA_DIR / "rvg"

CONTINUOUS_POPULAR = ["norm", "expon", "lognorm", "gamma", "weibull_min", "uniform", "triang"]
DISCRETE_POPULAR = ["poisson", "geom", "binom"]
MEAN_STD_SHIFT_THRESHOLD_PCT = 12.0
# Sample standard deviations of heavy-tailed families (lognormal, gamma) vary by
# well over 12% between two 300-value windows with no change at all.
STD_SHIFT_THRESHOLD_PCT = 25.0
NORMALIZED_WASSERSTEIN_THRESHOLD = 0.25


class InsufficientSamples(RuntimeError):
    def __init__(self, available: int, required: int, phase: str = "baseline"):
        self.available = int(available)
        self.required = int(required)
        self.phase = phase
        super().__init__(
            f"Waiting for data: {self.available}/{self.required} readings available "
            f"({self.required - self.available} more required)"
        )


def natural_key(value: str) -> list[Any]:
    """Sort TOP.M2 before TOP.M10 while preserving arbitrary entity names."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", str(value))]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return default


def write_json(path: Path, value: Any) -> None:
    atomic_write_text(str(path), json.dumps(value, indent=2, allow_nan=False))


def load_configs() -> list[dict[str, Any]]:
    value = read_json(CONFIG_FILE, [])
    return value if isinstance(value, list) else []


def save_configs(configs: list[dict[str, Any]]) -> None:
    write_json(CONFIG_FILE, configs)


def load_baselines() -> dict[str, Any]:
    value = read_json(BASELINE_FILE, {})
    return value if isinstance(value, dict) else {}


def load_results() -> dict[str, Any]:
    value = read_json(RESULT_FILE, {})
    return value if isinstance(value, dict) else {}


def delete_configurations(config_ids: list[str]) -> int:
    """Delete selected monitoring configurations and their derived artifacts.

    Source telemetry in InfluxDB is deliberately not modified.
    """
    selected = set(config_ids)
    configs = load_configs()
    removed = [config for config in configs if config.get("id") in selected]
    remaining = [config for config in configs if config.get("id") not in selected]

    baselines = load_baselines()
    results = load_results()
    for config in removed:
        baselines.pop(config["id"], None)
        results.pop(config["id"], None)

        # Preserve a shared plugin if another configuration still owns the
        # same entity/parameter pair.
        shared = any(
            item.get("entity_id") == config.get("entity_id")
            and item.get("parameter") == config.get("parameter")
            for item in remaining
        )
        if not shared:
            safe_entity = re.sub(r"[^A-Za-z0-9_.-]+", "_", config["entity_id"])
            safe_parameter = re.sub(r"[^A-Za-z0-9_.-]+", "_", config["parameter"])
            plugin = RVG_DIR / f"{safe_entity}_{safe_parameter}.py"
            try:
                plugin.unlink()
            except FileNotFoundError:
                pass

    save_configs(remaining)
    write_json(BASELINE_FILE, baselines)
    write_json(RESULT_FILE, results)
    return len(removed)


def influx_config() -> dict[str, Any]:
    saved = active_influx_config() or {}
    verify = saved.get("verify_ssl", os.getenv("INFLUX_VERIFY_SSL", "true"))
    if not isinstance(verify, bool):
        verify = str(verify).lower() in {"1", "true", "yes", "on"}
    return {
        "url": saved.get("url", os.getenv("INFLUX_URL", "")),
        "token": saved.get("token", os.getenv("INFLUX_TOKEN", "")),
        "org": saved.get("org", os.getenv("INFLUX_ORG", "")),
        "bucket": saved.get("bucket", os.getenv("INFLUX_BUCKET", "")),
        "verify_ssl": verify,
        "name": saved.get("name", "Environment InfluxDB"),
    }


def flux_string(value: str) -> str:
    return json.dumps(str(value))


class InfluxReader:
    def __init__(self) -> None:
        self.config = influx_config()
        missing = [key for key in ("url", "token", "org", "bucket") if not self.config[key]]
        if missing:
            raise RuntimeError("InfluxDB connection is incomplete: " + ", ".join(missing))
        self.client = InfluxDBClient(
            url=self.config["url"], token=self.config["token"], org=self.config["org"],
            verify_ssl=self.config["verify_ssl"], timeout=600_000,
        )
        self.query_api = self.client.query_api()

    def query(self, flux: str) -> pd.DataFrame:
        frame = self.query_api.query_data_frame(flux)
        if isinstance(frame, list):
            frame = pd.concat(frame, ignore_index=True) if frame else pd.DataFrame()
        return frame

    @staticmethod
    def _flux_filters(filters: dict[str, str] | None) -> str:
        return "\n".join(
            f'  |> filter(fn: (r) => r[{flux_string(key)}] == {flux_string(value)})'
            for key, value in (filters or {}).items() if key and value
        )

    def tag_values(
        self, tag: str, measurement: str, lookback: str = "-30d",
        filters: dict[str, str] | None = None,
    ) -> list[str]:
        predicates = [f'r._measurement == {flux_string(measurement)}']
        predicates.extend(
            f'r[{flux_string(key)}] == {flux_string(value)}'
            for key, value in (filters or {}).items() if key and value
        )
        flux = (
            'import "influxdata/influxdb/schema"\n'
            f'schema.tagValues(bucket: {flux_string(self.config["bucket"])}, '
            f'tag: {flux_string(tag)}, start: {lookback}, '
            f'predicate: (r) => {" and ".join(predicates)})'
        )
        df = self.query(flux)
        return sorted(
            (str(v) for v in df.get("_value", pd.Series(dtype=str)).dropna().unique()),
            key=natural_key,
        )

    def measurements(self) -> list[str]:
        flux = (
            'import "influxdata/influxdb/schema"\n'
            f'schema.measurements(bucket: {flux_string(self.config["bucket"])}, start: -30d)'
        )
        df = self.query(flux)
        return sorted(str(v) for v in df.get("_value", pd.Series(dtype=str)).dropna().unique())

    def fields(
        self, measurement: str, lookback: str = "-30d",
        filters: dict[str, str] | None = None,
    ) -> list[str]:
        predicates = [f'r._measurement == {flux_string(measurement)}']
        predicates.extend(
            f'r[{flux_string(key)}] == {flux_string(value)}'
            for key, value in (filters or {}).items() if key and value
        )
        flux = (
            'import "influxdata/influxdb/schema"\n'
            f'schema.fieldKeys(bucket: {flux_string(self.config["bucket"])}, '
            f'start: {lookback}, predicate: (r) => {" and ".join(predicates)})'
        )
        df = self.query(flux)
        return sorted(str(v) for v in df.get("_value", pd.Series(dtype=str)).dropna().unique())

    def series(self, config: dict[str, Any], window: str, limit: int | None = None) -> pd.DataFrame:
        filters = [
            f'  |> filter(fn: (r) => r["_measurement"] == {flux_string(config["measurement"])})',
            f'  |> filter(fn: (r) => r["_field"] == {flux_string(config["parameter"])})',
            f'  |> filter(fn: (r) => r[{flux_string(config["entity_tag"])}] == {flux_string(config["entity_id"])})',
        ]
        if config.get("entity_type") and config.get("entity_type_tag"):
            filters.append(
                f'  |> filter(fn: (r) => r[{flux_string(config["entity_type_tag"])}] == '
                f'{flux_string(config["entity_type"])})'
            )
        flux = (
            f'from(bucket: {flux_string(self.config["bucket"])})\n'
            f'  |> range(start: {window})\n' + "\n".join(filters) +
            # sort() and limit() act per series table; merge the tag sets first
            # so the limit selects the newest N readings overall, not N per table.
            '\n  |> group()'
            '\n  |> sort(columns: ["_time"], desc: true)'
        )
        if limit:
            flux += f"\n  |> limit(n: {int(limit)})"
        df = self.query(flux)
        if "_value" not in df:
            return pd.DataFrame(columns=["_time", "_value"])
        result = df[[column for column in ("_time", "_value") if column in df]].copy()
        result["_value"] = pd.to_numeric(result["_value"], errors="coerce")
        result.dropna(subset=["_value"], inplace=True)
        if "_time" in result:
            result["_time"] = pd.to_datetime(result["_time"], errors="coerce", utc=True)
            result.sort_values("_time", inplace=True)
        if limit:
            result = result.tail(int(limit))
        return result

    def values(self, config: dict[str, Any], window: str, limit: int | None = None) -> np.ndarray:
        return self.series(config, window, limit)["_value"].to_numpy(dtype=float)


def infer_kind(values: np.ndarray, requested: str) -> str:
    if requested != "auto":
        return requested
    return "discrete" if np.allclose(values, np.round(values)) else "continuous"


def silverman_kde(values: np.ndarray, evaluation_points: int) -> dict[str, Any]:
    """Build the Gaussian KDE used by DataFITR (Silverman's bandwidth)."""
    values = np.asarray(values, dtype=float)
    bandwidth = float(1.06 * np.std(values) * len(values) ** (-1 / 5.0))
    edges = np.histogram_bin_edges(
        values, bins=int(evaluation_points), range=(np.min(values), np.max(values))
    )
    centers = (edges[:-1] + edges[1:]) / 2
    if not np.isfinite(bandwidth) or bandwidth <= 0:
        density = np.zeros_like(centers)
    else:
        offsets = (centers[:, None] - values[None, :]) / bandwidth
        density = np.exp(-0.5 * offsets ** 2).sum(axis=1)
        density /= len(values) * bandwidth * np.sqrt(2 * np.pi)
    return {
        "bandwidth": bandwidth,
        "evaluation_points": int(evaluation_points),
        "x": centers.tolist(),
        "density": density.tolist(),
    }


def candidate_names(kind: str, selection: str) -> list[str]:
    if kind == "discrete":
        return DISCRETE_POPULAR
    if selection.startswith("custom:"):
        return [name for name in selection.removeprefix("custom:").split(",") if name]
    if selection == "all":
        try:
            return list(getcontinuousdist())
        except Exception:
            return CONTINUOUS_POPULAR
    return CONTINUOUS_POPULAR


def score_samples(observed: np.ndarray, expected: np.ndarray, method: str, bins: int) -> float:
    """Return a lower-is-better GOF statistic on a shared support."""
    if method == "ks":
        return float(stats.ks_2samp(observed, expected).statistic)
    edges = np.histogram_bin_edges(np.concatenate([observed, expected]), bins=max(int(bins), 5))
    observed_counts, _ = np.histogram(observed, bins=edges)
    expected_counts, _ = np.histogram(expected, bins=edges)
    if method == "sse":
        observed_prob = observed_counts / max(observed_counts.sum(), 1)
        expected_prob = expected_counts / max(expected_counts.sum(), 1)
        return float(np.sum((observed_prob - expected_prob) ** 2))
    expected_counts = expected_counts.astype(float) + 1e-9
    expected_counts *= observed_counts.sum() / expected_counts.sum()
    return float(stats.chisquare(observed_counts, expected_counts).statistic)


def two_sample_gof(
    reference: np.ndarray, current: np.ndarray, method: str, bins: int,
) -> tuple[float, float]:
    """Return the selected two-sample GoF statistic and p-value for drift detection."""
    reference = np.asarray(reference, dtype=float)
    current = np.asarray(current, dtype=float)
    if method == "ks":
        result = stats.ks_2samp(reference, current)
        return float(result.statistic), float(result.pvalue)

    edges = np.histogram_bin_edges(
        np.concatenate([reference, current]), bins=max(int(bins), 5)
    )
    reference_counts, _ = np.histogram(reference, bins=edges)
    current_counts, _ = np.histogram(current, bins=edges)
    reference_prob = reference_counts / max(reference_counts.sum(), 1)
    current_prob = current_counts / max(current_counts.sum(), 1)
    if method == "chi":
        expected = reference_prob.astype(float) + 1e-9
        expected *= current_counts.sum() / expected.sum()
        result = stats.chisquare(current_counts, expected)
        return float(result.statistic), float(result.pvalue)

    statistic = float(np.sum((reference_prob - current_prob) ** 2))
    pooled = np.concatenate([reference, current])
    rng = np.random.default_rng(1729)
    simulated = []
    for _ in range(100):
        shuffled = rng.permutation(pooled)
        simulated_reference, simulated_current = (
            shuffled[:len(reference)], shuffled[len(reference):]
        )
        simulated_reference_counts, _ = np.histogram(simulated_reference, bins=edges)
        simulated_current_counts, _ = np.histogram(simulated_current, bins=edges)
        simulated_reference_prob = simulated_reference_counts / max(simulated_reference_counts.sum(), 1)
        simulated_current_prob = simulated_current_counts / max(simulated_current_counts.sum(), 1)
        simulated.append(float(np.sum((simulated_reference_prob - simulated_current_prob) ** 2)))
    pvalue = float((1 + np.count_nonzero(np.asarray(simulated) >= statistic)) / 101)
    return statistic, pvalue


def goodness_of_fit_measures(
    values: np.ndarray, distribution: str, parameters: list[float], kind: str, bins: int,
) -> dict[str, float | None]:
    """Calculate comparable GOF measures for one fitted SciPy distribution."""
    values = np.asarray(values, dtype=float)
    dist = getattr(stats, distribution)
    params = tuple(parameters)
    try:
        ks = stats.kstest(values, dist.cdf, args=params)
        ks_statistic, ks_pvalue = float(ks.statistic), float(ks.pvalue)
    except Exception:
        ks_statistic = ks_pvalue = None

    try:
        if kind == "discrete":
            low, high = int(np.min(values)), int(np.max(values))
            edges = np.arange(low - 0.5, high + 1.5, 1.0)
        else:
            edges = np.histogram_bin_edges(values, bins=max(int(bins), 5))
        observed, _ = np.histogram(values, bins=edges)
        probabilities = np.diff(dist.cdf(edges, *params))
        probabilities = np.clip(np.asarray(probabilities, dtype=float), 0.0, None)
        valid = np.isfinite(probabilities) & (probabilities > 1e-12)
        observed = observed[valid]
        expected = probabilities[valid]
        expected *= observed.sum() / expected.sum()
        chi = stats.chisquare(observed, expected)
        chi_statistic, chi_pvalue = float(chi.statistic), float(chi.pvalue)
        observed_prob = observed / max(observed.sum(), 1)
        expected_prob = expected / max(expected.sum(), 1)
        sse = float(np.sum((observed_prob - expected_prob) ** 2))
        # SSE has no closed-form null distribution. Report a reproducible
        # parametric-bootstrap p-value: the share of samples from the fitted
        # distribution whose histogram SSE is at least the observed SSE.
        rng = np.random.default_rng(1729)
        simulated = dist.rvs(*params, size=(100, len(values)), random_state=rng)
        simulated_sse = []
        for sample in np.asarray(simulated):
            counts, _ = np.histogram(sample, bins=edges)
            counts = counts[valid]
            sample_prob = counts / max(counts.sum(), 1)
            simulated_sse.append(float(np.sum((sample_prob - expected_prob) ** 2)))
        sse_pvalue = float((1 + np.count_nonzero(np.asarray(simulated_sse) >= sse)) / 101)
    except Exception:
        chi_statistic = chi_pvalue = sse = sse_pvalue = None
    return {
        "ks_statistic": ks_statistic, "ks_pvalue": ks_pvalue,
        "chi_square_statistic": chi_statistic, "chi_square_pvalue": chi_pvalue,
        "sse": sse, "sse_pvalue": sse_pvalue,
    }


def goodness_of_fit_kde(
    values: np.ndarray, bandwidth: float, bins: int,
) -> dict[str, float | None]:
    """Calculate KS, chi-square, and SSE measures for a Gaussian KDE.

    The KDE is evaluated as an equally weighted mixture of normal kernels.
    As with the fitted parametric distributions, these in-sample measures are
    intended for comparative GoF display and ranking.
    """
    values = np.asarray(values, dtype=float)
    if not np.isfinite(bandwidth) or bandwidth <= 0 or not len(values):
        return {
            "ks_statistic": None, "ks_pvalue": None,
            "chi_square_statistic": None, "chi_square_pvalue": None,
            "sse": None, "sse_pvalue": None,
        }

    def kde_cdf(points: Any) -> Any:
        array = np.asarray(points, dtype=float)
        flat = array.reshape(-1)
        cdf = np.mean(stats.norm.cdf((flat[:, None] - values[None, :]) / bandwidth), axis=1)
        return float(cdf[0]) if array.ndim == 0 else cdf.reshape(array.shape)

    try:
        ks = stats.kstest(values, kde_cdf)
        ks_statistic, ks_pvalue = float(ks.statistic), float(ks.pvalue)
    except Exception:
        ks_statistic = ks_pvalue = None

    try:
        edges = np.histogram_bin_edges(values, bins=max(int(bins), 5))
        observed, _ = np.histogram(values, bins=edges)
        probabilities = np.diff(kde_cdf(edges))
        probabilities = np.clip(np.asarray(probabilities, dtype=float), 0.0, None)
        valid = np.isfinite(probabilities) & (probabilities > 1e-12)
        observed = observed[valid]
        expected = probabilities[valid]
        expected *= observed.sum() / expected.sum()
        chi = stats.chisquare(observed, expected)
        chi_statistic, chi_pvalue = float(chi.statistic), float(chi.pvalue)
        observed_prob = observed / max(observed.sum(), 1)
        expected_prob = expected / max(expected.sum(), 1)
        sse = float(np.sum((observed_prob - expected_prob) ** 2))
        simulated_sse = []
        rng = np.random.default_rng(1729)
        for _ in range(100):
            centers = values[rng.integers(0, len(values), size=len(values))]
            sample = centers + rng.normal(0.0, bandwidth, size=len(values))
            counts, _ = np.histogram(sample, bins=edges)
            counts = counts[valid]
            sample_prob = counts / max(counts.sum(), 1)
            simulated_sse.append(float(np.sum((sample_prob - expected_prob) ** 2)))
        sse_pvalue = float((1 + np.count_nonzero(np.asarray(simulated_sse) >= sse)) / 101)
    except Exception:
        chi_statistic = chi_pvalue = sse = sse_pvalue = None
    return {
        "ks_statistic": ks_statistic, "ks_pvalue": ks_pvalue,
        "chi_square_statistic": chi_statistic, "chi_square_pvalue": chi_pvalue,
        "sse": sse, "sse_pvalue": sse_pvalue,
    }


def fit_distribution(values: np.ndarray, config: dict[str, Any]) -> dict[str, Any]:
    """Fit candidates with the same modular IM engine used by param_calib."""
    values = np.asarray(values, dtype=float)
    kind = infer_kind(values, config.get("data_type", "auto"))
    gof_method = config.get("gof_method", "ks")
    if values.size and np.all(values == values[0]):
        constant = float(values[0])
        return {
            "data_type": "constant", "fit_method": "deterministic",
            "gof_method": "not_applicable",
            "best_distribution": "deterministic",
            "automatic_best_distribution": "deterministic",
            "selected_distribution": None,
            "parameters": [constant],
            "top3_parameters": {"deterministic": [constant]},
            "all_parameters": {"deterministic": [constant]},
            "gof_measures": {"deterministic": {
                "ks_statistic": None, "ks_pvalue": None,
                "chi_square_statistic": None, "chi_square_pvalue": None,
                "sse": None, "sse_pvalue": None,
            }},
            "gof_score": 0.0, "all_scores": {"deterministic": 0.0},
            "mean": constant, "std": 0.0, "sample_count": int(len(values)),
        }
    candidates = candidate_names(kind, config.get("distribution_set", "popular"))
    distlist = [kind]
    matcher = modelmatch(
        data=values,
        typeofdata=kind,
        title=f"{config.get('entity_id', '')} / {config.get('parameter', '')}",
        binhist=int(config.get("bin_size", 20)),
        gofoption=gof_method,
        distlist=distlist,
        distributions=[candidates],
    )
    _, raw_scores = matcher.bestfit(distlist, [candidates])
    scores = {
        name: float(score) for name, score in raw_scores.items()
        if name not in {"data", "binedges", "binpdf"} and np.isscalar(score) and math.isfinite(float(score))
    }
    if not scores:
        raise RuntimeError("IM modular did not fit any candidate distribution")
    parameters: dict[str, list[float]] = {}
    for name in scores:
        try:
            parameters[name] = [float(value) for value in matcher.calc_param(name)]
        except Exception:
            parameters[name] = []
    requested = config.get("selected_distribution")
    kde = None
    if kind == "continuous":
        kde_points = config.get("kde_bins") or min(
            200, max(5, 2 * int(config.get("bin_size", 20)))
        )
        kde = silverman_kde(values, int(kde_points))
    measures = {
        name: goodness_of_fit_measures(values, name, parameters[name], kind, int(config.get("bin_size", 20)))
        for name in scores if parameters.get(name)
    }
    if kde is not None:
        measures["kde"] = goodness_of_fit_kde(
            values, float(kde["bandwidth"]), int(config.get("bin_size", 20))
        )
        score_key = {
            "ks": "ks_statistic",
            "chi": "chi_square_statistic",
            "sse": "sse",
        }.get(gof_method, "ks_statistic")
        kde_score = measures["kde"].get(score_key)
        if kde_score is not None and math.isfinite(float(kde_score)):
            scores["kde"] = float(kde_score)
    # KDE is built from the sample itself, so it out-scores the true family on
    # small windows by fitting sampling noise. Keep it selectable, never automatic.
    parametric_scores = {name: score for name, score in scores.items() if name != "kde"}
    automatic_best = min(parametric_scores or scores, key=(parametric_scores or scores).get)
    best = (
        "kde" if requested == "kde" and kde is not None
        else requested if requested in scores and (requested == "kde" or parameters.get(requested))
        else automatic_best
    )
    return {
        "data_type": kind, "fit_method": "IM.modular_IM", "gof_method": gof_method,
        "best_distribution": best, "automatic_best_distribution": automatic_best,
        "selected_distribution": requested if requested in scores or (requested == "kde" and kde) else None,
        "parameters": parameters.get(best, []),
        "top3_parameters": {
            name: parameters[name]
            for name in dict.fromkeys([best, *sorted(scores, key=scores.get)[:3]])
            if name in parameters
        },
        "all_parameters": parameters, "gof_measures": measures,
        "gof_score": scores.get(best),
        "all_scores": scores, "mean": float(np.mean(values)),
        "std": float(np.std(values)), "sample_count": int(len(values)),
        "kde": kde,
    }


# Continuous plugins resample until the value is positive: a KDE's Gaussian
# kernel and a fit with a negative loc both put mass below zero, and the
# Digital Twin rejects a negative delay. Each plugin defines ``_draw(rng, size)``.
POSITIVE_SAMPLING_FUNCTIONS = (
    "def generate(size=1, seed=None):\n"
    "    rng = np.random.default_rng(seed)\n"
    "    values = np.asarray(_draw(rng, int(size)), dtype=float)\n"
    "    bad = values <= 0\n"
    "    while bad.any():\n"
    "        values[bad] = _draw(rng, int(bad.sum()))\n"
    "        bad = values <= 0\n"
    "    return values\n\n"
    "def create_generator(seed=None):\n"
    "    rng = np.random.default_rng(seed)\n"
    "    def sample():\n"
    "        while True:\n"
    "            value = float(_draw(rng, None))\n"
    "            if value > 0:\n"
    "                return value\n"
    "    return sample\n"
)


def write_rvg_file(config: dict[str, Any], fit: dict[str, Any]) -> str:
    """Write a standalone random-variate generator for the stored baseline."""
    RVG_DIR.mkdir(parents=True, exist_ok=True)
    safe_entity = re.sub(r"[^A-Za-z0-9_.-]+", "_", config["entity_id"])
    safe_parameter = re.sub(r"[^A-Za-z0-9_.-]+", "_", config["parameter"])
    # Digital Twin discovers plugins by the ``<entity>_<parameter>.py`` suffix
    # convention used by its file loader (for example TOP.M1_process_delay.py).
    requested_stem = config.get("rvg_filename")
    if requested_stem:
        safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(requested_stem)).strip("._")
        if not safe_stem:
            raise ValueError("RVG filename must contain at least one letter or number")
        path = RVG_DIR / f"{safe_stem}.py"
    else:
        path = RVG_DIR / f"{safe_entity}_{safe_parameter}.py"
    best = fit["best_distribution"]
    params = fit.get("parameters", [])
    sample_count = int(fit.get("sample_count", len(fit.get("reference_values", []))))
    header = (
        '"""Generated by DataFITR Parameter Drift Monitoring.\n'
        f'Entity: {config["entity_id"]}\nParameter: {config["parameter"]}\n'
        f'Distribution: {best}\nData points used: {sample_count}\n"""\n\n'
        "import numpy as np\nfrom scipy import stats\n\n"
        f"PLUGIN_API_VERSION = 1\nSAMPLE_COUNT = {sample_count}\n\n"
    )
    if best == "deterministic":
        raw_value = float(params[0] if params else fit.get("mean", 0.0))
        value = (
            int(round(raw_value))
            if config.get("parameter") in {"selected_in_edge", "selected_out_edge"}
            else raw_value
        )
        body = (
            f"VALUE = {value!r}\n\n"
            "def generate(size=1, seed=None):\n"
            "    return np.full(int(size), VALUE)\n\n"
            "def generate_data():\n"
            "    while True:\n"
            "        yield VALUE\n\n"
            "def create_generator(seed=None):\n"
            "    return generate_data()\n"
        )
    elif best == "categorical":
        choices = [int(value) for value in fit.get("categorical_values", [])]
        probabilities = [float(value) for value in params]
        body = (
            f"VALUES = np.asarray({choices!r}, dtype=int)\n"
            f"PROBABILITIES = np.asarray({probabilities!r}, dtype=float)\n\n"
            "def generate(size=1, seed=None):\n"
            "    return np.random.default_rng(seed).choice(VALUES, size=int(size), p=PROBABILITIES)\n\n"
            "def create_generator(seed=None):\n"
            "    rng = np.random.default_rng(seed)\n"
            "    return lambda: int(rng.choice(VALUES, p=PROBABILITIES))\n"
        )
    elif best == "poisson":
        body = f"PARAMETERS = {params!r}\n\ndef generate(size=1, seed=None):\n    return np.random.default_rng(seed).poisson(PARAMETERS[0], size=size)\n\ndef create_generator(seed=None):\n    rng = np.random.default_rng(seed)\n    return lambda: int(rng.poisson(PARAMETERS[0]))\n"
    elif best == "binom":
        body = f"PARAMETERS = {params!r}\n\ndef generate(size=1, seed=None):\n    return np.random.default_rng(seed).binomial(int(PARAMETERS[0]), PARAMETERS[1], size=size)\n\ndef create_generator(seed=None):\n    rng = np.random.default_rng(seed)\n    return lambda: int(rng.binomial(int(PARAMETERS[0]), PARAMETERS[1]))\n"
    elif best == "geom":
        body = f"PARAMETERS = {params!r}\n\ndef generate(size=1, seed=None):\n    return np.random.default_rng(seed).geometric(PARAMETERS[0], size=size) + PARAMETERS[1]\n\ndef create_generator(seed=None):\n    rng = np.random.default_rng(seed)\n    return lambda: int(rng.geometric(PARAMETERS[0]) + PARAMETERS[1])\n"
    elif best == "kde":
        reference = fit.get("reference_values", [])
        bandwidth = fit.get("kde", {}).get("bandwidth")
        body = (
            f"REFERENCE_VALUES = np.asarray({reference!r}, dtype=float)\n"
            f"BANDWIDTH = {bandwidth!r}\n\n"
            "def _draw(rng, size):\n"
            "    return REFERENCE_VALUES[rng.integers(0, len(REFERENCE_VALUES), size=size)] + rng.normal(0.0, BANDWIDTH, size=size)\n\n"
            + POSITIVE_SAMPLING_FUNCTIONS
        )
    else:
        body = (
            f"DISTRIBUTION = {best!r}\nPARAMETERS = {params!r}\n\n"
            "def _draw(rng, size):\n"
            "    return getattr(stats, DISTRIBUTION).rvs(*PARAMETERS, size=size, random_state=rng)\n\n"
            + POSITIVE_SAMPLING_FUNCTIONS
        )
    atomic_write_text(str(path), header + body)
    return str(path)


def write_policy_file(config: dict[str, Any], policy: str) -> str:
    """Write a durable edge-selection policy plugin for Digital Twin loading."""
    if policy not in {"RANDOM", "FIRST_AVAILABLE", "ROUND_ROBIN"}:
        raise ValueError(f"Unsupported edge-selection policy: {policy}")
    RVG_DIR.mkdir(parents=True, exist_ok=True)
    requested_stem = config.get("rvg_filename")
    if requested_stem:
        safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(requested_stem)).strip("._")
    else:
        safe_entity = re.sub(r"[^A-Za-z0-9_.-]+", "_", config["entity_id"])
        safe_parameter = re.sub(r"[^A-Za-z0-9_.-]+", "_", config["parameter"])
        safe_stem = f"{safe_entity}_{safe_parameter}"
    if not safe_stem:
        raise ValueError("Policy filename must contain at least one letter or number")
    path = RVG_DIR / f"{safe_stem}.py"
    content = (
        '"""Generated by DataFITR Parameter Drift Monitoring.\n'
        f'Entity: {config["entity_id"]}\nParameter: {config["parameter"]}\n'
        f'Routing policy: {policy}\n"""\n\n'
        "PLUGIN_API_VERSION = 1\n"
        "PLUGIN_KIND = \"edge_policy\"\n"
        f"POLICY = {policy!r}\n"
    )
    atomic_write_text(str(path), content)
    return str(path)


def create_policy_baseline(config: dict[str, Any]) -> dict[str, Any]:
    """Persist an Influx routing-policy configuration as a DT-loadable plugin."""
    policy = config.get("selection_policy")
    if not policy:
        raise ValueError("A routing policy must be selected")
    assignment = (
        f'{config["entity_id"]}.'
        f'{"in_edge_selection" if config.get("parameter") == "selected_in_edge" else "out_edge_selection"}'
        f'="{policy}"'
    )
    config["policy_assignment"] = assignment
    policy_file = write_policy_file(config, policy)
    baseline = {
        "configuration_id": config["id"], "created_at": utc_now(),
        "data_type": "policy", "fit_method": "policy",
        "best_distribution": "policy", "sample_count": 0,
        "selection_policy": policy, "policy_assignment": assignment,
        "rvg_file": policy_file,
    }
    baselines = load_baselines()
    baselines[config["id"]] = baseline
    write_json(BASELINE_FILE, baselines)
    results = load_results()
    results[config["id"]] = {
        "configuration_id": config["id"], "checked_at": utc_now(),
        "status": "configured", "selection_policy": policy,
        "policy_assignment": assignment, "rvg_file": policy_file,
        "message": "Routing policy saved as a Digital Twin policy plugin; drift detection is disabled.",
    }
    write_json(RESULT_FILE, results)
    return baseline


def create_baseline(reader: InfluxReader, config: dict[str, Any]) -> dict[str, Any]:
    if config.get("fit_method") == "policy":
        return create_policy_baseline(config)
    minimum = int(config.get("min_samples", 50))
    # A sample-count window smaller than min_samples could never be fitted.
    limit = max(int(config.get("baseline_points", minimum)), minimum) if config.get("baseline_mode") == "points" else None
    values = reader.values(config, config.get("baseline_window", "-7d"), limit)
    if len(values) < minimum:
        raise InsufficientSamples(len(values), minimum, "baseline")
    fit = fit_distribution(values, config)
    fit.update({
        "configuration_id": config["id"], "created_at": utc_now(), "version": str(uuid.uuid4()),
        "reference_values": values[:5000].tolist(),
    })
    fit["rvg_file"] = write_rvg_file(config, fit)
    baselines = load_baselines()
    baselines[config["id"]] = fit
    write_json(BASELINE_FILE, baselines)
    return fit


def check_drift(reader: InfluxReader, config: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    minimum = int(config.get("min_samples", 50))
    # A sample-count window smaller than min_samples could never be fitted.
    limit = max(int(config.get("check_points", minimum)), minimum) if config.get("check_mode") == "points" else None
    current = reader.values(config, config.get("check_window", "-7d"), limit)
    if len(current) < minimum:
        raise InsufficientSamples(len(current), minimum, "detection")
    reference = np.asarray(baseline["reference_values"], dtype=float)
    current_fit = fit_distribution(current, config)
    gof_method = config.get("gof_method", "ks")
    gof_statistic, gof_pvalue = two_sample_gof(
        reference, current, gof_method, int(config.get("bin_size", 20))
    )
    wasserstein = float(stats.wasserstein_distance(reference, current))
    scale = max(float(np.std(reference)), 1e-9)
    normalized_wasserstein = wasserstein / scale
    mean_shift = abs(float(np.mean(current)) - float(np.mean(reference))) / max(abs(float(np.mean(reference))), 1e-9) * 100
    std_shift = abs(float(np.std(current)) - float(np.std(reference))) / max(float(np.std(reference)), 1e-9) * 100
    reasons = []
    gof_threshold = float(config.get("gof_alpha", config.get("ks_alpha", 0.01)))
    gof_labels = {"ks": "KS test", "chi": "Chi-square test", "sse": "SSE test"}
    if gof_pvalue < gof_threshold:
        reasons.append(gof_labels.get(gof_method, "GOF test"))
    if normalized_wasserstein > NORMALIZED_WASSERSTEIN_THRESHOLD:
        reasons.append("Wasserstein distance")
    mean_shift_threshold = float(config.get(
        "mean_shift_threshold_pct",
        config.get("parameter_threshold", MEAN_STD_SHIFT_THRESHOLD_PCT),
    ))
    std_shift_threshold = float(config.get(
        "std_shift_threshold_pct", STD_SHIFT_THRESHOLD_PCT,
    ))
    if mean_shift > mean_shift_threshold:
        reasons.append("mean shift")
    if std_shift > std_shift_threshold:
        reasons.append("standard-deviation shift")
    return {
        "configuration_id": config["id"], "checked_at": utc_now(), "status": "drift" if reasons else "healthy",
        "drift": bool(reasons), "reasons": reasons,
        "gof_method": gof_method, "gof_statistic": gof_statistic,
        "gof_pvalue": gof_pvalue, "wasserstein": wasserstein,
        "normalized_wasserstein": normalized_wasserstein, "mean_shift_pct": mean_shift,
        "std_shift_pct": std_shift, "current_fit": current_fit,
        "current_values": current[:5000].tolist(),
    }


def run_due_checks(force: bool = False) -> dict[str, Any]:
    configs = load_configs()
    results = load_results()
    baselines = load_baselines()
    reader = InfluxReader()
    now = time.time()
    for config in configs:
        if not config.get("enabled", True):
            continue
        if config.get("fit_method") == "policy" and config.get("selection_policy"):
            create_policy_baseline(config)
            save_configs(configs)
            baselines = load_baselines()
            results = load_results()
            continue
        # Re-read every pass: the app, or a manual refit, may have replaced a
        # baseline while this loop was running, and a stale copy written back
        # whole would silently undo that refit.
        baselines = load_baselines()
        # Keep existing baselines available to Digital Twin even when the next
        # drift check is not due yet (also migrates older plugin filenames/API).
        baseline = baselines.get(config["id"])
        minimum = int(config.get("min_samples", 50))
        if baseline is not None and (
            len(baseline.get("reference_values", [])) < minimum
            or (
                config.get("baseline_mode") == "points"
                and int(baseline.get("sample_count", 0))
                > max(int(config.get("baseline_points", 0)), minimum)
            )
        ):
            # Fitted on too few readings, or before the per-table limit fix:
            # refetch the baseline window, which enforces min_samples.
            baselines.pop(config["id"])
            baseline = None
            force_refit = True
        else:
            force_refit = False
        if baseline is not None:
            selected = config.get("selected_distribution")
            needs_refit = (
                bool(selected) and baseline.get("best_distribution") != selected
            ) or not baseline.get("gof_measures")
            reference_values = baseline.get("reference_values", [])
            if reference_values and np.all(
                np.asarray(reference_values, dtype=float) == float(reference_values[0])
            ):
                needs_refit = baseline.get("best_distribution") != "deterministic"
            elif config.get("parameter") in {"selected_in_edge", "selected_out_edge"}:
                needs_refit = baseline.get("best_distribution") == "categorical"
            if needs_refit and reference_values:
                # Migrate legacy baselines and make the user's RVG choice durable.
                # The original reference sample remains fixed for drift detection.
                refreshed = fit_distribution(np.asarray(reference_values, dtype=float), config)
                refreshed.update({
                    "configuration_id": config["id"],
                    "created_at": baseline.get("created_at", utc_now()),
                    "version": str(uuid.uuid4()),
                    "reference_values": reference_values,
                })
                baseline = refreshed
            baseline["rvg_file"] = write_rvg_file(config, baseline)
            # Merge this entry into the file as it is now, not into the copy
            # read above, so concurrent writers keep their entries.
            baselines = load_baselines()
            baselines[config["id"]] = baseline
            write_json(BASELINE_FILE, baselines)
        previous = results.get(config["id"], {})
        try:
            last = datetime.fromisoformat(previous["checked_at"]).timestamp() if previous.get("checked_at") else 0
        except ValueError:
            last = 0
        if not force and not force_refit and now - last < int(config.get("interval_seconds", 300)):
            continue
        try:
            baseline = baselines.get(config["id"])
            if baseline is None:
                baseline = create_baseline(reader, config)
                baselines = load_baselines()
            result = check_drift(reader, config, baseline)
            prior_count = int(previous.get("consecutive_detections", 0))
            result["consecutive_detections"] = prior_count + 1 if result["drift"] else 0
            required = int(config.get("confirmations", 2))
            if result["drift"] and result["consecutive_detections"] < required:
                result["status"] = "warning"
        except InsufficientSamples as exc:
            result = {
                "configuration_id": config["id"], "checked_at": utc_now(),
                "status": "waiting", "available_samples": exc.available,
                "required_samples": exc.required,
                "needed_samples": exc.required - exc.available,
                "error": str(exc),
            }
        except Exception as exc:
            result = {"configuration_id": config["id"], "checked_at": utc_now(), "status": "error", "error": str(exc)}
        results = load_results()
        results[config["id"]] = result
        write_json(RESULT_FILE, results)
    return results
