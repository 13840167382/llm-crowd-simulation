from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


UNITY_COLUMNS = [
    "experiment_id",
    "scene",
    "run_id",
    "decision_mode",
    "time_ms",
    "frame",
    "agent_id",
    "agent_type",
    "x_world",
    "y_world",
    "speed",
    "current_zone",
    "destination_id",
    "decision_source",
]

STANFORD_COLUMNS = [
    "scene",
    "video_id",
    "source",
    "decision_mode",
    "agent_id",
    "track_id",
    "frame",
    "time_ms",
    "x_img",
    "y_img",
    "x_analysis",
    "y_analysis",
    "lost",
    "occluded",
    "generated",
    "label",
]

DECISION_COLUMNS = [
    "experiment_id",
    "batch_id",
    "time_ms",
    "agent_id",
    "crowd_type",
    "current_zone",
    "stay_ms",
    "patience",
    "algorithm_rec",
    "candidate_count",
    "chosen_exit",
    "decision_source",
    "confidence",
    "latency_ms",
    "reasoning",
]


@dataclass(frozen=True)
class AnalysisConfig:
    grid_size: float = 0.23
    window_ms: int = 30 * 60 * 1000
    normalization: str = "percentile_99"
    colormap: str = "magma"
    output_dpi: int = 200
    output_inches: float = 8.0
    stanford_fps: float = 30.0
    top_k: int = 10
    scene: str = "unity_campus"
    coordinate_mode: str = "stanford_minmax_to_unity_world_bounds"


