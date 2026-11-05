using System;

namespace CrowdSim.Data.Enums
{
    public enum DecisionSource
    {
        Llm,
        Fallback,
        Algorithm,
        None,
    }

    public static class DecisionSourceMapper
    {
        public static bool TryParse(string raw, out DecisionSource source)
        {
            switch ((raw ?? string.Empty).Trim().ToLowerInvariant())
            {
                case "llm":
                    source = DecisionSource.Llm;
                    return true;
                case "fallback":
                    source = DecisionSource.Fallback;
                    return true;
                case "algorithm":
                    source = DecisionSource.Algorithm;
                    return true;
                case "none":
                    source = DecisionSource.None;
                    return true;
                default:
                    source = default;
                    return false;
            }
        }

        public static string ToApiValue(this DecisionSource source)
        {
            switch (source)
            {
                case DecisionSource.Llm:
                    return "llm";
                case DecisionSource.Fallback:
                    return "fallback";
                case DecisionSource.Algorithm:
                    return "algorithm";
                case DecisionSource.None:
                    return "none";
                default:
                    throw new ArgumentOutOfRangeException(nameof(source), source, "Unknown decision source");
            }
        }
    }
}
