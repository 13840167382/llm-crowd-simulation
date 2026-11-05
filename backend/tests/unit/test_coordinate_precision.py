"""坐标三位小数统一口径回归测试。"""

from __future__ import annotations

from backend.app.core.precision import round_coordinate
from backend.app.schemas.allocation import SpawnPointWeight
from backend.app.schemas.analytics import GridOrigin
from backend.app.schemas.topology import Bounds2D, Position2D


def test_coordinate_rounding_uses_three_decimal_half_up() -> None:
    assert round_coordinate(1.2345) == 1.235
    assert round_coordinate(2.3454) == 2.345
    assert round_coordinate(-1.2345) == -1.235


def test_coordinate_schemas_normalize_to_three_decimals() -> None:
    position = Position2D(x=1.2345, z=2.3454)
    bounds = Bounds2D(min_x=-1.2345, min_z=0.0004, max_x=12.9995, max_z=20.1234)
    spawn_point = SpawnPointWeight(x=7.7775, z=8.8884, weight=1.0)
    grid_origin = GridOrigin(x=-10.5555, z=11.1114)

    assert position.model_dump() == {"x": 1.235, "z": 2.345}
    assert bounds.model_dump() == {"min_x": -1.235, "min_z": 0.0, "max_x": 13.0, "max_z": 20.123}
    assert spawn_point.model_dump() == {"x": 7.778, "z": 8.888, "weight": 1.0}
    assert grid_origin.model_dump() == {"x": -10.556, "z": 11.111}