def parse_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_int(value: object, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def infer_experiment_id(path: Path) -> str:
    match = re.search(r"path_traces_(exp_[A-Za-z0-9]+)_", path.name)
    return match.group(1) if match else path.stem


def standardize_unity_trace(path: Path, experiment_id: str, run_id: str, group_mode: str) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    unique_times = sorted({parse_float(row.get("time_ms")) for row in rows})
    frame_by_time = {time_ms: index for index, time_ms in enumerate(unique_times)}
    previous_by_agent: dict[str, tuple[float, float, float]] = {}
    output: list[dict[str, object]] = []

    for row in rows:
        time_ms = parse_float(row.get("time_ms"))
        agent_id = str(row.get("agent_id") or "").strip()
        x_world = parse_float(row.get("x_world"))
        y_world = parse_float(row.get("y_world"))
        decision_mode = str(row.get("decision_mode") or group_mode).strip() or group_mode
        if group_mode == "llm":
            decision_mode = "llm"
        elif group_mode == "baseline":
            decision_mode = "baseline"
        speed = 0.0
        previous = previous_by_agent.get(agent_id)
        if previous is not None:
            prev_time, prev_x, prev_y = previous
            delta_seconds = max((time_ms - prev_time) / 1000.0, 0.0)
            if delta_seconds > 0:
                speed = math.hypot(x_world - prev_x, y_world - prev_y) / delta_seconds
        previous_by_agent[agent_id] = (time_ms, x_world, y_world)
        decision_source = "llm" if group_mode == "llm" else "algorithm"

        output.append(
            {
                "experiment_id": experiment_id,
                "scene": "unity_campus",
                "run_id": run_id,
                "decision_mode": decision_mode,
                "time_ms": time_ms,
                "frame": frame_by_time[time_ms],
                "agent_id": agent_id,
                "agent_type": "",
                "x_world": x_world,
                "y_world": y_world,
                "speed": speed,
                "current_zone": "",
                "destination_id": "",
                "decision_source": decision_source,
            }
        )
    return output


def convert_stanford_annotation_lines(lines: Iterable[str], fps: float = 30.0) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in lines:
        parts = line.strip().split()
        if len(parts) < 10:
            continue
        track_id, xmin, ymin, xmax, ymax, frame, lost, occluded, generated = parts[:9]
        label = parts[9].strip('"')
        if label != "Pedestrian":
            continue
        x_center = (parse_float(xmin) + parse_float(xmax)) / 2.0
        y_center = (parse_float(ymin) + parse_float(ymax)) / 2.0
        track = str(track_id)
        rows.append(
            {
                "scene": "deathCircle",
                "video_id": "video0",
                "source": "stanford_drone_dataset",
                "decision_mode": "real",
                "agent_id": f"real_{track}",
                "track_id": track,
                "frame": parse_int(frame),
                "time_ms": parse_int(frame) / fps * 1000.0,
                "x_img": x_center,
                "y_img": y_center,
                "x_analysis": x_center,
                "y_analysis": y_center,
                "lost": parse_int(lost),
                "occluded": parse_int(occluded),
                "generated": parse_int(generated),
                "label": label,
            }
        )
    return rows


def write_csv(path: Path, rows: Iterable[dict[str, object]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_unity_groups(project_root: Path) -> dict[str, list[Path]]:
    return {
        "llm": sorted((project_root / "llm组").glob("*/path_traces*.csv")),
        "baseline": sorted((project_root / "无llm组").glob("*/path_traces*.csv")),
    }


def standardize_all_unity(project_root: Path, output_dir: Path) -> dict[str, pd.DataFrame]:
    groups = read_unity_groups(project_root)
    frames: dict[str, list[pd.DataFrame]] = {"llm": [], "baseline": []}
    for group_mode, paths in groups.items():
        for path in paths:
            run_id = path.parent.name
            experiment_id = infer_experiment_id(path)
            rows = standardize_unity_trace(path, experiment_id, run_id, group_mode)
            out_path = output_dir / "standardized" / group_mode / f"run_{run_id}_{experiment_id}.csv"
            write_csv(out_path, rows, UNITY_COLUMNS)
            frames[group_mode].append(pd.DataFrame(rows, columns=UNITY_COLUMNS))
    return {key: pd.concat(value, ignore_index=True) if value else pd.DataFrame(columns=UNITY_COLUMNS) for key, value in frames.items()}


def convert_stanford_file(annotation_path: Path, output_path: Path, fps: float) -> pd.DataFrame:
    with annotation_path.open("r", encoding="utf-8") as handle:
        rows = convert_stanford_annotation_lines(handle, fps=fps)
    write_csv(output_path, rows, STANFORD_COLUMNS)
    return pd.DataFrame(rows, columns=STANFORD_COLUMNS)


def bounds_from_points(x: pd.Series, y: pd.Series) -> tuple[float, float, float, float]:
    return (float(x.min()), float(x.max()), float(y.min()), float(y.max()))


def map_points_to_bounds(
    x: pd.Series,
    y: pd.Series,
    source_bounds: tuple[float, float, float, float],
    target_bounds: tuple[float, float, float, float],
) -> tuple[np.ndarray, np.ndarray]:
    src_x_min, src_x_max, src_y_min, src_y_max = source_bounds
    dst_x_min, dst_x_max, dst_y_min, dst_y_max = target_bounds
    x_den = max(src_x_max - src_x_min, 1e-9)
    y_den = max(src_y_max - src_y_min, 1e-9)
    mapped_x = dst_x_min + (x.to_numpy(dtype=float) - src_x_min) / x_den * (dst_x_max - dst_x_min)
    mapped_y = dst_y_min + (y.to_numpy(dtype=float) - src_y_min) / y_den * (dst_y_max - dst_y_min)
    return mapped_x, mapped_y


def apply_stanford_analysis_coordinates(stanford_df: pd.DataFrame, unity_df: pd.DataFrame, output_path: Path) -> tuple[pd.DataFrame, dict[str, tuple[float, float, float, float]]]:
    unity_bounds = bounds_from_points(unity_df["x_world"], unity_df["y_world"])
    real_bounds = bounds_from_points(stanford_df["x_img"], stanford_df["y_img"])
    x_analysis, y_analysis = map_points_to_bounds(stanford_df["x_img"], stanford_df["y_img"], real_bounds, unity_bounds)
    mapped = stanford_df.copy()
    mapped["x_analysis"] = x_analysis
    mapped["y_analysis"] = y_analysis
    write_csv(output_path, mapped.to_dict("records"), STANFORD_COLUMNS)
    return mapped, {"unity_world_bounds": unity_bounds, "stanford_image_bounds": real_bounds}


def filter_time_window(df: pd.DataFrame, window_ms: int) -> pd.DataFrame:
    if df.empty:
        return df
    time_values = df["time_ms"].astype(float)
    start = float(time_values.min())
    return df.loc[(time_values - start) <= window_ms].copy()


def heatmap_matrix(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    bounds: tuple[float, float, float, float],
    grid_size: float,
    window_ms: int,
) -> np.ndarray:
    windowed = filter_time_window(df, window_ms)
    x_min, x_max, y_min, y_max = bounds
    x_edges = np.arange(x_min, x_max + grid_size, grid_size)
    y_edges = np.arange(y_min, y_max + grid_size, grid_size)
    if len(x_edges) < 2:
        x_edges = np.array([x_min, x_min + grid_size])
    if len(y_edges) < 2:
        y_edges = np.array([y_min, y_min + grid_size])
    hist, _, _ = np.histogram2d(
        windowed[x_col].to_numpy(dtype=float),
        windowed[y_col].to_numpy(dtype=float),
        bins=[x_edges, y_edges],
    )
    return hist.T


def normalize_matrix(matrix: np.ndarray, method: str = "percentile_99") -> np.ndarray:
    values = matrix.astype(float)
    if values.size == 0:
        return values
    if method == "percentile_99":
        positive = values[values > 0]
        scale = float(np.percentile(positive, 99)) if positive.size else 0.0
    elif method == "max":
        scale = float(values.max())
    elif method == "sum":
        scale = float(values.sum())
    else:
        scale = 1.0
    if scale <= 0:
        return np.zeros_like(values, dtype=float)
    return np.clip(values / scale, 0.0, 1.0) if method != "sum" else values / scale


def save_matrix(path_prefix: Path, matrix: np.ndarray) -> None:
    path_prefix.parent.mkdir(parents=True, exist_ok=True)
    np.save(path_prefix.with_suffix(".npy"), matrix)
    np.savetxt(path_prefix.with_suffix(".csv"), matrix, delimiter=",", fmt="%.8f")


def save_heatmap(path: Path, matrix: np.ndarray, title: str, cmap: str, dpi: int, inches: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(inches, inches), dpi=dpi)
    ax.imshow(matrix, origin="lower", cmap=cmap, interpolation="nearest", vmin=0.0, vmax=1.0)
    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.tight_layout(pad=0.2)
    fig.savefig(path)
    plt.close(fig)


def save_difference(path: Path, matrix: np.ndarray, title: str, dpi: int, inches: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    limit = max(float(np.max(np.abs(matrix))), 1e-9)
    fig, ax = plt.subplots(figsize=(inches, inches), dpi=dpi)
    ax.imshow(matrix, origin="lower", cmap="coolwarm", interpolation="nearest", vmin=-limit, vmax=limit)
    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.tight_layout(pad=0.2)
    fig.savefig(path)
    plt.close(fig)


def jensen_shannon_divergence(left: np.ndarray, right: np.ndarray) -> float:
    a = left.astype(float).ravel()
    b = right.astype(float).ravel()
    a = np.maximum(a, 0.0)
    b = np.maximum(b, 0.0)
    if a.sum() <= 0 and b.sum() <= 0:
        return 0.0
    if a.sum() <= 0 or b.sum() <= 0:
        return 1.0
    p = a / a.sum()
    q = b / b.sum()
    m = 0.5 * (p + q)

    def kl_divergence(x: np.ndarray, y: np.ndarray) -> float:
        mask = x > 0
        return float(np.sum(x[mask] * np.log2(x[mask] / y[mask])))

    return 0.5 * kl_divergence(p, m) + 0.5 * kl_divergence(q, m)


def top_k_indices(matrix: np.ndarray, top_k: int) -> set[int]:
    flat = matrix.astype(float).ravel()
    k = min(max(int(top_k), 1), flat.size)
    return set(np.argpartition(flat, -k)[-k:].tolist())


def hotspot_center(matrix: np.ndarray, top_k: int, grid_size: float) -> tuple[float, float]:
    flat = matrix.astype(float).ravel()
    k = min(max(int(top_k), 1), flat.size)
    indices = np.argpartition(flat, -k)[-k:]
    weights = flat[indices]
    rows, cols = np.unravel_index(indices, matrix.shape)
    if weights.sum() <= 0:
        weights = np.ones_like(weights, dtype=float)
    x = float(np.average(cols + 0.5, weights=weights) * grid_size)
    y = float(np.average(rows + 0.5, weights=weights) * grid_size)
    return x, y


def compute_distribution_metrics(candidate: np.ndarray, real: np.ndarray, grid_size: float, top_k: int = 10) -> dict[str, float]:
    candidate_top = top_k_indices(candidate, top_k)
    real_top = top_k_indices(real, top_k)
    overlap = len(candidate_top & real_top) / max(len(real_top), 1)
    candidate_center = hotspot_center(candidate, top_k, grid_size)
    real_center = hotspot_center(real, top_k, grid_size)
    center_distance = math.hypot(candidate_center[0] - real_center[0], candidate_center[1] - real_center[1])
    return {
        "jsd": jensen_shannon_divergence(candidate, real),
        "recall_top10": float(overlap),
        "hotspot_center_distance": float(center_distance),
    }


def gaussian_kernel1d(sigma: float) -> np.ndarray:
    sigma = max(float(sigma), 1e-6)
    radius = max(1, int(math.ceil(3 * sigma)))
    positions = np.arange(-radius, radius + 1, dtype=float)
    kernel = np.exp(-(positions * positions) / (2 * sigma * sigma))
    return kernel / kernel.sum()


def convolve_axis_edge(matrix: np.ndarray, kernel: np.ndarray, axis: int) -> np.ndarray:
    pad = len(kernel) // 2
    pad_width = [(0, 0)] * matrix.ndim
    pad_width[axis] = (pad, pad)
    padded = np.pad(matrix, pad_width, mode="edge")
    output = np.zeros_like(matrix, dtype=float)
    if axis == 0:
        for offset, weight in enumerate(kernel):
            output += weight * padded[offset : offset + matrix.shape[0], :]
    else:
        for offset, weight in enumerate(kernel):
            output += weight * padded[:, offset : offset + matrix.shape[1]]
    return output


def gaussian_smooth(matrix: np.ndarray, sigma: float) -> np.ndarray:
    kernel = gaussian_kernel1d(sigma)
    return convolve_axis_edge(convolve_axis_edge(matrix.astype(float), kernel, axis=0), kernel, axis=1)


def percentile_hotspot_mask(matrix: np.ndarray, percentile: float) -> np.ndarray:
    values = matrix.astype(float)
    positive = values[values > 0]
    if positive.size == 0:
        return np.zeros_like(values, dtype=bool)
    threshold = float(np.percentile(positive, percentile))
    return values >= threshold


def dilate_mask(mask: np.ndarray, radius_cells: int) -> np.ndarray:
    radius_cells = max(int(radius_cells), 0)
    if radius_cells == 0:
        return mask.copy()
    result = np.zeros_like(mask, dtype=bool)
    ys, xs = np.nonzero(mask)
    height, width = mask.shape
    for y, x in zip(ys, xs):
        y_min = max(0, y - radius_cells)
        y_max = min(height, y + radius_cells + 1)
        x_min = max(0, x - radius_cells)
        x_max = min(width, x + radius_cells + 1)
        result[y_min:y_max, x_min:x_max] = True
    return result


def tolerant_hotspot_f1(candidate: np.ndarray, real: np.ndarray, percentile: float, tolerance_cells: int) -> tuple[float, float, float]:
    candidate_mask = percentile_hotspot_mask(candidate, percentile)
    real_mask = percentile_hotspot_mask(real, percentile)
    candidate_dilated = dilate_mask(candidate_mask, tolerance_cells)
    real_dilated = dilate_mask(real_mask, tolerance_cells)
    candidate_hits = np.logical_and(candidate_mask, real_dilated).sum()
    real_hits = np.logical_and(real_mask, candidate_dilated).sum()
    precision = float(candidate_hits / candidate_mask.sum()) if candidate_mask.sum() else 0.0
    recall = float(real_hits / real_mask.sum()) if real_mask.sum() else 0.0
    f1 = float(2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    return precision, recall, f1


def compute_supplemental_metrics(candidate: np.ndarray, real: np.ndarray, grid_size: float) -> dict[str, float]:
    tolerance_cells = 2
    precision_5, recall_5, f1_5 = tolerant_hotspot_f1(candidate, real, percentile=95.0, tolerance_cells=tolerance_cells)
    precision_10, recall_10, f1_10 = tolerant_hotspot_f1(candidate, real, percentile=90.0, tolerance_cells=tolerance_cells)
    candidate_coverage = int((candidate > 0).sum())
    real_coverage = int((real > 0).sum())
    coverage_abs_diff = abs(candidate_coverage - real_coverage)
    coverage_similarity = 1.0 - coverage_abs_diff / max(candidate_coverage, real_coverage, 1)
    return {
        "hotspot_precision_top5pct": precision_5,
        "hotspot_recall_top5pct": recall_5,
        "hotspot_f1_top5pct": f1_5,
        "hotspot_f1_top10pct": f1_10,
        "coverage_count_candidate": float(candidate_coverage),
        "coverage_count_real": float(real_coverage),
        "coverage_count_abs_diff": float(coverage_abs_diff),
        "coverage_similarity": float(coverage_similarity),
        "tolerance_cells": float(tolerance_cells),
        "tolerance_world_units": float(tolerance_cells * grid_size),
    }


def parse_decision_transcript(text: str) -> list[dict[str, object]]:
    experiment_match = re.search(r"Experiment:\s*(\S+)", text)
    experiment_id = experiment_match.group(1) if experiment_match else ""
    events: list[dict[str, object]] = []
    current_batch = ""
    first_batch_time: int | None = None
    current: dict[str, object] | None = None
    pending_reason_event: dict[str, object] | None = None

    def finish_current() -> None:
        nonlocal current
        if current is not None and current.get("decision_source"):
            events.append({column: current.get(column, "") for column in DECISION_COLUMNS})
        current = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        batch_match = re.match(r"=== Batch:\s*([^|]+)\|", line)
        if batch_match:
            finish_current()
            current_batch = batch_match.group(1).strip()
            batch_time_match = re.search(r"(\d{10,})", current_batch)
            if batch_time_match:
                batch_time = int(batch_time_match.group(1))
                if first_batch_time is None:
                    first_batch_time = batch_time
            continue

        agent_match = re.match(r"\[Agent:\s*([^#\]]+)", line)
        if agent_match:
            finish_current()
            batch_time_match = re.search(r"(\d{10,})", current_batch)
            batch_time_ms = ""
            if batch_time_match and first_batch_time is not None:
                batch_time_ms = int(batch_time_match.group(1)) - first_batch_time
            current = {
                "experiment_id": experiment_id,
                "batch_id": current_batch,
                "time_ms": batch_time_ms,
                "agent_id": agent_match.group(1).strip(),
                "crowd_type": "",
                "current_zone": "",
                "stay_ms": "",
                "patience": "",
                "algorithm_rec": "",
                "candidate_count": "",
                "chosen_exit": "",
                "decision_source": "",
                "confidence": "",
                "latency_ms": "",
                "reasoning": "",
            }
            continue

        if current is None:
            continue

        state_match = re.search(r"CrowdType:\s*([^|]+)\|\s*Zone:\s*([^|]+)\|\s*Stay:\s*([0-9.]+)ms\s*\|\s*Patience:\s*([0-9.]+)", line)
        if state_match:
            current["crowd_type"] = state_match.group(1).strip()
            current["current_zone"] = state_match.group(2).strip()
            current["stay_ms"] = parse_float(state_match.group(3))
            current["patience"] = parse_float(state_match.group(4))
            continue

        alg_match = re.search(r"AlgorithmRec:\s*([A-Za-z0-9_\\-]+)", line)
        if alg_match:
            current["algorithm_rec"] = alg_match.group(1)

        exits_match = re.search(r"Exits\s*\((\d+)\)", line)
        if exits_match:
            current["candidate_count"] = parse_int(exits_match.group(1))
            continue

        decision_match = re.search(r"\[([A-Za-z_]+)\]\s*exit=([^\s]+)\s*confidence=([0-9.]+)(?:.*?latency_ms=([0-9.]+))?", line)
        if decision_match:
            current["decision_source"] = decision_match.group(1)
            current["chosen_exit"] = decision_match.group(2)
            current["confidence"] = parse_float(decision_match.group(3))
            if decision_match.group(4):
                current["latency_ms"] = parse_float(decision_match.group(4))
            pending_reason_event = current
            continue

        if line.startswith("Reasoning:") and pending_reason_event is current:
            current["reasoning"] = line.split("Reasoning:", 1)[1].strip()
            pending_reason_event = None

    finish_current()
    return events


def parse_all_decision_transcripts(project_root: Path, output_path: Path) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for group, folder in [("llm", project_root / "llm组"), ("baseline", project_root / "无llm组")]:
        for path in sorted(folder.glob("*/transcript*.txt")):
            text = path.read_text(encoding="utf-8", errors="replace")
            parsed_rows = parse_decision_transcript(text)
            if group == "llm":
                for row in parsed_rows:
                    if str(row.get("decision_source", "")).lower() != "fallback":
                        row["decision_source"] = "llm"
            rows.extend(parsed_rows)
    write_csv(output_path, rows, DECISION_COLUMNS)
    return pd.DataFrame(rows, columns=DECISION_COLUMNS)


def decision_statistics(events: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if events.empty:
        return pd.DataFrame(columns=["scope", "crowd_type", "destination_id", "metric", "value"])
    total = len(events)
    sources = events["decision_source"].fillna("").astype(str)
    latency = pd.to_numeric(events["latency_ms"], errors="coerce")
    consistency = (events["chosen_exit"].astype(str) == events["algorithm_rec"].astype(str)).mean()
    summary = {
        "total_decisions": total,
        "llm_success_count": int((sources == "llm").sum()),
        "fallback_count": int((sources == "fallback").sum()),
        "fallback_ratio": float((sources == "fallback").sum() / total),
        "random_ratio": float((sources == "random").sum() / total),
        "algorithm_ratio": float((sources == "algorithm").sum() / total),
        "llm_ratio": float((sources == "llm").sum() / total),
        "fallback_source_ratio": float((sources == "fallback").sum() / total),
        "average_latency_ms": float(latency.mean()) if latency.notna().any() else "",
        "p95_latency_ms": float(latency.quantile(0.95)) if latency.notna().any() else "",
        "llm_algorithm_recommendation_agreement": float(
            (events.loc[sources == "llm", "chosen_exit"].astype(str) == events.loc[sources == "llm", "algorithm_rec"].astype(str)).mean()
        )
        if (sources == "llm").any()
        else "",
        "overall_algorithm_recommendation_agreement": float(consistency),
    }
    for metric, value in summary.items():
        rows.append({"scope": "overall", "crowd_type": "", "destination_id": "", "metric": metric, "value": value})

    grouped = events.groupby(["crowd_type", "chosen_exit"], dropna=False).size().reset_index(name="count")
    totals = events.groupby("crowd_type", dropna=False).size().to_dict()
    for _, row in grouped.iterrows():
        crowd_type = str(row["crowd_type"])
        count = int(row["count"])
        ratio = count / max(int(totals.get(row["crowd_type"], 0)), 1)
        rows.append({"scope": "destination_distribution", "crowd_type": crowd_type, "destination_id": row["chosen_exit"], "metric": "count", "value": count})
        rows.append({"scope": "destination_distribution", "crowd_type": crowd_type, "destination_id": row["chosen_exit"], "metric": "ratio", "value": ratio})
    return pd.DataFrame(rows)


def performance_statistics(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for group, df in frames.items():
        if df.empty:
            continue
        for experiment_id, exp_df in df.groupby("experiment_id"):
            speed = pd.to_numeric(exp_df["speed"], errors="coerce")
            time_ms = pd.to_numeric(exp_df["time_ms"], errors="coerce")
            rows.append(
                {
                    "decision_group": group,
                    "experiment_id": experiment_id,
                    "rows": int(len(exp_df)),
                    "agent_count": int(exp_df["agent_id"].nunique()),
                    "duration_ms": float(time_ms.max() - time_ms.min()),
                    "avg_speed": float(speed.mean()),
                    "p95_speed": float(speed.quantile(0.95)),
                }
            )
    return pd.DataFrame(rows)


def save_table(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def build_heatmaps(
    real_df: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
    bounds: tuple[float, float, float, float],
    config: AnalysisConfig,
    output_dir: Path,
) -> pd.DataFrame:
    matrix_dir = output_dir / "matrices"
    figure_dir = output_dir / "figures"
    real_raw = heatmap_matrix(real_df, "x_analysis", "y_analysis", bounds, config.grid_size, config.window_ms)
    real = normalize_matrix(real_raw, config.normalization)
    save_matrix(matrix_dir / "real_heatmap_matrix", real)
    save_heatmap(figure_dir / "fig5_real_heatmap.png", real, "Real pedestrian heatmap", config.colormap, config.output_dpi, config.output_inches)

    metrics_rows: list[dict[str, object]] = []
    supplemental_rows: list[dict[str, object]] = []
    mean_matrices: dict[str, np.ndarray] = {}
    for group in ["baseline", "llm"]:
        df = frames[group]
        run_matrices = []
        for run_id, run_df in df.groupby("run_id"):
            raw = heatmap_matrix(run_df, "x_world", "y_world", bounds, config.grid_size, config.window_ms)
            matrix = normalize_matrix(raw, config.normalization)
            run_matrices.append(matrix)
            save_matrix(matrix_dir / f"{group}_run_{run_id}_heatmap_matrix", matrix)
            metrics = compute_distribution_metrics(matrix, real, config.grid_size, config.top_k)
            metrics_rows.append({"comparison": f"{group}_run_{run_id}_vs_real", **metrics})
            supplemental = compute_supplemental_metrics(matrix, real, config.grid_size)
            supplemental_rows.append({"comparison": f"{group}_run_{run_id}_vs_real", **supplemental})
        if run_matrices:
            mean_matrix = np.mean(np.stack(run_matrices, axis=0), axis=0)
        else:
            mean_matrix = np.zeros_like(real)
        mean_matrices[group] = mean_matrix
        save_matrix(matrix_dir / f"{group}_mean_heatmap_matrix", mean_matrix)
        name = "baseline" if group == "baseline" else "llm"
        save_heatmap(figure_dir / f"fig5_{name}_mean_heatmap.png", mean_matrix, f"{name.upper()} mean heatmap", config.colormap, config.output_dpi, config.output_inches)
        difference = mean_matrix - real
        save_matrix(matrix_dir / f"difference_{name}_real_matrix", difference)
        save_difference(figure_dir / f"fig5_difference_{name}_real.png", difference, f"{name.upper()} - real", config.output_dpi, config.output_inches)
        metrics = compute_distribution_metrics(mean_matrix, real, config.grid_size, config.top_k)
        metrics_rows.append({"comparison": f"{group}_vs_real", **metrics})
        supplemental = compute_supplemental_metrics(mean_matrix, real, config.grid_size)
        supplemental_rows.append({"comparison": f"{group}_vs_real", **supplemental})

    if "baseline" in mean_matrices and "llm" in mean_matrices:
        metrics = compute_distribution_metrics(mean_matrices["llm"], mean_matrices["baseline"], config.grid_size, config.top_k)
        metrics_rows.append({"comparison": "llm_vs_baseline", **metrics})
        supplemental = compute_supplemental_metrics(mean_matrices["llm"], mean_matrices["baseline"], config.grid_size)
        supplemental_rows.append({"comparison": "llm_vs_baseline", **supplemental})

    metrics_df = pd.DataFrame(metrics_rows, columns=["comparison", "jsd", "recall_top10", "hotspot_center_distance"])
    supplemental_df = pd.DataFrame(
        supplemental_rows,
        columns=[
            "comparison",
            "hotspot_precision_top5pct",
            "hotspot_recall_top5pct",
            "hotspot_f1_top5pct",
            "hotspot_f1_top10pct",
            "coverage_count_candidate",
            "coverage_count_real",
            "coverage_count_abs_diff",
            "coverage_similarity",
            "tolerance_cells",
            "tolerance_world_units",
        ],
    )
    return metrics_df, supplemental_df


def write_results_summary(
    path: Path,
    inputs: dict[str, object],
    config: AnalysisConfig,
    bounds: dict[str, tuple[float, float, float, float]],
    outputs: dict[str, str],
    metrics: pd.DataFrame,
    supplemental_metrics: pd.DataFrame,
    decision_stats: pd.DataFrame,
) -> None:
    paper_values = []
    for _, row in metrics.iterrows():
        if row["comparison"] in {"baseline_vs_real", "llm_vs_real", "llm_vs_baseline"}:
            paper_values.append(
                f"- {row['comparison']}: JSD={row['jsd']:.4f}, "
                f"Top-10 recall={row['recall_top10']:.4f}, hotspot center distance={row['hotspot_center_distance']:.4f}"
            )
    fallback_row = decision_stats.loc[(decision_stats["scope"] == "overall") & (decision_stats["metric"] == "fallback_ratio")]
    if not fallback_row.empty:
        paper_values.append(f"- fallback_ratio={float(fallback_row.iloc[0]['value']):.4f}")
    if not supplemental_metrics.empty:
        for comparison in ["baseline_vs_real", "llm_vs_real", "llm_vs_baseline"]:
            row = supplemental_metrics.loc[supplemental_metrics["comparison"] == comparison]
            if not row.empty:
                item = row.iloc[0]
                paper_values.append(
                    f"- {comparison} supplemental: Top-5% tolerant hotspot F1={item['hotspot_f1_top5pct']:.4f}, "
                    f"coverage similarity={item['coverage_similarity']:.4f}"
                )

    text = f"""# Results Summary

## Input Files
- LLM traces: {inputs['llm_traces']}
- Baseline traces: {inputs['baseline_traces']}
- Stanford annotation: {inputs['stanford_annotation']}
- Decision transcripts: {inputs['decision_transcripts']}

## Processing Flow
1. Standardized Unity trajectory CSV files to the paper schema.
2. Converted Stanford Drone Dataset deathCircle/video0 annotations to pedestrian center-point trajectories.
3. Mapped Stanford image coordinates into the Unity world-coordinate bounds using min-max affine normalization.
4. Generated heatmap matrices with one offline script and one shared parameter set.
5. Computed distribution metrics against the real pedestrian heatmap.
6. Parsed decision transcripts and summarized decision source, fallback, agreement, latency, and destination distribution.
7. Red-green trajectory overlays are generated by `offline_analysis/generate_red_green_overlays.py`; the Stanford overlay uses raw `x_img/y_img` bbox-center image coordinates from the full deathCircle/video0 annotations.

## Heatmap Parameters
- grid_size: {config.grid_size}
- window_ms: {config.window_ms} ({config.window_ms / 60000:.1f} min)
- normalization: {config.normalization}
- colormap: {config.colormap}
- output_resolution: {int(config.output_dpi * config.output_inches)} x {int(config.output_dpi * config.output_inches)}
- coordinate_mode: {config.coordinate_mode}
- unity_world_bounds: {bounds.get('unity_world_bounds')}
- stanford_image_bounds: {bounds.get('stanford_image_bounds')}

## Metric Methods
- Jensen-Shannon Divergence: base-2 Jensen-Shannon divergence between normalized nonnegative heatmap distributions.
- Top-k hotspot recall: overlap ratio between the top {config.top_k} cells of simulated and real heatmaps.
- Hotspot center distance: Euclidean distance between weighted centers of the top {config.top_k} hotspot cells, measured in analysis/world-coordinate units.
- Supplemental hotspot F1: precision/recall/F1 for top-percentile hotspot cells with a two-cell spatial tolerance.
- Supplemental coverage similarity: 1 minus the normalized difference between nonzero heatmap cell counts.

## Output Files
"""
    for label, output_path in outputs.items():
        text += f"- {label}: {output_path}\n"

    text += "\n## Values Ready For Paper\n"
    text += "\n".join(paper_values) if paper_values else "- No metric rows were produced.\n"
    text += """

## Parameters Requiring Manual Confirmation
- Whether Stanford image coordinates should be mapped by manually calibrated homography instead of min-max affine normalization.
- Whether `grid_size = 0.23` should be interpreted strictly as Unity world meters after cross-source mapping.
- Whether LLM group run 1 with original `decision_mode = fixedpath` should remain in the LLM average or be excluded/re-run.
- Whether transcript latency should be left blank when not explicitly exported, or recovered from backend logs if available.
- Whether the 30-minute window should start from the first recorded frame of each run, as implemented here.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig")


def run_pipeline(project_root: Path, output_dir: Path, config: AnalysisConfig) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = standardize_all_unity(project_root, output_dir)
    all_unity = pd.concat([frames["llm"], frames["baseline"]], ignore_index=True)
    stanford_annotation = project_root / "斯坦福行人数据集" / "annotations" / "deathCircle" / "video0" / "annotations.txt"
    stanford_raw_path = output_dir / "standardized" / "stanford_deathCircle_video0_raw.csv"
    stanford_mapped_path = output_dir / "standardized" / "stanford_deathCircle_video0_pedestrians.csv"
    stanford_df = convert_stanford_file(stanford_annotation, stanford_raw_path, config.stanford_fps)
    stanford_df, coord_bounds = apply_stanford_analysis_coordinates(stanford_df, all_unity, stanford_mapped_path)
    unity_bounds = coord_bounds["unity_world_bounds"]

    metrics, supplemental_metrics = build_heatmaps(stanford_df, frames, unity_bounds, config, output_dir)
    metrics_path = output_dir / "tables" / "table5_heatmap_metrics.csv"
    save_table(metrics_path, metrics)
    supplemental_metrics_path = output_dir / "tables" / "table5_supplemental_heatmap_metrics.csv"
    save_table(supplemental_metrics_path, supplemental_metrics)

    decision_events_path = output_dir / "tables" / "decision_events.csv"
    decision_events = parse_all_decision_transcripts(project_root, decision_events_path)
    decision_stats = decision_statistics(decision_events)
    decision_stats_path = output_dir / "tables" / "table5_decision_statistics.csv"
    save_table(decision_stats_path, decision_stats)

    performance = performance_statistics(frames)
    performance_path = output_dir / "tables" / "table5_performance_statistics.csv"
    save_table(performance_path, performance)

    config_path = output_dir / "analysis_config.json"
    config_payload = {**config.__dict__, "bounds": coord_bounds}
    config_path.write_text(json.dumps(config_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    outputs = {
        "real_heatmap": output_dir / "figures" / "fig5_real_heatmap.png",
        "baseline_mean_heatmap": output_dir / "figures" / "fig5_baseline_mean_heatmap.png",
        "llm_mean_heatmap": output_dir / "figures" / "fig5_llm_mean_heatmap.png",
        "baseline_difference": output_dir / "figures" / "fig5_difference_baseline_real.png",
        "llm_difference": output_dir / "figures" / "fig5_difference_llm_real.png",
        "heatmap_metrics": metrics_path,
        "supplemental_heatmap_metrics": supplemental_metrics_path,
        "decision_statistics": decision_stats_path,
        "performance_statistics": performance_path,
        "decision_events": decision_events_path,
        "stanford_pedestrians": stanford_mapped_path,
        "analysis_config": config_path,
        "fig4_llm_red_green_overlay": output_dir / "figures" / "fig4_1_llm_red_green_overlay.png",
        "fig4_baseline_red_green_overlay": output_dir / "figures" / "fig4_2_baseline_red_green_overlay.png",
        "fig4_real_red_green_overlay": output_dir / "figures" / "fig4_3_real_red_green_overlay.png",
        "fig4_red_green_overlay_params": output_dir / "figures" / "fig4_red_green_overlay_params.json",
    }
    summary_outputs = {key: str(value) for key, value in outputs.items()}
    write_results_summary(
        output_dir / "results_summary.md",
        inputs={
            "llm_traces": [str(path) for path in read_unity_groups(project_root)["llm"]],
            "baseline_traces": [str(path) for path in read_unity_groups(project_root)["baseline"]],
            "stanford_annotation": str(stanford_annotation),
            "decision_transcripts": [str(path) for path in sorted((project_root / "llm组").glob("*/transcript*.txt")) + sorted((project_root / "无llm组").glob("*/transcript*.txt"))],
        },
        config=config,
        bounds=coord_bounds,
        outputs=summary_outputs,
        metrics=metrics,
        supplemental_metrics=supplemental_metrics,
        decision_stats=decision_stats,
    )
    outputs["results_summary"] = output_dir / "results_summary.md"
    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate offline crowd simulation heatmaps and metrics.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, default=Path.cwd() / "offline_analysis" / "outputs")
    parser.add_argument("--grid-size", type=float, default=0.23)
    parser.add_argument("--window-minutes", type=float, default=30.0)
    parser.add_argument("--normalization", choices=["percentile_99", "max", "sum", "none"], default="percentile_99")
    parser.add_argument("--colormap", default="magma")
    parser.add_argument("--stanford-fps", type=float, default=30.0)
    parser.add_argument("--top-k", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = AnalysisConfig(
        grid_size=args.grid_size,
        window_ms=int(args.window_minutes * 60 * 1000),
        normalization=args.normalization,
        colormap=args.colormap,
        stanford_fps=args.stanford_fps,
        top_k=args.top_k,
    )
    outputs = run_pipeline(args.project_root, args.output_dir, config)
    print("Generated outputs:")
    for key, path in outputs.items():
        print(f"{key}: {path}")


if __name__ == "__main__":
    main()
