from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "offline_analysis" / "outputs" / "figures"
STANDARDIZED_DIR = ROOT / "offline_analysis" / "outputs" / "standardized"
REFERENCE_IMAGE = ROOT / "斯坦福行人数据集" / "annotations" / "deathCircle" / "video0" / "reference.jpg"


OVERLAY_PARAMS = {
    "line_width": 1.0,
    "point_size": 0.0,
    "line_opacity": 0.88,
    "point_opacity": 0.0,
    "base_fade": 0.18,
    "color_mode": "density",
    "fixed_color": "#ff2a20",
    "neighborhood_radius": 1.0,
    "red_line_count": 130.0,
    "draw_lines": True,
    "draw_points": False,
    "invert_y": True,
}
OUTPUT_WIDTH = 960


def load_renderer():
    renderer_path = next(ROOT.rglob("trajectory_overlay_renderer.py"))
    spec = importlib.util.spec_from_file_location("trajectory_overlay_renderer", renderer_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dataframe_to_trajectory(renderer, df: pd.DataFrame, x_col: str, y_col: str, mode: str):
    records = []
    subset = df[["time_ms", "agent_id", x_col, y_col]].dropna()
    for order, row in enumerate(subset.itertuples(index=False)):
        records.append(
            renderer.TrajectoryRecord(
                time_ms=float(row.time_ms),
                agent_id=str(row.agent_id),
                x_world=float(getattr(row, x_col)),
                y_world=float(getattr(row, y_col)),
                decision_mode=mode,
                order=order,
            )
        )
    points = pd.DataFrame({"x": subset[x_col].astype(float), "y": subset[y_col].astype(float)}).to_numpy()
    return renderer.TrajectoryData(
        records=records,
        points=points,
        columns=["time_ms", "agent_id", "x_world", "y_world", "decision_mode"],
        row_count=len(records),
        agent_count=subset["agent_id"].nunique(),
        mode_count=1,
    )


def render_overlay(
    renderer,
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    mode: str,
    output_name: str,
    bounds,
    params_override: dict[str, object] | None = None,
    coordinate_source: str = "unity_world",
):
    base_image = Image.open(REFERENCE_IMAGE).convert("RGBA")
    if OUTPUT_WIDTH and base_image.width != OUTPUT_WIDTH:
        output_height = int(round(base_image.height * OUTPUT_WIDTH / base_image.width))
        base_image = base_image.resize((OUTPUT_WIDTH, output_height), Image.Resampling.LANCZOS)
    trajectory = dataframe_to_trajectory(renderer, df, x_col, y_col, mode)
    params = {**OVERLAY_PARAMS, **(params_override or {})}
    image, used_points, used_segments = renderer.render_trajectory_overlay(
        base_image=base_image,
        trajectory=trajectory,
        bounds=tuple(bounds),
        **params,
    )
    output_path = OUTPUT_DIR / output_name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output_path)
    return {
        "output": str(output_path),
        "used_points": used_points,
        "used_segments": used_segments,
        "row_count": trajectory.row_count,
        "agent_count": trajectory.agent_count,
        "output_size": list(image.size),
        "coordinate_source": coordinate_source,
        "bounds": list(bounds),
        **params,
    }


def main() -> None:
    renderer = load_renderer()
    config = json.loads((ROOT / "offline_analysis" / "outputs" / "analysis_config.json").read_text(encoding="utf-8"))
    unity_bounds = config["bounds"]["unity_world_bounds"]
    with Image.open(REFERENCE_IMAGE) as reference_image:
        real_image_bounds = (0.0, float(reference_image.width - 1), 0.0, float(reference_image.height - 1))

    llm = pd.concat([pd.read_csv(path) for path in sorted((STANDARDIZED_DIR / "llm").glob("*.csv"))], ignore_index=True)
    baseline = pd.concat([pd.read_csv(path) for path in sorted((STANDARDIZED_DIR / "baseline").glob("*.csv"))], ignore_index=True)
    real = pd.read_csv(STANDARDIZED_DIR / "stanford_deathCircle_video0_pedestrians.csv")

    metadata = {
        "fig4_1_llm_red_green_overlay": render_overlay(
            renderer, llm, "x_world", "y_world", "llm", "fig4_1_llm_red_green_overlay.png", unity_bounds
        ),
        "fig4_2_baseline_red_green_overlay": render_overlay(
            renderer, baseline, "x_world", "y_world", "baseline", "fig4_2_baseline_red_green_overlay.png", unity_bounds
        ),
        "fig4_3_real_red_green_overlay": render_overlay(
            renderer,
            real,
            "x_img",
            "y_img",
            "real",
            "fig4_3_real_red_green_overlay.png",
            real_image_bounds,
            params_override={"invert_y": False},
            coordinate_source="stanford_bbox_center_image_coordinates",
        ),
        "unity_world_bounds": unity_bounds,
        "real_image_bounds": real_image_bounds,
        "stanford_annotation_source": str(ROOT / "斯坦福行人数据集" / "annotations" / "deathCircle" / "video0" / "annotations.txt"),
        "base_image": str(REFERENCE_IMAGE),
        "output_width": OUTPUT_WIDTH,
    }
    metadata_path = OUTPUT_DIR / "fig4_red_green_overlay_params.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    print(metadata_path)


if __name__ == "__main__":
    main()
