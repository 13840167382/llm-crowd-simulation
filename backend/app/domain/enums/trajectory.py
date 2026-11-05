"""轨迹相关领域枚举。"""

from __future__ import annotations

from enum import Enum


class AgentState(str, Enum):
    """轨迹点中的人物行为状态。"""

    WALKING = "walking"
    QUEUING = "queuing"
    IDLE = "idle"
    TRANSITIONING = "transitioning"
