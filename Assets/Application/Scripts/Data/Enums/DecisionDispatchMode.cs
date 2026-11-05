using System;

namespace CrowdSim.Data.Enums
{
    public enum DecisionDispatchMode
    {
        LlmOnly,
        LocalOnly,
        HybridPreferLlm,
    }

    public static class DecisionDispatchModeMapper
    {
        public static string ToDisplayName(this DecisionDispatchMode mode)
        {
            switch (mode)
            {
                case DecisionDispatchMode.LlmOnly:
                    return "LLM Only";
                case DecisionDispatchMode.LocalOnly:
                    return "Local Only";
                case DecisionDispatchMode.HybridPreferLlm:
                    return "Hybrid (Prefer LLM)";
                default:
                    throw new ArgumentOutOfRangeException(nameof(mode), mode, "Unknown decision dispatch mode");
            }
        }
    }
}