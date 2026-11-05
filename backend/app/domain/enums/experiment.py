"""实验相关领域枚举。"""

from __future__ import annotations

from enum import Enum


class ExperimentStatus(str, Enum):
    """实验状态枚举。"""

    INITIALIZED = "initialized"
    CONFIGURED = "configured"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class CompletionReason(str, Enum):
    """实验完成原因枚举。"""

    SIMULATION_FINISHED = "simulation_finished"
    INTERRUPTED = "interrupted"
    ERROR = "error"
