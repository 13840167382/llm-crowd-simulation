"""决策相关领域枚举。"""

from __future__ import annotations

from enum import Enum


class DecisionSource(str, Enum):
    """单个决策结果的来源。"""

    LLM = "llm"
    FALLBACK = "fallback"
    ALGORITHM = "algorithm"
    NONE = "none"


class DecisionBatchStatus(str, Enum):
    """决策批次处理状态。"""

    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
