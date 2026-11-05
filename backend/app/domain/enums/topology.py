"""拓扑相关领域枚举。"""

from __future__ import annotations

from enum import Enum


class ZoneType(str, Enum):
    """区域类型枚举。"""

    PASSAGE = "passage"
    PLAZA = "plaza"
    EXHIBIT = "exhibit"
    REST = "rest"
    COMMERCIAL = "commercial"
    SERVICE = "service"
    EXIT_FINAL = "exit_final"
    OTHER = "other"


class ExitType(str, Enum):
    """出口类型枚举。"""

    PORTAL = "portal"
    RAMP = "ramp"
    ELEVATOR = "elevator"
    STAIR = "stair"
