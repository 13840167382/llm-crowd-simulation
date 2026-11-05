"""Numeric precision helpers shared by API schemas and services."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

COORDINATE_DECIMAL_PLACES = 3
COORDINATE_QUANT = Decimal("0.001")


def round_coordinate(value: Any) -> float:
    """Round a coordinate to the PDF-defined three decimal places."""

    try:
        return float(Decimal(str(value)).quantize(COORDINATE_QUANT, rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("坐标必须是可保留三位小数的数值") from exc
