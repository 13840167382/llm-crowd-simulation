import csv
import hashlib
import os
import shlex
import threading
import tkinter as tk
from dataclasses import dataclass
from tkinter import colorchooser, filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageTk


APP_TITLE = "轨迹叠加图生成器"
SUPPORTED_IMAGES = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")
EXPERIMENT_WORLD_BOUNDS = (-24.096, 24.045, -16.398, 40.689)


@dataclass
class TrajectoryRecord:
    time_ms: float
    agent_id: str
    x_world: float
    y_world: float
    decision_mode: str
    order: int


@dataclass
class TrajectoryData:
    records: list[TrajectoryRecord]
    points: np.ndarray
    columns: list[str]
    row_count: int
    agent_count: int
    mode_count: int
    source_format: str = "table"
    coordinate_system: str = "world"


@dataclass
class RenderGeometry:
    pixel_groups: list[list[tuple[TrajectoryRecord, tuple[int, int]]]]
    time_range: tuple[float, float]
    order_range: tuple[int, int]
    used_points: int


@dataclass
class NearbyLineIndex:
    grid: dict[tuple[int, int], set[int]]
    cell_size: float


def parse_float(value: str, fallback: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def detect_delimiter(sample: str) -> str | None:
    if "\t" in sample:
        return "\t"
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t ")
        return dialect.delimiter
    except csv.Error:
        return None


def make_trajectory_data(
    records: list[TrajectoryRecord],
    columns: list[str],
    source_format: str = "table",
    coordinate_system: str = "world",
) -> TrajectoryData:
    if not records:
        raise ValueError("没有读到可用的轨迹坐标。")
    points = np.asarray([(record.x_world, record.y_world) for record in records], dtype=np.float32)
    agent_count = len({record.agent_id for record in records})
    mode_count = len({record.decision_mode for record in records})
    return TrajectoryData(records, points, columns, len(records), agent_count, mode_count, source_format, coordinate_system)


def try_read_sdd_annotations(path: str) -> TrajectoryData | None:
    columns = ["track_id", "xmin", "ymin", "xmax", "ymax", "frame", "lost", "occluded", "generated", "label"]
    records: list[TrajectoryRecord] = []
    saw_candidate_line = False

    with open(path, "r", encoding="utf-8-sig") as file:
        for order, line in enumerate(file):
            line = line.strip()
            if not line:
                continue
            try:
                parts = shlex.split(line)
            except ValueError:
                return None
            if len(parts) < 10:
                return None
            saw_candidate_line = True
            try:
                track_id = parts[0]
                xmin = float(parts[1])
                ymin = float(parts[2])
                xmax = float(parts[3])
                ymax = float(parts[4])
                frame = float(parts[5])
            except ValueError:
                return None

            label = parts[9]
            if label.lower() not in ("pedestrian", "ped", "person"):
                continue
            x_center = (xmin + xmax) / 2.0
            y_center = (ymin + ymax) / 2.0
            records.append(TrajectoryRecord(frame, track_id, x_center, y_center, label, order))

    if not saw_candidate_line:
        return None
    return make_trajectory_data(records, columns, source_format="sdd_annotations", coordinate_system="image_pixels")


def read_trajectory(path: str) -> TrajectoryData:
    with open(path, "r", encoding="utf-8-sig", newline="") as file:
        sample = file.read(4096)
        file.seek(0)
        delimiter = detect_delimiter(sample)
        if delimiter:
            reader = csv.DictReader(file, delimiter=delimiter, skipinitialspace=True)
            columns = reader.fieldnames or []
            rows = list(reader)
        else:
            header = file.readline().strip().split()
            columns = header
            rows = []
            for line in file:
                values = line.strip().split()
                if values:
                    rows.append(dict(zip(header, values)))

    if not columns:
        sdd_data = try_read_sdd_annotations(path)
        if sdd_data is not None:
            return sdd_data
        raise ValueError("轨迹文件没有表头，也不是可识别的 SDD annotations.txt 格式。")
    for required in ("x_world", "y_world"):
        if required not in columns:
            sdd_data = try_read_sdd_annotations(path)
            if sdd_data is not None:
                return sdd_data
            raise ValueError(f"轨迹文件缺少必需列：{required}")

    records: list[TrajectoryRecord] = []
    for order, row in enumerate(rows):
        try:
            x_world = float(str(row["x_world"]).strip())
            y_world = float(str(row["y_world"]).strip())
        except (KeyError, TypeError, ValueError):
            continue

        agent_id = str(row.get("agent_id") or "agent").strip() or "agent"
        decision_mode = str(row.get("decision_mode") or "default").strip() or "default"
        time_ms = parse_float(row.get("time_ms"), fallback=float(order))
        records.append(TrajectoryRecord(time_ms, agent_id, x_world, y_world, decision_mode, order))

    return make_trajectory_data(records, columns)


def parse_hex_color(value: str, alpha: int) -> tuple[int, int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) != 6:
        return (255, 42, 32, alpha)
    try:
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha)
    except ValueError:
        return (255, 42, 32, alpha)


