using UnityEngine;

namespace CrowdSim.Data.Decisions
{
    /// <summary>
    /// Agent 决策覆写配置 ScriptableObject：让指定人群类型的单位按配置的模式选择出口，
    /// 而不走正常的 LLM / 本地算法决策流程。
    /// 在 Project 窗口中右键 → Create → CrowdSim → Agent Decision Override 创建。
    /// </summary>
    [CreateAssetMenu(fileName = "DecisionOverride", menuName = "CrowdSim/Agent Decision Override", order = 100)]
    public sealed class FixedPathConfigSO : ScriptableObject
    {
        [Tooltip("决策覆写模式。")]
        public DecisionOverrideMode mode = DecisionOverrideMode.FixedPath;

        [Tooltip("匹配的人群类型 ID。")]
        public string crowdType;

        [Tooltip("走该覆写模式的单位数量上限。")]
        [Min(1)]
        public int count = 1;

        [Tooltip("区域 ID 列表。FixedPath 模式按顺序循环；Random 模式从中随机选取。")]
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
