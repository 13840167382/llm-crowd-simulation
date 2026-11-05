using System;

namespace CrowdSim.Data.Decisions
{
    [Serializable]
    public class DecisionAvailableExitOptionDto
    {
        public string exit_id;
        public string target_zone_id;
        public float algorithm_score;
        /*
        sample:
        "通往坡道A区(展览区入口). 展览区对该类人群吸引力很强(权重0.9), 但当前坡道口排队约45人, 预计等待12秒, 通过后到达的展览区内部人也较多."
        "通往休息区1号. 休息区对该类人群吸引力较弱(权重0.3), 但当前几乎无人排队, 可即时通过, 内部空旷安静."
        "通往商业区B. 吸引力中等(权重0.6), 有少量排队约10人, 预计等待3秒, 商业区内人流适中."
        */
        public string nl_description;
    }

    [Serializable]
    public class DecisionAgentRequestDto
    {
        public string agent_id;
        public string crowd_type;
        public string current_zone_id;
        public int stay_duration_ms;
        /*
        patience_ratio_display 转换规则：
        - < 0.5 → "较低，不太着急"
        - 0.5-0.8 → "中等，开始有些焦虑"
        - 0.8-1.0 → "较高，比较急迫"
        - \> 1.0 → "已超出容忍限度，非常急迫"
        */
        public float patience_ratio;
        public string algorithm_recommendation;
        // sample: "一位观光游客已在主大厅停留16.2秒(耐心消耗81%), 偏好展览区, 当前面临出口选择困境。"
        public string dilemma_summary;
        public DecisionAvailableExitOptionDto[] available_exits;
    }

    [Serializable]
    public class DecisionBatchRequestDto
    {
        public string batch_id;
        public int simulation_time_ms;
        public DecisionAgentRequestDto[] agents;
    }

    [Serializable]
    public class DecisionResultItemDto
    {
        public string agent_id;
        public string chosen_exit_id;
        public string decision_source;
        public string reasoning;
        public float confidence;
    }

    [Serializable]
    public class DecisionBatchResponseDataDto
    {
        public string batch_id;
        public long processed_at_ms;
        public int processing_duration_ms;
        public int total_count;
        public int success_count;
        public int fallback_count;
        public DecisionResultItemDto[] decisions;
    }

    [Serializable]
    public class DecisionBatchResponseDto
    {
        public int code;
        public string message;
        public DecisionBatchResponseDataDto data;
        public string request_id;
        public long timestamp_ms;
    }

    /// <summary>
    /// Agent 决策覆写模式。
    /// </summary>
    public enum DecisionOverrideMode
    {
        /// <summary>走正常决策流程（LLM / 本地算法）。</summary>
        Normal = 0,
        /// <summary>按固定区域列表循环移动。</summary>
        FixedPath = 1,
        /// <summary>每次从候选区域中完全随机选择。</summary>
        Random = 2,
    }

    /// <summary>
    /// 固定路径配置：让指定人群类型的单位不走正常决策，沿固定区域列表循环移动。
    /// </summary>
    [Serializable]
    public class FixedPathConfig
    {
        /// <summary>匹配的人群类型 ID。</summary>
        public string crowdType;
        /// <summary>走固定路径的单位数量上限。</summary>
        public int count = 1;
        /// <summary>固定路径区域 ID 列表，到达末尾后回到开头循环。</summary>
        public string[] zoneIds;

        public bool IsValid()
        {
            return !string.IsNullOrWhiteSpace(crowdType)
                && count > 0
                && zoneIds != null
                && zoneIds.Length > 0;
        }
    }
}
