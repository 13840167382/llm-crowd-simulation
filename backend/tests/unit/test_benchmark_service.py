"""benchmark service 的纯函数测试。"""

from __future__ import annotations

from backend.app.application.benchmarking import service


def test_decision_benchmark_payload_contains_diverse_prompt_context() -> None:
    type_ids = {
        "tourist": "bt_test",
        "shopper": "bs_test",
        "resident": "br_test",
    }
    spawned_agents = [
        {
            "agent_id": f"agent_{index}",
            "crowd_type": type_ids[("tourist", "shopper", "resident")[index % 3]],
            "spawn_zone_id": "zone_lobby",
            "spawn_x": float(index),
            "spawn_z": float(index),
        }
        for index in range(12)
    ]
    context = service.BenchmarkExperimentContext(
        experiment_id="exp_test",
        type_ids=type_ids,
        spawned_agents=spawned_agents,
        allocation_counts={value: 4 for value in type_ids.values()},
    )
    decision_targets = service._build_agent_target_map(context)

    payload = service._build_decision_request_payload(
        context=context,
        benchmark_agents=spawned_agents,
        decision_targets=decision_targets,
        batch_id="batch_test",
        simulation_time_ms=300_000,
    )

    dilemma_summaries = {item["dilemma_summary"] for item in payload["agents"]}
    algorithm_recommendations = {item["algorithm_recommendation"] for item in payload["agents"]}
    first_exit_descriptions = {
        item["available_exits"][0]["nl_description"]
        for item in payload["agents"]
    }

    assert len(dilemma_summaries) >= 3
    assert None in algorithm_recommendations
    assert len(first_exit_descriptions) >= 3
