"""决策 Prompt 组装器。

Prompt 只包含单个 agent 的当前处境、算法推荐和候选出口列表。
服务端仍会在 response_parser 和 service 层校验 chosen_exit_id，不能只依赖 prompt 约束模型。
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.schemas.decisions import AvailableExitOption, DecisionAgentRequest


@dataclass(frozen=True, slots=True)
class BuiltDecisionPrompt:
    """组装完成的 Prompt。"""

    system_prompt: str
    user_prompt: str
    prompt_version: str


class DecisionPromptBuilder:
    """按约定格式组装决策 Prompt。

    prompt_version 是向后兼容开关；当需要调整提示词结构时，应新增版本而不是静默改旧版本。
    """

    def __init__(self, *, prompt_version: str = "v1") -> None:
        self.prompt_version = prompt_version

    def build(
        self,
        agent: DecisionAgentRequest,
        *,
        crowd_type_display_name: str,
    ) -> BuiltDecisionPrompt:
        """把单个 agent 决策输入转成模型可直接消费的 prompt。"""

        if self.prompt_version != "v1":
            raise ValueError(f"当前不支持的决策 prompt 版本 `{self.prompt_version}`")

        system_prompt = (
            "你是一个人群流动模拟系统的行为决策引擎。"
            "你将收到一个模拟人物的当前处境描述和若干可选行动方案，请根据描述为该人物选择一个最优方案。\n"
            "你只能从给定的可选出口中选择一个 chosen_exit_id。\n"
            "你必须且只能以JSON格式回复，不要输出任何其他内容。\n"
            "如果算法推荐为空，不要自行编造算法推荐。\n"
            "confidence 必须是 0.0 到 1.0 之间的数字。\n"
            "reasoning 必须是一句话。"
        )

        algorithm_description = self._build_algorithm_recommendation_text(agent)
        exits_description = self._build_available_exits_text(agent.available_exits)
        patience_ratio_display = self._format_patience_ratio(agent.patience_ratio)

        user_prompt = (
            f"人物类型：{crowd_type_display_name}\n"
            f"当前处境：{agent.dilemma_summary}\n"
            f"耐心消耗：{patience_ratio_display}\n"
            f"算法推荐：{algorithm_description}\n"
            f"可选方案：\n"
            f"{exits_description}\n"
            "请回复：\n"
            '{"chosen_exit_id": "你选择的出口ID", "reasoning": "一句话理由", "confidence": 0.0到1.0之间的数字}'
        )

        return BuiltDecisionPrompt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            prompt_version=self.prompt_version,
        )

    def _build_algorithm_recommendation_text(self, agent: DecisionAgentRequest) -> str:
        """把算法推荐转成自然语言，并附带对应候选出口的评分。"""

        if agent.algorithm_recommendation is None:
            return "无"

        option = next(
            (item for item in agent.available_exits if item.exit_id == agent.algorithm_recommendation),
            None,
        )
        if option is None:
            return f"{agent.algorithm_recommendation}（算法评分：未知）"

        return f"{agent.algorithm_recommendation}（算法评分：{self._format_score(option.algorithm_score)}）"

    def _build_available_exits_text(self, options: list[AvailableExitOption]) -> str:
        """把候选出口列表格式化成模型容易逐项比较的文本。"""

        return "\n".join(
            (
                f"[{index}] 出口 {option.exit_id} → {option.target_zone_id}\n"
                f"描述：{option.nl_description}\n"
                f"算法评分：{self._format_score(option.algorithm_score)}"
            )
            for index, option in enumerate(options, start=1)
        )

    def _format_patience_ratio(self, value: float) -> str:
        """把 0~1 以上的耐心消耗值翻译成更稳定的语义标签。"""

        if value < 0.5:
            return "较低，不太着急"
        if value <= 0.8:
            return "中等，开始有些焦虑"
        if value <= 1.0:
            return "较高，比较急迫"
        return "已超出容忍限度，非常急迫"

    def _format_score(self, value: float) -> str:
        """统一算法评分在 prompt 中的展示精度。"""

        return f"{float(value):.2f}"