def stable_color(key: str, alpha: int) -> tuple[int, int, int, int]:
    palette = [
        (239, 68, 68),
        (34, 197, 94),
        (59, 130, 246),
        (245, 158, 11),
        (168, 85, 247),
        (20, 184, 166),
        (236, 72, 153),
        (132, 204, 22),
        (249, 115, 22),
        (14, 165, 233),
    ]
    digest = hashlib.sha1(key.encode("utf-8", errors="ignore")).digest()
    red, green, blue = palette[digest[0] % len(palette)]
    return (red, green, blue, alpha)


def green_red_gradient(progress: float, alpha: int) -> tuple[int, int, int, int]:
    progress = max(0.0, min(float(progress), 1.0))
    green = np.asarray((24, 185, 70), dtype=np.float32)
    yellow = np.asarray((244, 210, 54), dtype=np.float32)
    red = np.asarray((255, 42, 32), dtype=np.float32)
    if progress < 0.5:
        t = progress / 0.5
        color = green + (yellow - green) * t
    else:
        t = (progress - 0.5) / 0.5
        color = yellow + (red - yellow) * t
    return (int(color[0]), int(color[1]), int(color[2]), alpha)


def is_time_gradient_mode(color_mode: str) -> bool:
    return color_mode in ("按时间渐变", "绿红渐变", "green_red", "green-red")


def is_density_gradient_mode(color_mode: str) -> bool:
    return color_mode in ("按叠加程度", "overlap_density", "density")


def record_progress(record: TrajectoryRecord, time_range: tuple[float, float], order_range: tuple[int, int]) -> float:
    time_min, time_max = time_range
    if time_max > time_min:
        return (record.time_ms - time_min) / (time_max - time_min)
    order_min, order_max = order_range
    return (record.order - order_min) / max(order_max - order_min, 1)


def soften_base_image(base_image: Image.Image, fade: float) -> Image.Image:
    image = base_image.convert("RGBA")
    fade = max(0.0, min(float(fade), 1.0))
    if fade <= 0:
        return image
    image = ImageEnhance.Contrast(image).enhance(1.0 - fade * 0.42)
    white = Image.new("RGBA", image.size, (255, 255, 255, int(255 * fade)))
    return Image.alpha_composite(image, white)


def world_to_pixel(
    x_world: float,
    y_world: float,
    bounds: tuple[float, float, float, float],
    size: tuple[int, int],
    invert_y: bool,
) -> tuple[int, int] | None:
    width, height = size
    x_min, x_max, y_min, y_max = bounds
    if x_max <= x_min or y_max <= y_min:
        raise ValueError("坐标范围无效：最大值必须大于最小值。")

    x = (x_world - x_min) / (x_max - x_min) * (width - 1)
    if invert_y:
        y = (y_max - y_world) / (y_max - y_min) * (height - 1)
    else:
        y = (y_world - y_min) / (y_max - y_min) * (height - 1)

    epsilon = 1e-6
    if x < -epsilon or x > width - 1 + epsilon or y < -epsilon or y > height - 1 + epsilon:
        return None
    x = min(max(x, 0.0), width - 1)
    y = min(max(y, 0.0), height - 1)
    return (int(round(x)), int(round(y)))


def iter_segment_samples(
    first_pixel: tuple[int, int],
    second_pixel: tuple[int, int],
    spacing: float,
    max_samples: int | None = None,
):
    x1, y1 = first_pixel
    x2, y2 = second_pixel
    distance = max(abs(x2 - x1), abs(y2 - y1))
    steps = max(1, int(distance / max(float(spacing), 1.0)))
    if max_samples is not None:
        steps = min(steps, max(1, max_samples - 1))
    for index in range(steps + 1):
        t = index / steps
        yield (int(round(x1 + (x2 - x1) * t)), int(round(y1 + (y2 - y1) * t)))


