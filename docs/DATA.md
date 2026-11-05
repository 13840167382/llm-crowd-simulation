# 数据与分析入口

原始 CSV/TXT、SDD 视频/标注、标准化 CSV、矩阵及实验输出与源码分开保存，不自动上传到 GitHub。

核心数值入口：`python -m offline_analysis.pipeline --project-root DATA_ROOT --output-dir OUTPUT_ROOT`。参数可通过 `--help` 查看。默认 30 分钟窗口、0.23 网格、99 百分位归一化，真实标注源为 DeathCircle/video0。

红绿图入口：`python -m offline_analysis.generate_red_green_overlays`。这个原始入口从仓库根目录读取 `offline_analysis/outputs/standardized`、`analysis_config.json` 和 `斯坦福行人数据集/annotations/deathCircle/video0/reference.jpg`，因此运行前需要把输入安排到这些位置，或自行修改入口参数。数值 pipeline 不会自动生成红绿叠加图。

交互渲染器：`python 斯坦福行人数据集/热力图渲染/trajectory_overlay_renderer.py`，需要 Pillow、NumPy 和 Python 的 Tkinter；Linux 上可能需另外安装 Tk 支持。使用方法见该目录 README。

`samples/results` 保留现有分析的四份小型汇总表，便于了解输出字段；它们是历史分析结果，不是此次重新运行完整 Unity 仿真实验产生的结果。

已知口径：LLM 第一次原始 CSV 的 `decision_mode` 为 `fixedpath`，现有 pipeline 会按组别重标为 llm；`decision_source` 在轨迹标准化时也按组别填充。审计真实调用须使用 transcript 中的事件来源。当前坐标映射为 min-max 归一化，不应表述为经过单应矩阵配准的真实坐标对齐。
