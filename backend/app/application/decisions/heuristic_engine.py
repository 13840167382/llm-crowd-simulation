"""本地启发式决策引擎。

该引擎用于本地开发、压测和真实模型不可用时的低成本验证。它不调用外部服务，
但仍返回和真实 LLM 一致的 DecisionEngineResult，让 service 层的落库、统计、
fallback 和 override 逻辑保持同一条代码路径。
"""

from __future__ import annotations

from backend.app.application.decisions.engine import (
    DecisionEngineContext,
    DecisionEngineResponseParseFailed,
    DecisionEngineResult,
)
from backend.app.schemas.decisions import AvailableExitOption, DecisionAgentRequest


class HeuristicDecisionEngine:
    """启发式决策引擎。

    这个实现不依赖外部网络，但会尽量按 PDF 的 `llm` 语义返回结构化结果，
    让服务层可以稳定复用同一套 fallback、统计、写库逻辑。
    """

    def decide(
        self,
        agent: DecisionAgentRequest,
        *,
        context: DecisionEngineContext,
        llm_timeout_ms: int,
        llm_model: str,
    ) -> DecisionEngineResult:
        """按算法推荐或最高评分候选出口生成决策结果。"""

        del context
        del llm_timeout_ms
        del llm_model

        selected_exit = self._select_exit(agent)
        if selected_exit is None:
            raise DecisionEngineResponseParseFailed(
                f"人物 `{agent.agent_id}` 没有可用于决策的候选出口"
            )

        return DecisionEngineResult(
            chosen_exit_id=selected_exit.exit_id,
            reasoning=self._build_reasoning(agent, selected_exit),
            confidence=_normalize_score(selected_exit.algorithm_score),
        )

    def _select_exit(self, agent: DecisionAgentRequest) -> AvailableExitOption | None:
        """选择启发式出口，优先尊重 algorithm_recommendation。"""

        if agent.algorithm_recommendation is not None:
            for option in agent.available_exits:
                if option.exit_id == agent.algorithm_recommendation:
                    return option

        if not agent.available_exits:
            return None

        return max(agent.available_exits, key=lambda option: (option.algorithm_score, option.exit_id))

    def _build_reasoning(
        self,
        agent: DecisionAgentRequest,
        selected_exit: AvailableExitOption,
    ) -> str:
        """生成一条简短理由，便于前端和日志区分推荐一致/改选两种情况。"""

        recommendation_aligned = agent.algorithm_recommendation == selected_exit.exit_id
        recommendation_text = (
            f"沿用算法推荐出口 `{selected_exit.exit_id}`。"
            if recommendation_aligned
            else f"在综合比较后改选出口 `{selected_exit.exit_id}`。"
        )
        return (
            f"{agent.dilemma_summary}"
            f"{recommendation_text}"
            f"{selected_exit.nl_description}"
        )


def _normalize_score(score: float | int | None) -> float | None:
    """把算法评分裁剪到和 LLM confidence 一致的 0~1 区间。"""

    if score is None:
        return None

    numeric_score = float(score)
    if numeric_score < 0.0:
        return 0.0
    if numeric_score > 1.0:
        return 1.0
    return round(numeric_score, 4)
