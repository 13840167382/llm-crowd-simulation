"""P0 benchmark suite 的纯函数测试。"""

from __future__ import annotations

import pytest

from backend.app.application.benchmarking import p0_suite


def test_parse_positive_int_tuple_deduplicates_and_preserves_order() -> None:
    result = p0_suite.parse_positive_int_tuple("1, 10, 10, 20", fallback=(3,))

    assert result == (1, 10, 20)


def test_parse_positive_int_tuple_rejects_non_positive_values() -> None:
    with pytest.raises(ValueError):
        p0_suite.parse_positive_int_tuple("1,0,2", fallback=(1,))


def test_normalize_http_base_url_rewrites_wildcard_host() -> None:
    result = p0_suite.normalize_http_base_url("http://0.0.0.0:8000/")

    assert result == "http://127.0.0.1:8000"


def test_build_mixed_operation_plan_matches_requested_concurrency() -> None:
    plan = p0_suite.build_mixed_operation_plan(12)

    assert len(plan) == 12
    assert set(plan) == {"decision", "trajectory", "stats", "heatmap"}


def test_build_periodic_schedule_offsets_generates_wall_clock_schedule() -> None:
    offsets = p0_suite.build_periodic_schedule_offsets(interval_seconds=5.0, batch_count=4)

    assert offsets == [0.0, 5.0, 10.0, 15.0]


def test_scale_type_counts_preserves_total_agents() -> None:
    counts = p0_suite.scale_type_counts(10000)

    assert sum(counts.values()) == 10000
    assert counts["tourist"] > 0
    assert counts["shopper"] > 0
    assert counts["resident"] > 0
