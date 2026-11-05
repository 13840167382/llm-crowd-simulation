"""分配相关领域枚举。"""

from __future__ import annotations

from enum import Enum


class DistributionMode(str, Enum):
    """初始分布模式枚举。"""

    ZONE_WEIGHTED = "zone_weighted"
    RANDOM_ENTRY = "random_entry"
    POINT_SPREAD = "point_spread"
    ZONE_FIXED = "zone_fixed"
