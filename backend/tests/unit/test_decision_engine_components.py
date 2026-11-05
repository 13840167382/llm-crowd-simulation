"""决策引擎组件级测试。"""

from __future__ import annotations

import json
import logging
from email.message import Message

import pytest

from backend.app.application.decisions.engine import DecisionEngineContext, DecisionEngineResponseParseFailed
from backend.app.application.decisions.llm_engine import DecisionProvider, OpenAICompatibleDecisionEngine
from backend.app.application.decisions.prompt_builder import DecisionPromptBuilder
from backend.app.application.decisions.response_parser import DecisionResponseParser
from backend.app.core.config import get_settings
from backend.app.schemas.decisions import DecisionAgentRequest


def test_prompt_builder_can_build_pdf_style_prompt() -> None:
    builder = DecisionPromptBuilder(prompt_version="v1")
    prompt = builder.build(_build_agent_request(), crowd_type_display_name="观光游客")

    assert "你是一个人群流动模拟系统的行为决策引擎" in prompt.system_prompt
    assert "人物类型：观光游客" in prompt.user_prompt
    assert "当前处境：一位观光游客已在主大厅停留16.2秒" in prompt.user_prompt
    assert "耐心消耗：较高，比较急迫" in prompt.user_prompt
    assert "算法推荐：exit_lobby_to_ramp（算法评分：0.72）" in prompt.user_prompt
    assert "[1] 出口 exit_lobby_to_ramp → zone_ramp_a" in prompt.user_prompt
    assert '"chosen_exit_id": "你选择的出口ID"' in prompt.user_prompt


def test_response_parser_can_parse_valid_json_payload() -> None:
    parser = DecisionResponseParser()
    result = parser.parse(
        '```json\n{"chosen_exit_id":"exit_lobby_to_ramp","reasoning":"优先前往展览区。","confidence":0.78}\n```',
        available_exits=_build_agent_request().available_exits,
    )

    assert result.chosen_exit_id == "exit_lobby_to_ramp"
    assert result.reasoning == "优先前往展览区。"
    assert result.confidence == 0.78


def test_response_parser_rejects_unknown_exit_id() -> None:
    parser = DecisionResponseParser()

    with pytest.raises(DecisionEngineResponseParseFailed):
        parser.parse(
            '{"chosen_exit_id":"exit_unknown","reasoning":"随便选。","confidence":0.55}',
            available_exits=_build_agent_request().available_exits,
        )


def test_prompt_builder_can_bucket_patience_ratio_display() -> None:
    builder = DecisionPromptBuilder(prompt_version="v1")

    low_prompt = builder.build(
        _build_agent_request(patience_ratio=0.30),
        crowd_type_display_name="观光游客",
    )
    medium_prompt = builder.build(
        _build_agent_request(patience_ratio=0.70),
        crowd_type_display_name="观光游客",
    )
    high_prompt = builder.build(
        _build_agent_request(patience_ratio=0.95),
        crowd_type_display_name="观光游客",
    )
    overflow_prompt = builder.build(
        _build_agent_request(patience_ratio=1.20),
        crowd_type_display_name="观光游客",
    )

    assert "耐心消耗：较低，不太着急" in low_prompt.user_prompt
    assert "耐心消耗：中等，开始有些焦虑" in medium_prompt.user_prompt
    assert "耐心消耗：较高，比较急迫" in high_prompt.user_prompt
    assert "耐心消耗：已超出容忍限度，非常急迫" in overflow_prompt.user_prompt


def test_openai_compatible_engine_can_log_outgoing_request_and_parse_response(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeHttpResponse:
        def __init__(self) -> None:
            self.headers = Message()
            self.headers["x-request-id"] = "provider_req_123"

        def read(self) -> bytes:
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"chosen_exit_id":"exit_lobby_to_ramp","reasoning":"继续前往展览区。","confidence":0.81}'
                            }
                        }
                    ]
                }
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            del exc_type
            del exc
            del tb

    def fake_urlopen(request, timeout: float):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeHttpResponse()

    monkeypatch.setattr("backend.app.application.decisions.llm_engine.urllib_request.urlopen", fake_urlopen)
    caplog.set_level(logging.DEBUG, logger="backend.app.application.decisions.llm_engine")

    engine = OpenAICompatibleDecisionEngine(
        base_url="https://llm.example.com/v1",
        api_key="test-key",
        provider_model="provider-model",
        temperature=0.0,
        prompt_version="v1",
    )

    result = engine.decide(
        _build_agent_request(),
        context=DecisionEngineContext(
            experiment_id="exp_demo",
            batch_id="batch_demo",
            request_id="req_demo",
            simulation_time_ms=1700000150000,
            crowd_type_display_name="观光游客",
        ),
        llm_timeout_ms=5000,
        llm_model="gpt-4.1-mini",
    )

    assert result.chosen_exit_id == "exit_lobby_to_ramp"
    assert result.reasoning == "继续前往展览区。"
    assert result.confidence == 0.81
    assert captured["url"] == "https://llm.example.com/v1/chat/completions"
    assert captured["timeout"] == 5.0
    assert captured["body"]["model"] == "provider-model"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["messages"][0]["role"] == "system"
    assert captured["body"]["messages"][1]["role"] == "user"
    assert "真实模型调用开始" in caplog.text
    assert "真实模型请求体预览" in caplog.text
    assert "真实模型请求消息预览" in caplog.text
    assert "provider-model" in caplog.text
    assert "req_demo" in caplog.text
    assert "test-key" not in caplog.text


