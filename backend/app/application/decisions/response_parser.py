"""决策模型响应解析器。

真实模型可能返回纯 JSON、Markdown fenced code block，或在 JSON 前后混入少量解释文本。
解析器会尽量提取 JSON 对象，但对字段契约保持严格校验：chosen_exit_id 必须来自候选出口，
confidence 必须是 0~1，避免模型幻觉污染后续决策统计。
"""

from __future__ import annotations

import json

from backend.app.application.decisions.engine import (
    DecisionEngineResponseParseFailed,
    DecisionEngineResult,
)
from backend.app.schemas.decisions import AvailableExitOption


class DecisionResponseParser:
    """把模型输出解析成统一的决策结果。"""

    def parse(
        self,
        raw_text: str,
        *,
        available_exits: list[AvailableExitOption],
    ) -> DecisionEngineResult:
        """解析模型原始文本输出。

        这里不做 fallback，解析失败会抛 DecisionEngineResponseParseFailed，
        由上层 service 决定是否降级到算法推荐。
        """

        normalized_text = _extract_json_payload(raw_text)
        try:
            payload = json.loads(normalized_text)
        except json.JSONDecodeError as exc:
            raise DecisionEngineResponseParseFailed(f"模型返回内容不是合法 JSON: {exc}") from exc

        if not isinstance(payload, dict):
            raise DecisionEngineResponseParseFailed("模型返回内容必须是 JSON 对象")

        chosen_exit_id = payload.get("chosen_exit_id")
        if not isinstance(chosen_exit_id, str) or not chosen_exit_id.strip():
            raise DecisionEngineResponseParseFailed("模型返回缺少合法的 chosen_exit_id")
        chosen_exit_id = chosen_exit_id.strip()

        available_exit_ids = {item.exit_id for item in available_exits}
        if chosen_exit_id not in available_exit_ids:
            raise DecisionEngineResponseParseFailed(
                f"模型返回的 chosen_exit_id `{chosen_exit_id}` 不在候选出口中"
            )

        reasoning = payload.get("reasoning")
        if reasoning is not None and not isinstance(reasoning, str):
            raise DecisionEngineResponseParseFailed("模型返回的 reasoning 必须是字符串或 null")
        if isinstance(reasoning, str):
            reasoning = reasoning.strip() or None

        confidence = payload.get("confidence")
        if confidence is None:
            raise DecisionEngineResponseParseFailed("模型返回缺少 confidence")

        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError) as exc:
            raise DecisionEngineResponseParseFailed("模型返回的 confidence 不是数字") from exc

        if confidence_value < 0.0 or confidence_value > 1.0:
            raise DecisionEngineResponseParseFailed("模型返回的 confidence 必须在 0.0 到 1.0 之间")

        return DecisionEngineResult(
            chosen_exit_id=chosen_exit_id,
            reasoning=reasoning,
            confidence=round(confidence_value, 4),
        )


def _extract_json_payload(raw_text: str) -> str:
    """从模型文本中提取第一个 JSON 对象片段。"""

    stripped = raw_text.strip()
    if not stripped:
        raise DecisionEngineResponseParseFailed("模型返回了空内容")

    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            stripped = "\n".join(lines[1:-1]).strip()

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise DecisionEngineResponseParseFailed("模型返回中未找到 JSON 对象")

    return stripped[start : end + 1]
