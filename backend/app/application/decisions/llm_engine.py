"""真实模型决策引擎实现。

这里对接的是 OpenAI Chat Completions 兼容接口，包括 OpenAI 官方接口、
vLLM 的 OpenAI-compatible server，以及其他返回 choices/message/content 结构的服务。
本模块只负责一次人物决策的模型调用、日志脱敏预览和响应解析；批次级并发、超时降级、
fallback 和数据库写入由 decisions.service 负责。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from urllib import error as urllib_error
from urllib import request as urllib_request

from backend.app.application.decisions.engine import (
    DecisionEngineCallFailed,
    DecisionEngineContext,
    DecisionEngineResponseParseFailed,
    DecisionEngineResult,
    DecisionEngineUnavailable,
)
from backend.app.application.decisions.prompt_builder import DecisionPromptBuilder
from backend.app.application.decisions.response_parser import DecisionResponseParser
from backend.app.infrastructure.observability.logger import get_logger
from backend.app.schemas.decisions import DecisionAgentRequest

logger = get_logger(__name__)
MAX_PROVIDER_BODY_LOG_CHARS = 4000
MAX_PROVIDER_CONTENT_LOG_CHARS = 2000


@dataclass(frozen=True, slots=True)
class DecisionProvider:
    """一次模型调用可用的 provider 凭据。"""

    name: str
    base_url: str
    api_key: str
    model: str = ""


class OpenAICompatibleDecisionEngine:
    """兼容 OpenAI Chat Completions 风格接口的决策引擎。

    provider_model 为空时会使用实验里的 llm_model，这样同一后端可以按实验切换模型；
    provider_model 不为空时则作为全局覆盖，适合服务器统一接入固定模型服务。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        provider_model: str,
        temperature: float,
        prompt_version: str,
        providers: Sequence[object] | None = None,
        prompt_builder: DecisionPromptBuilder | None = None,
        response_parser: DecisionResponseParser | None = None,
    ) -> None:
        """保存模型连接配置，并允许测试注入 prompt_builder/response_parser。"""

        self.providers = _normalize_providers(
            providers,
            fallback_base_url=base_url,
            fallback_api_key=api_key,
            fallback_model=provider_model,
        )
        self.provider_model = provider_model.strip()
        self.temperature = float(temperature)
        self.prompt_builder = prompt_builder or DecisionPromptBuilder(prompt_version=prompt_version)
        self.response_parser = response_parser or DecisionResponseParser()
        self._provider_index = 0
        self._provider_lock = threading.Lock()

    def decide(
        self,
        agent: DecisionAgentRequest,
        *,
        context: DecisionEngineContext,
        llm_timeout_ms: int,
        llm_model: str,
    ) -> DecisionEngineResult:
        """为单个 agent 构建 prompt、调用模型并解析成统一结果。"""

        provider = self._next_provider()
        model_name = provider.model or self.provider_model or llm_model
        if not provider.api_key:
            raise DecisionEngineUnavailable("未配置决策模型 API Key")
        if not model_name:
            raise DecisionEngineUnavailable("未配置决策模型名称")
        if not provider.base_url:
            raise DecisionEngineUnavailable("未配置决策模型服务地址")

        # prompt_builder 只拼接业务输入；response_format 要求模型尽量返回 JSON 对象。
        built_prompt = self.prompt_builder.build(
            agent,
            crowd_type_display_name=context.crowd_type_display_name,
        )
        payload = {
            "model": model_name,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": built_prompt.system_prompt},
                {"role": "user", "content": built_prompt.user_prompt},
            ],
        }
        timeout_seconds = _normalize_timeout_seconds(llm_timeout_ms)
        response_json = self._post_chat_completion(
            provider=provider,
            payload=payload,
            timeout_seconds=timeout_seconds,
            context=context,
        )
        try:
            raw_content = _extract_choice_content(response_json)
            logger.debug(
                "真实模型 message.content 预览: experiment_id=%s batch_id=%s request_id=%s content=%s",
                context.experiment_id,
                context.batch_id,
                context.request_id,
                _truncate_for_log(raw_content, MAX_PROVIDER_CONTENT_LOG_CHARS),
            )
            return self.response_parser.parse(raw_content, available_exits=agent.available_exits)
        except DecisionEngineResponseParseFailed as exc:
            logger.warning(
                "真实模型响应内容解析失败: experiment_id=%s batch_id=%s request_id=%s error=%s response=%s",
                context.experiment_id,
                context.batch_id,
                context.request_id,
                exc,
                _truncate_for_log(
                    json.dumps(response_json, ensure_ascii=False),
                    MAX_PROVIDER_BODY_LOG_CHARS,
                ),
            )
            raise

    def _post_chat_completion(
        self,
        *,
        provider: DecisionProvider,
        payload: dict[str, object],
        timeout_seconds: float,
        context: DecisionEngineContext,
    ) -> dict[str, object]:
        """向兼容服务发送 /chat/completions 请求，并返回外层 JSON 对象。"""

        request_url = f"{provider.base_url}/chat/completions"
        request_body_text = json.dumps(payload, ensure_ascii=False)
        messages = payload.get("messages")
        message_count = len(messages) if isinstance(messages, list) else 0
        system_prompt, user_prompt = _extract_logged_request_prompts(payload)

        logger.info(
            "真实模型调用开始: experiment_id=%s batch_id=%s request_id=%s model=%s timeout_seconds=%.3f url=%s message_count=%s",
            context.experiment_id,
            context.batch_id,
            context.request_id,
            payload.get("model", "<unknown>"),
            timeout_seconds,
            request_url,
            message_count,
        )
        logger.debug(
            "真实模型 provider 已选择: experiment_id=%s batch_id=%s request_id=%s provider=%s",
            context.experiment_id,
            context.batch_id,
            context.request_id,
            provider.name,
        )
        logger.debug(
            "真实模型请求体预览: experiment_id=%s batch_id=%s request_id=%s body=%s",
            context.experiment_id,
            context.batch_id,
            context.request_id,
            _truncate_for_log(request_body_text, MAX_PROVIDER_BODY_LOG_CHARS),
        )
        logger.debug(
            "真实模型请求消息预览: experiment_id=%s batch_id=%s request_id=%s system_prompt=%s user_prompt=%s",
            context.experiment_id,
            context.batch_id,
            context.request_id,
            _truncate_for_log(system_prompt, MAX_PROVIDER_CONTENT_LOG_CHARS),
            _truncate_for_log(user_prompt, MAX_PROVIDER_CONTENT_LOG_CHARS),
        )

        request_body = request_body_text.encode("utf-8")
        request = urllib_request.Request(
            url=request_url,
            data=request_body,
            method="POST",
            headers={
                "Authorization": f"Bearer {provider.api_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
                content_type = response.headers.get("Content-Type", "<unknown>")
                provider_request_id = (
                    response.headers.get("x-request-id")
                    or response.headers.get("request-id")
                    or response.headers.get("x-trace-id")
                )
        except urllib_error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            logger.warning(
                "真实模型调用返回 HTTP 错误: experiment_id=%s batch_id=%s request_id=%s status=%s body=%s",
                context.experiment_id,
                context.batch_id,
                context.request_id,
                exc.code,
                error_body[:500],
            )
            raise DecisionEngineCallFailed(f"决策模型返回 HTTP {exc.code}") from exc
        except urllib_error.URLError as exc:
            logger.warning(
                "真实模型调用连接失败: experiment_id=%s batch_id=%s request_id=%s error=%s",
                context.experiment_id,
                context.batch_id,
                context.request_id,
                exc,
            )
            raise DecisionEngineUnavailable(f"决策模型连接失败: {exc.reason}") from exc

        logger.info(
            "真实模型调用完成: experiment_id=%s batch_id=%s request_id=%s provider_request_id=%s",
            context.experiment_id,
            context.batch_id,
            context.request_id,
            provider_request_id or "<unknown>",
        )
        logger.debug(
            "真实模型原始响应预览: experiment_id=%s batch_id=%s request_id=%s provider_request_id=%s content_type=%s body=%s",
            context.experiment_id,
            context.batch_id,
            context.request_id,
            provider_request_id or "<unknown>",
            content_type,
            _truncate_for_log(raw_body, MAX_PROVIDER_BODY_LOG_CHARS),
        )

        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            logger.warning(
                "真实模型外层响应 JSON 解析失败: experiment_id=%s batch_id=%s request_id=%s error=%s body=%s",
                context.experiment_id,
                context.batch_id,
                context.request_id,
                exc,
                _truncate_for_log(raw_body, MAX_PROVIDER_BODY_LOG_CHARS),
            )
            raise DecisionEngineResponseParseFailed("模型服务返回的外层响应不是合法 JSON") from exc

        if not isinstance(parsed, dict):
            logger.warning(
                "真实模型外层响应不是 JSON 对象: experiment_id=%s batch_id=%s request_id=%s parsed_type=%s body=%s",
                context.experiment_id,
                context.batch_id,
                context.request_id,
                type(parsed).__name__,
                _truncate_for_log(raw_body, MAX_PROVIDER_BODY_LOG_CHARS),
            )
            raise DecisionEngineResponseParseFailed("模型服务返回的外层响应必须是 JSON 对象")
        return parsed

    def _next_provider(self) -> DecisionProvider:
        """按轮询顺序选择 provider，保证同一批次并发调用时计数安全。"""

        if not self.providers:
            return DecisionProvider(name="provider_1", base_url="", api_key="", model="")
        with self._provider_lock:
            provider = self.providers[self._provider_index % len(self.providers)]
            self._provider_index += 1
        return provider


def _normalize_providers(
    providers: Sequence[object] | None,
    *,
    fallback_base_url: str,
    fallback_api_key: str,
    fallback_model: str,
) -> tuple[DecisionProvider, ...]:
    """把配置层 provider 对象转换成引擎内部只读结构。"""

    if not providers:
        return (
            DecisionProvider(
                name="provider_1",
                base_url=fallback_base_url.rstrip("/"),
                api_key=fallback_api_key.strip(),
                model=fallback_model.strip(),
            ),
        )

    normalized: list[DecisionProvider] = []
    for index, provider in enumerate(providers, start=1):
        name = str(getattr(provider, "name", f"provider_{index}")).strip() or f"provider_{index}"
        base_url = str(getattr(provider, "base_url", "")).strip().rstrip("/")
        api_key = str(getattr(provider, "api_key", "")).strip()
        model = str(getattr(provider, "model", "")).strip()
        normalized.append(
            DecisionProvider(
                name=name,
                base_url=base_url,
                api_key=api_key,
                model=model,
            )
        )
    return tuple(normalized)


def _extract_choice_content(response_json: dict[str, object]) -> str:
    """从 OpenAI-compatible 外层响应中提取第一条 message.content。"""

    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices:
        raise DecisionEngineResponseParseFailed("模型服务响应缺少 choices")

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise DecisionEngineResponseParseFailed("模型服务响应中的 choice 结构不合法")

    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise DecisionEngineResponseParseFailed("模型服务响应缺少 message")

    content = message.get("content")
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                text_parts.append(text)
        if text_parts:
            return "\n".join(text_parts)

    raise DecisionEngineResponseParseFailed("模型服务响应缺少可解析的 message.content")


def _normalize_timeout_seconds(timeout_ms: int) -> float:
    """把实验配置的毫秒超时转换成 urlopen 使用的秒。"""

    if timeout_ms <= 0:
        return 30.0
    return max(timeout_ms / 1000, 1.0)


def _extract_logged_request_prompts(payload: dict[str, object]) -> tuple[str, str]:
    """从请求体中取出 system/user prompt，仅用于 debug 日志预览。"""

    messages = payload.get("messages")
    if not isinstance(messages, list):
        return "", ""

    system_prompt = ""
    user_prompt = ""
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if not isinstance(content, str):
            continue
        if role == "system" and not system_prompt:
            system_prompt = content
        elif role == "user" and not user_prompt:
            user_prompt = content
    return system_prompt, user_prompt


def _truncate_for_log(value: str, limit: int) -> str:
    """限制日志中的模型请求/响应长度，避免大 prompt 或异常响应刷爆日志。"""

    if limit <= 0 or len(value) <= limit:
        return value
    return f"{value[:limit]}...(truncated {len(value) - limit} chars)"
