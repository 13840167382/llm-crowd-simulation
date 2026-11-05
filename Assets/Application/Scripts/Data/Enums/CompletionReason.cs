using System;

namespace CrowdSim.Data.Enums
{
    public enum CompletionReason
    {
        SimulationFinished,
        Interrupted,
        Error,
    }

    public static class CompletionReasonMapper
    {
        public static bool TryParse(string raw, out CompletionReason reason)
        {
            switch ((raw ?? string.Empty).Trim().ToLowerInvariant())
            {
                case "simulation_finished":
                    reason = CompletionReason.SimulationFinished;
                    return true;
                case "interrupted":
                    reason = CompletionReason.Interrupted;
                    return true;
                case "error":
                    reason = CompletionReason.Error;
                    return true;
                default:
                    reason = default;
                    return false;
            }
        }

        public static string ToApiValue(this CompletionReason reason)
        {
            switch (reason)
            {
                case CompletionReason.SimulationFinished:
                    return "simulation_finished";
                case CompletionReason.Interrupted:
                    return "interrupted";
                case CompletionReason.Error:
                    return "error";
                default:
                    throw new ArgumentOutOfRangeException(nameof(reason), reason, "Unknown completion reason");
            }
        }
    }
}
