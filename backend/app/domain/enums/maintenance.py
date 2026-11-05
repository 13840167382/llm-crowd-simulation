"""维护任务相关枚举。"""

from __future__ import annotations

from enum import Enum


class MaintenanceJobType(str, Enum):
    """维护任务类型。"""

    ANALYTICS_REFRESH = "analytics_refresh"
    HEATMAP_PREWARM = "heatmap_prewarm"
    RUNTIME_DATA_CLEANUP = "runtime_data_cleanup"
    EXPERIMENT_ARCHIVE = "experiment_archive"


class MaintenanceJobStatus(str, Enum):
    """维护任务状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
