"""决策引擎公共接口与工厂。

这里负责两件事：
1. 定义决策引擎的统一输入输出契约。
2. 按配置选择当前实际使用的引擎实现。

后续如果需要替换真实模型，只要保持这里暴露出来的接口不变，
业务层基本不用重写。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.app.core.config import get_settings
from backend.app.infrastructure.observability.logger import get_logger
from backend.app.schemas.decisions import DecisionAgentRequest

logger = get_logger(__name__)


class DecisionEngineError(Exception):
    """决策引擎基础异常。"""


class DecisionEngineCallFailed(DecisionEngineError):
    """单次模型调用失败。"""


class DecisionEngineResponseParseFailed(DecisionEngineError):
    """模型响应无法通过接口契约校验。"""


class DecisionEngineUnavailable(DecisionEngineError):
    """当前环境下没有可用的决策引擎。"""


@dataclass(frozen=True, slots=True)
class DecisionEngineContext:
    """单个 agent 决策时的上下文。"""

    experiment_id: str
    batch_id: str
    request_id: str | None
    simulation_time_ms: int
    crowd_type_display_name: str


@dataclass(frozen=True, slots=True)
class DecisionEngineResult:
    """决策引擎输出。"""

    chosen_exit_id: str
    reasoning: str | None
    confidence: float | None


class DecisionEngine(Protocol):
    """决策引擎统一协议。"""

    def decide(
        self,
        agent: DecisionAgentRequest,
        *,
        context: DecisionEngineContext,
        llm_timeout_ms: int,
        llm_model: str,
    ) -> DecisionEngineResult:
        """为单个 agent 生成决策结果。"""


def get_decision_engine() -> DecisionEngine:
    """按配置获取当前启用的决策引擎。

    这里每次调用都新建轻量引擎对象，避免把实验级模型配置、测试注入对象或外部连接状态
    缓存在全局单例里。真正昂贵的外部调用发生在 decide() 阶段。
    """

    settings = get_settings()
    mode = settings.decision_engine_mode

    if mode in {"heuristic", "mock", "local"}:
        from backend.app.application.decisions.heuristic_engine import HeuristicDecisionEngine

        logger.info("决策引擎已启用本地启发式模式: mode=%s", mode)
        return HeuristicDecisionEngine()

    if mode in {"openai", "openai_compatible", "llm"}:
        from backend.app.application.decisions.llm_engine import OpenAICompatibleDecisionEngine

        logger.info(
            "决策引擎已启用真实模型模式: mode=%s provider_count=%s base_url=%s model_override=%s prompt_version=%s",
            mode,
            len(settings.decision_providers),
            settings.decision_provider_base_url,
            settings.decision_provider_model or "<follow_experiment>",
            settings.decision_prompt_version,
        )
        return OpenAICompatibleDecisionEngine(
            base_url=settings.decision_provider_base_url,
            api_key=settings.decision_provider_api_key,
            provider_model=settings.decision_provider_model,
            temperature=settings.decision_provider_temperature,
            prompt_version=settings.decision_prompt_version,
            providers=settings.decision_providers,
        )

    raise DecisionEngineUnavailable(f"当前决策引擎模式 `{mode}` 不可用")


__all__ = [
    "DecisionEngine",
    "DecisionEngineCallFailed",
    "DecisionEngineContext",
    "DecisionEngineError",
    "DecisionEngineResponseParseFailed",
    "DecisionEngineResult",
    "DecisionEngineUnavailable",
    "get_decision_engine",
]