def test_settings_can_load_additional_decision_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("APP_DECISION_PROVIDER_BASE_URL", "https://primary.example.com/v1")
    monkeypatch.setenv("APP_DECISION_PROVIDER_API_KEY", "primary-key")
    monkeypatch.setenv("APP_DECISION_PROVIDER_MODEL", "primary-model")
    monkeypatch.setenv("APP_DECISION_PROVIDER_2_BASE_URL", "https://secondary.example.com/v1")
    monkeypatch.setenv("APP_DECISION_PROVIDER_2_API_KEY", "secondary-key")
    monkeypatch.setenv("APP_DECISION_PROVIDER_2_MODEL", "secondary-model")

    try:
        settings = get_settings()
    finally:
        get_settings.cache_clear()

    assert [provider.name for provider in settings.decision_providers] == ["provider_1", "provider_2"]
    assert [provider.base_url for provider in settings.decision_providers] == [
        "https://primary.example.com/v1",
        "https://secondary.example.com/v1",
    ]
    assert [provider.api_key for provider in settings.decision_providers] == ["primary-key", "secondary-key"]
    assert [provider.model for provider in settings.decision_providers] == ["primary-model", "secondary-model"]


def test_openai_compatible_engine_round_robins_multiple_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_requests: list[dict[str, object]] = []

    class FakeHttpResponse:
        def __init__(self, request_index: int) -> None:
            self.headers = Message()
            self.headers["x-request-id"] = f"provider_req_{request_index}"

        def read(self) -> bytes:
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"chosen_exit_id":"exit_lobby_to_ramp","reasoning":"继续前往展览区。","confidence":0.81}'
                            }
                        }
                    ]
                }
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            del exc_type
            del exc
            del tb

    def fake_urlopen(request, timeout: float):
        captured_requests.append(
            {
                "url": request.full_url,
                "auth": request.get_header("Authorization"),
                "body": json.loads(request.data.decode("utf-8")),
                "timeout": timeout,
            }
        )
        return FakeHttpResponse(len(captured_requests))

    monkeypatch.setattr("backend.app.application.decisions.llm_engine.urllib_request.urlopen", fake_urlopen)

    engine = OpenAICompatibleDecisionEngine(
        base_url="https://fallback.example.com/v1",
        api_key="fallback-key",
        provider_model="",
        temperature=0.0,
        prompt_version="v1",
        providers=(
            DecisionProvider(
                name="provider_1",
                base_url="https://primary.example.com/v1",
                api_key="primary-key",
                model="primary-model",
            ),
            DecisionProvider(
                name="provider_2",
                base_url="https://secondary.example.com/v1",
                api_key="secondary-key",
                model="secondary-model",
            ),
        ),
    )

    context = DecisionEngineContext(
        experiment_id="exp_demo",
        batch_id="batch_demo",
        request_id="req_demo",
        simulation_time_ms=1700000150000,
        crowd_type_display_name="观光游客",
    )

    first_result = engine.decide(
        _build_agent_request(),
        context=context,
        llm_timeout_ms=5000,
        llm_model="experiment-model",
    )
    second_result = engine.decide(
        _build_agent_request(),
        context=context,
        llm_timeout_ms=5000,
        llm_model="experiment-model",
    )

    assert first_result.chosen_exit_id == "exit_lobby_to_ramp"
    assert second_result.chosen_exit_id == "exit_lobby_to_ramp"
    assert [item["url"] for item in captured_requests] == [
        "https://primary.example.com/v1/chat/completions",
        "https://secondary.example.com/v1/chat/completions",
    ]
    assert [item["auth"] for item in captured_requests] == [
        "Bearer primary-key",
        "Bearer secondary-key",
    ]
    assert [item["body"]["model"] for item in captured_requests] == [
        "primary-model",
        "secondary-model",
    ]
    assert [item["timeout"] for item in captured_requests] == [5.0, 5.0]


def _build_agent_request(*, patience_ratio: float = 0.81) -> DecisionAgentRequest:
    return DecisionAgentRequest.model_validate(
        {
            "agent_id": "sim_a3_0001",
            "crowd_type": "tourist",
            "current_zone_id": "zone_lobby",
            "stay_duration_ms": 16200,
            "patience_ratio": patience_ratio,
            "algorithm_recommendation": "exit_lobby_to_ramp",
            "dilemma_summary": "一位观光游客已在主大厅停留16.2秒(耐心消耗81%), 偏好展览区, 当前面临出口选择困境。",
            "available_exits": [
                {
                    "exit_id": "exit_lobby_to_ramp",
                    "target_zone_id": "zone_ramp_a",
                    "algorithm_score": 0.72,
                    "nl_description": "通往坡道A区(展览区入口). 展览区吸引力强, 当前坡道口排队约45人.",
                },
                {
                    "exit_id": "exit_lobby_to_rest",
                    "target_zone_id": "zone_rest_1",
                    "algorithm_score": 0.38,
                    "nl_description": "通往休息区1号. 吸引力较弱, 但几乎无需排队.",
                },
            ],
        }
    )
