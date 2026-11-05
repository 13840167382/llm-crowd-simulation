"""系统内置人群模板定义。"""

from __future__ import annotations

from typing import Any


BUILTIN_CROWD_TYPE_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "type_id": "tourist",
        "display_name": "观光游客",
        "color_hex": "#FF6B35",
        "base_speed": 1.20,
        "social_distance": 0.80,
        "tolerance_duration_ms": 20000,
        "attraction_weights": {
            "exhibit": 0.90,
            "commercial": 0.60,
            "rest": 0.30,
            "service": 0.20,
            "plaza": 0.50,
            "passage": 0.10,
            "exit_final": 0.05,
        },
    },
    {
        "type_id": "shopper",
        "display_name": "购物者",
        "color_hex": "#E91E63",
        "base_speed": 0.95,
        "social_distance": 0.70,
        "tolerance_duration_ms": 30000,
        "attraction_weights": {
            "exhibit": 0.30,
            "commercial": 0.95,
            "rest": 0.50,
            "service": 0.10,
            "plaza": 0.40,
            "passage": 0.10,
            "exit_final": 0.10,
        },
    },
    {
        "type_id": "commuter",
        "display_name": "通勤者",
        "color_hex": "#2196F3",
        "base_speed": 1.45,
        "social_distance": 0.60,
        "tolerance_duration_ms": 12000,
        "attraction_weights": {
            "exhibit": 0.10,
            "commercial": 0.15,
            "rest": 0.10,
            "service": 0.20,
            "plaza": 0.20,
            "passage": 0.95,
            "exit_final": 0.80,
        },
    },
    {
        "type_id": "resident",
        "display_name": "居民",
        "color_hex": "#4CAF50",
        "base_speed": 1.05,
        "social_distance": 0.90,
        "tolerance_duration_ms": 28000,
        "attraction_weights": {
            "exhibit": 0.25,
            "commercial": 0.20,
            "rest": 0.70,
            "service": 0.45,
            "plaza": 0.35,
            "passage": 0.25,
            "exit_final": 0.15,
        },
    },
    {
        "type_id": "student",
        "display_name": "学生",
        "color_hex": "#FF9800",
        "base_speed": 1.30,
        "social_distance": 0.65,
        "tolerance_duration_ms": 18000,
        "attraction_weights": {
            "exhibit": 0.55,
            "commercial": 0.35,
            "rest": 0.25,
            "service": 0.20,
            "plaza": 0.55,
            "passage": 0.45,
            "exit_final": 0.20,
        },
    },
    {
        "type_id": "staff",
        "display_name": "工作人员",
        "color_hex": "#9E9E9E",
        "base_speed": 1.10,
        "social_distance": 0.75,
        "tolerance_duration_ms": 45000,
        "attraction_weights": {
            "exhibit": 0.20,
            "commercial": 0.15,
            "rest": 0.30,
            "service": 0.95,
            "plaza": 0.25,
            "passage": 0.60,
            "exit_final": 0.10,
        },
    },
)

BUILTIN_CROWD_TYPE_SEED_METADATA: dict[str, Any] = {
    "seed_source": "system_builtin_v1",
    "builtin_version": 1,
}