def influenced_cells(pixel: tuple[int, int], cell_size: float, expansion_radius: float):
    center_x = int(pixel[0] // cell_size)
    center_y = int(pixel[1] // cell_size)
    cell_radius = max(0, int(np.ceil(expansion_radius / cell_size)))
    for y_offset in range(-cell_radius, cell_radius + 1):
        for x_offset in range(-cell_radius, cell_radius + 1):
            yield center_x + x_offset, center_y + y_offset


def build_nearby_line_index(
    pixel_groups: list[list[tuple[TrajectoryRecord, tuple[int, int]]]],
    line_width: int,
    point_radius: float,
    neighborhood_radius: int,
    draw_lines: bool,
    draw_points: bool,
) -> tuple[dict[tuple[int, int], set[int]], float]:
    cell_size = max(4.0, float(neighborhood_radius))
    expansion_radius = max(1.0, float(neighborhood_radius) + line_width / 2.0 + float(point_radius))
    sample_spacing = max(3.0, float(neighborhood_radius) / 2.0)
    grid: dict[tuple[int, int], set[int]] = {}

    def add_agent_to_cells(agent_index: int, pixel: tuple[int, int]) -> None:
        for cell in influenced_cells(pixel, cell_size, expansion_radius):
            grid.setdefault(cell, set()).add(agent_index)

    for agent_index, pixel_records in enumerate(pixel_groups):
        if not pixel_records:
            continue
        if draw_lines and len(pixel_records) >= 2:
            for (_first_record, first_pixel), (_second_record, second_pixel) in zip(pixel_records[:-1], pixel_records[1:]):
                for pixel in iter_segment_samples(first_pixel, second_pixel, sample_spacing):
                    add_agent_to_cells(agent_index, pixel)
        if draw_points and point_radius > 0:
            for _record, pixel in pixel_records:
                add_agent_to_cells(agent_index, pixel)

    return grid, cell_size


def count_at_pixel(line_index: dict[tuple[int, int], set[int]], pixel: tuple[int, int], cell_size: float) -> float:
    cell_x = int(pixel[0] // cell_size)
    cell_y = int(pixel[1] // cell_size)
    nearby_agents: set[int] = set()
    for y_offset in (-1, 0, 1):
        for x_offset in (-1, 0, 1):
            nearby_agents.update(line_index.get((cell_x + x_offset, cell_y + y_offset), ()))
    return float(len(nearby_agents))


def count_on_segment(
    line_index: dict[tuple[int, int], set[int]],
    first_pixel: tuple[int, int],
    second_pixel: tuple[int, int],
    cell_size: float,
    neighborhood_radius: float,
) -> float:
    sample_spacing = max(float(neighborhood_radius), 8.0)
    counts = [count_at_pixel(line_index, pixel, cell_size) for pixel in iter_segment_samples(first_pixel, second_pixel, sample_spacing, max_samples=9)]
    return float(np.percentile(counts, 85)) if counts else 1.0


def overlap_progress(line_count: float, red_line_count: float) -> float:
    red_line_count = max(2.0, float(red_line_count))
    return max(0.0, min((float(line_count) - 1.0) / (red_line_count - 1.0), 1.0))


def grouped_records(records: list[TrajectoryRecord]) -> dict[str, list[TrajectoryRecord]]:
    groups: dict[str, list[TrajectoryRecord]] = {}
    for record in records:
        groups.setdefault(record.agent_id, []).append(record)
    for items in groups.values():
        items.sort(key=lambda record: (record.time_ms, record.order))
    return groups


def compact_pixel_records(
    pixel_records: list[tuple[TrajectoryRecord, tuple[int, int]]],
) -> list[tuple[TrajectoryRecord, tuple[int, int]]]:
    if len(pixel_records) < 2:
        return pixel_records

    compacted = [pixel_records[0]]
    for record, pixel in pixel_records[1:]:
        if pixel != compacted[-1][1]:
            compacted.append((record, pixel))
        else:
            compacted[-1] = (record, pixel)
    return compacted


def color_for_record(
    record: TrajectoryRecord,
    color_mode: str,
    line_alpha: int,
    fixed_color: str,
    time_range: tuple[float, float],
    order_range: tuple[int, int],
) -> tuple[int, int, int, int]:
    if is_time_gradient_mode(color_mode):
        return green_red_gradient(record_progress(record, time_range, order_range), line_alpha)
    if color_mode == "按行人":
        return stable_color(record.agent_id, line_alpha)
    if color_mode == "按决策模式":
        return stable_color(record.decision_mode, line_alpha)
    return parse_hex_color(fixed_color, line_alpha)


def prepare_render_geometry(
    trajectory: TrajectoryData,
    bounds: tuple[float, float, float, float],
    image_size: tuple[int, int],
    invert_y: bool,
) -> RenderGeometry:
    time_values = [record.time_ms for record in trajectory.records]
    order_values = [record.order for record in trajectory.records]
    time_range = (min(time_values), max(time_values))
    order_range = (min(order_values), max(order_values))
    used_points = 0
    pixel_groups: list[list[tuple[TrajectoryRecord, tuple[int, int]]]] = []

    for records in grouped_records(trajectory.records).values():
        pixel_records: list[tuple[TrajectoryRecord, tuple[int, int]]] = []
        for record in records:
            pixel = world_to_pixel(record.x_world, record.y_world, bounds, image_size, invert_y)
            if pixel is not None:
                pixel_records.append((record, pixel))

        used_points += len(pixel_records)
        pixel_groups.append(compact_pixel_records(pixel_records))

    return RenderGeometry(pixel_groups, time_range, order_range, used_points)


def draw_trajectory_overlay(
    base_image: Image.Image,
    geometry: RenderGeometry,
    line_width: float,
    point_size: float,
    line_opacity: float,
    point_opacity: float,
    base_fade: float,
    color_mode: str,
    fixed_color: str,
    neighborhood_radius: float,
    red_line_count: float,
    draw_lines: bool,
    draw_points: bool,
    nearby_index: NearbyLineIndex | None = None,
) -> tuple[Image.Image, int, int, NearbyLineIndex | None]:
    image = soften_base_image(base_image, base_fade)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    line_alpha = int(max(0.0, min(line_opacity, 1.0)) * 255)
    point_alpha = int(max(0.0, min(point_opacity, 1.0)) * 255)
    width = max(1, int(round(line_width)))
    radius = max(0, float(point_size) / 2.0)
    density_mode = is_density_gradient_mode(color_mode)
    used_segments = 0

    if density_mode:
        if nearby_index is None:
            grid, cell_size = build_nearby_line_index(
                geometry.pixel_groups,
                width,
                radius,
                int(round(neighborhood_radius)),
                draw_lines,
                draw_points,
            )
            nearby_index = NearbyLineIndex(grid, cell_size)
        nearby_line_index = nearby_index.grid
        nearby_cell_size = nearby_index.cell_size
    else:
        nearby_line_index = {}
        nearby_cell_size = 1.0

    for pixel_records in geometry.pixel_groups:
        if draw_lines and len(pixel_records) >= 2:
            for (first_record, first_pixel), (second_record, second_pixel) in zip(pixel_records[:-1], pixel_records[1:]):
                if density_mode:
                    value = count_on_segment(nearby_line_index, first_pixel, second_pixel, nearby_cell_size, neighborhood_radius)
                    color = green_red_gradient(overlap_progress(value, red_line_count), line_alpha)
                elif is_time_gradient_mode(color_mode):
                    progress = (record_progress(first_record, geometry.time_range, geometry.order_range) + record_progress(second_record, geometry.time_range, geometry.order_range)) / 2.0
                    color = green_red_gradient(progress, line_alpha)
                else:
                    color = color_for_record(second_record, color_mode, line_alpha, fixed_color, geometry.time_range, geometry.order_range)
                draw.line([first_pixel, second_pixel], fill=color, width=width)
                used_segments += 1

        if draw_points and radius > 0:
            for record, pixel in pixel_records:
                x, y = pixel
                if density_mode:
                    point_color = green_red_gradient(overlap_progress(count_at_pixel(nearby_line_index, pixel, nearby_cell_size), red_line_count), point_alpha)
                else:
                    point_color = color_for_record(record, color_mode, point_alpha, fixed_color, geometry.time_range, geometry.order_range)
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=point_color)

    if geometry.used_points == 0:
        raise ValueError("当前坐标范围内没有轨迹点，请检查范围或 Y 轴方向。")
    return Image.alpha_composite(image, overlay), geometry.used_points, used_segments, nearby_index


def render_trajectory_overlay(
    base_image: Image.Image,
    trajectory: TrajectoryData,
    bounds: tuple[float, float, float, float],
    line_width: float,
    point_size: float,
    line_opacity: float,
    point_opacity: float,
    base_fade: float,
    color_mode: str,
    fixed_color: str,
    neighborhood_radius: float,
    red_line_count: float,
    draw_lines: bool,
    draw_points: bool,
    invert_y: bool,
) -> tuple[Image.Image, int, int]:
    geometry = prepare_render_geometry(trajectory, bounds, base_image.size, invert_y)
    image, used_points, used_segments, _nearby_index = draw_trajectory_overlay(
        base_image,
        geometry,
        line_width,
        point_size,
        line_opacity,
        point_opacity,
        base_fade,
        color_mode,
        fixed_color,
        neighborhood_radius,
        red_line_count,
        draw_lines,
        draw_points,
    )
    return image, used_points, used_segments


class TrajectoryOverlayApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1160x760")
        self.minsize(980, 660)

        self.trajectory: TrajectoryData | None = None
        self.base_image: Image.Image | None = None
        self.rendered_image: Image.Image | None = None
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.geometry_cache_key = None
        self.geometry_cache: RenderGeometry | None = None
        self.nearby_cache_key = None
        self.nearby_cache: NearbyLineIndex | None = None
        self.auto_preview_job: str | None = None
        self.render_serial = 0

        self.traj_path = tk.StringVar()
        self.image_path = tk.StringVar()
        self.output_path = tk.StringVar(value=os.path.abspath("trajectory_overlay.png"))
        self.status = tk.StringVar(value="请选择轨迹文件和底图。")

        self.x_min = tk.StringVar()
        self.x_max = tk.StringVar()
        self.y_min = tk.StringVar()
        self.y_max = tk.StringVar()
        self.padding = tk.DoubleVar(value=4.0)
        self.line_width = tk.DoubleVar(value=1.0)
        self.point_size = tk.DoubleVar(value=1.5)
        self.line_opacity = tk.DoubleVar(value=0.78)
        self.point_opacity = tk.DoubleVar(value=0.85)
        self.base_fade = tk.DoubleVar(value=0.0)
        self.neighborhood_radius = tk.DoubleVar(value=12.0)
        self.red_line_count = tk.DoubleVar(value=30.0)
        self.color_mode = tk.StringVar(value="按叠加程度")
        self.fixed_color = tk.StringVar(value="#ff2a20")
        self.draw_lines = tk.BooleanVar(value=True)
        self.draw_points = tk.BooleanVar(value=True)
        self.invert_y = tk.BooleanVar(value=True)
        self.auto_preview = tk.BooleanVar(value=True)
        self.high_fidelity_preview = tk.BooleanVar(value=True)

        self._build_ui()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        side_shell = ttk.Frame(self, padding=14)
        side_shell.grid(row=0, column=0, sticky="nsw")
        side_shell.rowconfigure(0, weight=1)
        side_shell.columnconfigure(0, weight=1)

        control_canvas = tk.Canvas(side_shell, borderwidth=0, highlightthickness=0, width=360)
        control_scrollbar = ttk.Scrollbar(side_shell, orient="vertical", command=control_canvas.yview)
        control_canvas.configure(yscrollcommand=control_scrollbar.set)
        control_canvas.grid(row=0, column=0, sticky="nsew")
        control_scrollbar.grid(row=0, column=1, sticky="ns", padx=(6, 0))

        side = ttk.Frame(control_canvas)
        side.columnconfigure(1, weight=1)
        side_window = control_canvas.create_window((0, 0), window=side, anchor="nw")

        def update_scroll_region(_event=None) -> None:
            control_canvas.configure(scrollregion=control_canvas.bbox("all"))

        def update_canvas_width(event) -> None:
            control_canvas.itemconfigure(side_window, width=event.width)

        def on_mousewheel(event) -> None:
            control_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        side.bind("<Configure>", update_scroll_region)
        control_canvas.bind("<Configure>", update_canvas_width)
        self.bind_all("<MouseWheel>", on_mousewheel)

        preview = ttk.Frame(self, padding=(0, 14, 14, 14))
        preview.grid(row=0, column=1, sticky="nsew")
        preview.rowconfigure(0, weight=1)
        preview.columnconfigure(0, weight=1)

        self.preview_label = ttk.Label(preview, text="预览区域", anchor="center", background="#20242a", foreground="#dfe7ef")
        self.preview_label.grid(row=0, column=0, sticky="nsew")

        ttk.Label(side, text=APP_TITLE, font=("Microsoft YaHei UI", 15, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 14))

        self._file_row(side, 1, "轨迹文件", self.traj_path, self.choose_trajectory)
        self._file_row(side, 2, "底图图片", self.image_path, self.choose_image)
        self._file_row(side, 3, "保存位置", self.output_path, self.choose_output, save=True)

        ttk.Separator(side).grid(row=4, column=0, columnspan=3, sticky="ew", pady=14)
        ttk.Label(side, text="世界坐标范围", font=("Microsoft YaHei UI", 10, "bold")).grid(row=5, column=0, columnspan=3, sticky="w")

        bounds = ttk.Frame(side)
        bounds.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        for index in range(4):
            bounds.columnconfigure(index, weight=1)
        self._small_entry(bounds, 0, "X 最小", self.x_min)
        self._small_entry(bounds, 1, "X 最大", self.x_max)
        self._small_entry(bounds, 2, "Y 最小", self.y_min)
        self._small_entry(bounds, 3, "Y 最大", self.y_max)

        range_tools = ttk.Frame(side)
        range_tools.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(4, 10))
        ttk.Label(range_tools, text="边距 %").pack(side="left")
        ttk.Spinbox(range_tools, from_=0, to=50, increment=1, textvariable=self.padding, width=6).pack(side="left", padx=(6, 12))
        ttk.Button(range_tools, text="自动坐标范围", command=self.apply_default_bounds).pack(side="left", fill="x", expand=True)

        ttk.Checkbutton(side, text="Y 轴向上为正（常见世界坐标）", variable=self.invert_y).grid(row=8, column=0, columnspan=3, sticky="w", pady=(0, 12))

        ttk.Separator(side).grid(row=9, column=0, columnspan=3, sticky="ew", pady=14)
        ttk.Label(side, text="叠加图参数", font=("Microsoft YaHei UI", 10, "bold")).grid(row=10, column=0, columnspan=3, sticky="w")

        self._scale_row(side, 11, "轨迹线宽", self.line_width, 1, 8, "{:.0f}px")
        self._scale_row(side, 12, "轨迹透明度", self.line_opacity, 0.05, 1.0, "{:.2f}")
        self._scale_row(side, 13, "轨迹点大小", self.point_size, 0, 8, "{:.1f}px")
        self._scale_row(side, 14, "点透明度", self.point_opacity, 0.05, 1.0, "{:.2f}")
        self._scale_row(side, 15, "底图淡化", self.base_fade, 0, 0.8, "{:.2f}")
        self._scale_row(side, 16, "判红半径", self.neighborhood_radius, 3, 80, "{:.0f}px")
        self._scale_row(side, 17, "红色线数", self.red_line_count, 2, 300, "{:.0f}条")

        ttk.Label(side, text="颜色方式").grid(row=18, column=0, sticky="w", pady=(10, 4))
        ttk.Combobox(side, textvariable=self.color_mode, values=("按叠加程度", "按时间渐变", "固定红色", "按决策模式", "按行人"), state="readonly", width=12).grid(row=18, column=1, columnspan=2, sticky="ew", pady=(10, 4))

        color_tools = ttk.Frame(side)
        color_tools.grid(row=19, column=0, columnspan=3, sticky="ew", pady=(2, 8))
        color_tools.columnconfigure(1, weight=1)
        ttk.Label(color_tools, text="固定颜色").grid(row=0, column=0, sticky="w")
        ttk.Entry(color_tools, textvariable=self.fixed_color, width=12).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(color_tools, text="选色", command=self.choose_color, width=6).grid(row=0, column=2)

        toggles = ttk.Frame(side)
        toggles.grid(row=20, column=0, columnspan=3, sticky="ew", pady=(4, 10))
        ttk.Checkbutton(toggles, text="轨迹线", variable=self.draw_lines).pack(side="left")
        ttk.Checkbutton(toggles, text="轨迹点", variable=self.draw_points).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(toggles, text="自动", variable=self.auto_preview).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(toggles, text="高保真", variable=self.high_fidelity_preview).pack(side="left", padx=(12, 0))

        footer = ttk.Frame(side_shell, padding=(0, 12, 0, 0))
        footer.grid(row=1, column=0, columnspan=2, sticky="ew")
        footer.columnconfigure(0, weight=1)
        footer.columnconfigure(1, weight=1)
        ttk.Button(footer, text="预览叠加图", command=self.preview_render).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(footer, text="导出图片", command=self.export_render).grid(row=0, column=1, sticky="ew", padx=(6, 0))

        ttk.Label(footer, textvariable=self.status, wraplength=330, foreground="#4b5563").grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.attach_auto_preview_traces()

    def _file_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, command, save: bool = False) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(parent, textvariable=variable, width=32).grid(row=row, column=1, sticky="ew", padx=6, pady=5)
        ttk.Button(parent, text="选择" if not save else "另存", command=command, width=6).grid(row=row, column=2, sticky="e", pady=5)

    def _small_entry(self, parent: ttk.Frame, column: int, label: str, variable: tk.StringVar) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 6, 0))
        ttk.Label(frame, text=label).pack(anchor="w")
        ttk.Entry(frame, textvariable=variable, width=9).pack(fill="x")

    def _scale_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.DoubleVar, start: float, end: float, fmt: str) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 2))
        frame.columnconfigure(1, weight=1)
        value_label = ttk.Label(frame, width=7)

        def refresh(*_args) -> None:
            value_label.configure(text=fmt.format(variable.get()))

        variable.trace_add("write", refresh)
        ttk.Label(frame, text=label).grid(row=0, column=0, sticky="w")
        ttk.Scale(frame, from_=start, to=end, variable=variable, command=lambda _value: refresh()).grid(row=0, column=1, sticky="ew", padx=8)
        value_label.grid(row=0, column=2, sticky="e")
        refresh()

    def attach_auto_preview_traces(self) -> None:
        variables = (
            self.x_min,
            self.x_max,
            self.y_min,
            self.y_max,
            self.line_width,
            self.point_size,
            self.line_opacity,
            self.point_opacity,
            self.base_fade,
            self.neighborhood_radius,
            self.red_line_count,
            self.color_mode,
            self.fixed_color,
            self.draw_lines,
            self.draw_points,
            self.invert_y,
            self.high_fidelity_preview,
        )
        for variable in variables:
            variable.trace_add("write", lambda *_args: self.schedule_auto_preview())

    def invalidate_render_cache(self) -> None:
        self.geometry_cache_key = None
        self.geometry_cache = None
        self.nearby_cache_key = None
        self.nearby_cache = None

    def schedule_auto_preview(self, delay_ms: int = 280) -> None:
        if not self.auto_preview.get() or self.trajectory is None or self.base_image is None:
            return
        if self.auto_preview_job is not None:
            self.after_cancel(self.auto_preview_job)
        self.auto_preview_job = self.after(delay_ms, self.run_auto_preview)

    def run_auto_preview(self) -> None:
        self.auto_preview_job = None
        if self.trajectory is not None and self.base_image is not None:
            self.run_render(save=False, automatic=True)

    def choose_color(self) -> None:
        _rgb, color = colorchooser.askcolor(color=self.fixed_color.get(), title="选择轨迹颜色")
        if color:
            self.fixed_color.set(color)

    def choose_trajectory(self) -> None:
        path = filedialog.askopenfilename(
            title="选择轨迹文件",
            filetypes=[("表格文本", "*.txt *.tsv *.csv"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            self.trajectory = read_trajectory(path)
            self.invalidate_render_cache()
            self.traj_path.set(path)
            if self.trajectory.coordinate_system == "image_pixels":
                self.invert_y.set(False)
            self.apply_default_bounds()
            self.status.set(
                f"已读取 {self.trajectory.row_count} 个轨迹点，"
                f"{self.trajectory.agent_count} 条轨迹，格式：{self.trajectory.source_format}。"
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))

    def choose_image(self) -> None:
        path = filedialog.askopenfilename(
            title="选择底图图片",
            filetypes=[("图片文件", " ".join(f"*{ext}" for ext in SUPPORTED_IMAGES)), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            self.base_image = Image.open(path).convert("RGBA")
            self.invalidate_render_cache()
            self.image_path.set(path)
            base = os.path.splitext(os.path.basename(path))[0]
            self.output_path.set(os.path.abspath(f"{base}_trajectory_overlay.png"))
            self.rendered_image = self.base_image.copy()
            self.apply_default_bounds()
            self.update_preview(self.rendered_image)
            suffix = " 已自动使用图片像素坐标范围。" if self.trajectory and self.trajectory.coordinate_system == "image_pixels" else ""
            self.status.set(f"已读取底图：{self.base_image.size[0]} x {self.base_image.size[1]} 像素。{suffix}")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"图片读取失败：{exc}")

    def choose_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="保存轨迹叠加图",
            defaultextension=".png",
            filetypes=[("PNG 图片", "*.png"), ("JPEG 图片", "*.jpg"), ("所有文件", "*.*")],
            initialfile=os.path.basename(self.output_path.get() or "trajectory_overlay.png"),
        )
        if path:
            self.output_path.set(path)

    def fill_bounds_from_data(self) -> None:
        if not self.trajectory:
            return
        if self.trajectory.coordinate_system == "image_pixels" and self.base_image is not None:
            self.fill_bounds_from_image_pixels()
            return
        points = self.trajectory.points
        x_min, y_min = points.min(axis=0)
        x_max, y_max = points.max(axis=0)
        pad = max(0.0, float(self.padding.get())) / 100.0
        x_span = max(float(x_max - x_min), 1e-6)
        y_span = max(float(y_max - y_min), 1e-6)
        self.x_min.set(f"{float(x_min - x_span * pad):.4f}")
        self.x_max.set(f"{float(x_max + x_span * pad):.4f}")
        self.y_min.set(f"{float(y_min - y_span * pad):.4f}")
        self.y_max.set(f"{float(y_max + y_span * pad):.4f}")

    def fill_bounds_from_experiment_world(self) -> bool:
        x_min, x_max, y_min, y_max = EXPERIMENT_WORLD_BOUNDS
        self.x_min.set(f"{x_min:.4f}")
        self.x_max.set(f"{x_max:.4f}")
        self.y_min.set(f"{y_min:.4f}")
        self.y_max.set(f"{y_max:.4f}")
        self.invert_y.set(True)
        self.invalidate_render_cache()
        return True

    def fill_bounds_from_image_pixels(self) -> bool:
        if not self.base_image:
            return False
        width, height = self.base_image.size
        self.x_min.set("0")
        self.x_max.set(str(width))
        self.y_min.set("0")
        self.y_max.set(str(height))
        self.invert_y.set(False)
        self.invalidate_render_cache()
        return True

    def apply_default_bounds(self) -> None:
        if not self.trajectory:
            return
        if self.trajectory.coordinate_system == "image_pixels" and self.base_image is not None:
            self.fill_bounds_from_image_pixels()
        else:
            if self.trajectory.coordinate_system == "image_pixels":
                self.invert_y.set(False)
                self.fill_bounds_from_data()
            else:
                self.fill_bounds_from_experiment_world()

    def enforce_sdd_image_bounds(self) -> None:
        if not self.trajectory or not self.base_image:
            return
        if self.trajectory.coordinate_system != "image_pixels":
            return
        width, height = self.base_image.size
        try:
            current = self.get_bounds()
        except ValueError:
            current = (None, None, None, None)
        expected = (0.0, float(width), 0.0, float(height))
        wrong_bounds = current != expected
        if wrong_bounds or self.invert_y.get():
            self.fill_bounds_from_image_pixels()

    def get_bounds(self) -> tuple[float, float, float, float]:
        return (float(self.x_min.get()), float(self.x_max.get()), float(self.y_min.get()), float(self.y_max.get()))

    def ensure_ready(self, show_message: bool = True) -> bool:
        if self.trajectory is None:
            if show_message:
                messagebox.showwarning(APP_TITLE, "请先选择轨迹文件。")
            return False
        if self.base_image is None:
            if show_message:
                messagebox.showwarning(APP_TITLE, "请先选择底图图片。")
            return False
        if not self.draw_lines.get() and not self.draw_points.get():
            if show_message:
                messagebox.showwarning(APP_TITLE, "请至少选择“画轨迹线”或“画轨迹点”。")
            return False
        return True

    def preview_render(self) -> None:
        if not self.ensure_ready():
            return
        self.run_render(save=False)

    def export_render(self) -> None:
        if not self.ensure_ready():
            return
        if not self.output_path.get():
            self.choose_output()
        if self.output_path.get():
            self.run_render(save=True)

    def render_keys(self, bounds: tuple[float, float, float, float], params: dict) -> tuple[tuple, tuple]:
        assert self.trajectory is not None
        assert self.base_image is not None
        normalized_bounds = tuple(round(value, 6) for value in bounds)
        geometry_key = (id(self.trajectory), self.base_image.size, normalized_bounds, bool(params["invert_y"]))
        nearby_key = (
            geometry_key,
            int(round(params["line_width"])),
            round(float(params["point_size"]), 3),
            int(round(params["neighborhood_radius"])),
            bool(params["draw_lines"]),
            bool(params["draw_points"]),
        )
        return geometry_key, nearby_key

    def collect_render_params(self) -> tuple[tuple[float, float, float, float], dict]:
        return self.get_bounds(), {
            "line_width": self.line_width.get(),
            "point_size": self.point_size.get(),
            "line_opacity": self.line_opacity.get(),
            "point_opacity": self.point_opacity.get(),
            "base_fade": self.base_fade.get(),
            "color_mode": self.color_mode.get(),
            "fixed_color": self.fixed_color.get(),
            "neighborhood_radius": self.neighborhood_radius.get(),
            "red_line_count": self.red_line_count.get(),
            "draw_lines": self.draw_lines.get(),
            "draw_points": self.draw_points.get(),
            "invert_y": self.invert_y.get(),
        }

    def render_base_image_for_mode(self, save: bool) -> Image.Image:
        assert self.base_image is not None
        if save or self.high_fidelity_preview.get():
            return self.base_image

        area_width = max(self.preview_label.winfo_width() - 24, 640)
        area_height = max(self.preview_label.winfo_height() - 24, 480)
        preview = self.base_image.copy()
        preview.thumbnail((area_width, area_height), Image.Resampling.LANCZOS)
        return preview

    def run_render(self, save: bool, automatic: bool = False) -> None:
        if not self.ensure_ready(show_message=not automatic):
            return

        try:
            self.enforce_sdd_image_bounds()
            bounds, params = self.collect_render_params()
            render_base_image = self.render_base_image_for_mode(save)
            geometry_key, nearby_key = self.render_keys(bounds, params)
            geometry_key = (geometry_key, render_base_image.size, save)
            nearby_key = (nearby_key, render_base_image.size, save)
        except Exception as exc:
            if not automatic:
                messagebox.showerror(APP_TITLE, str(exc))
            return

        self.render_serial += 1
        serial = self.render_serial
        self.status.set("正在生成轨迹叠加图，请稍候...")

        def task() -> None:
            try:
                assert self.base_image is not None
                assert self.trajectory is not None
                if self.geometry_cache_key == geometry_key and self.geometry_cache is not None:
                    geometry = self.geometry_cache
                else:
                    geometry = prepare_render_geometry(self.trajectory, bounds, render_base_image.size, params["invert_y"])
                    self.geometry_cache_key = geometry_key
                    self.geometry_cache = geometry
                    self.nearby_cache_key = None
                    self.nearby_cache = None

                density_mode = is_density_gradient_mode(params["color_mode"])
                nearby_index = self.nearby_cache if density_mode and self.nearby_cache_key == nearby_key else None
                result, used_points, used_segments, nearby_index = draw_trajectory_overlay(
                    render_base_image,
                    geometry,
                    params["line_width"],
                    params["point_size"],
                    params["line_opacity"],
                    params["point_opacity"],
                    params["base_fade"],
                    params["color_mode"],
                    params["fixed_color"],
                    params["neighborhood_radius"],
                    params["red_line_count"],
                    params["draw_lines"],
                    params["draw_points"],
                    nearby_index,
                )
                if density_mode:
                    self.nearby_cache_key = nearby_key
                    self.nearby_cache = nearby_index
                if save:
                    path = self.output_path.get()
                    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                    save_image = result.convert("RGB") if os.path.splitext(path)[1].lower() in (".jpg", ".jpeg") else result
                    save_image.save(path)
                self.after(0, lambda: self.finish_render(result, used_points, used_segments, save, serial))
            except Exception as exc:
                self.after(0, lambda error=exc: self.fail_render(error, automatic, serial))

        threading.Thread(target=task, daemon=True).start()

    def finish_render(self, image: Image.Image, used_points: int, used_segments: int, saved: bool, serial: int) -> None:
        if serial != self.render_serial:
            return
        self.rendered_image = image
        self.update_preview(image)
        suffix = f" 已保存到：{self.output_path.get()}" if saved else ""
        self.status.set(f"生成完成，使用 {used_points} 个点、{used_segments} 条线段。{suffix}")

    def fail_render(self, exc: Exception, automatic: bool = False, serial: int | None = None) -> None:
        if serial is not None and serial != self.render_serial:
            return
        self.status.set("生成失败。")
        if not automatic:
            messagebox.showerror(APP_TITLE, str(exc))

    def update_preview(self, image: Image.Image) -> None:
        area_width = max(self.preview_label.winfo_width(), 640)
        area_height = max(self.preview_label.winfo_height(), 480)
        preview = image.copy()
        preview.thumbnail((area_width - 24, area_height - 24), Image.Resampling.LANCZOS)
        background = Image.new("RGBA", (area_width, area_height), (32, 36, 42, 255))
        left = (area_width - preview.width) // 2
        top = (area_height - preview.height) // 2
        background.alpha_composite(preview, (left, top))
        self.preview_photo = ImageTk.PhotoImage(background)
        self.preview_label.configure(image=self.preview_photo, text="")


if __name__ == "__main__":
    app = TrajectoryOverlayApp()
    app.mainloop()
