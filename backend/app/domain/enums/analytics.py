"""分析与热力图相关领域枚举。"""

from __future__ import annotations

from enum import Enum


class HeatmapMetric(str, Enum):
    """热力图指标类型。"""

    STAY_DURATION = "stay_duration"
    PASSAGE_COUNT = "passage_count"
    PEAK_DENSITY = "peak_density"
    QUEUE_FREQUENCY = "queue_frequency"
    AVG_SPEED = "avg_speed"


class NormalizationMode(str, Enum):
    """热力图归一化方式。"""

    NONE = "none"
    MIN_MAX = "min_max"
    PERCENTILE_99 = "percentile_99"
    LOG = "log"
