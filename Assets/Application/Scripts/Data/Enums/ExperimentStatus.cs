using System;

namespace CrowdSim.Data.Enums
{
    public enum ExperimentStatus
    {
        Initialized,
        Configured,
        Running,
        Completed,
        Failed,
    }

    public static class ExperimentStatusMapper
    {
        public static bool TryParse(string raw, out ExperimentStatus status)
        {
            switch ((raw ?? string.Empty).Trim().ToLowerInvariant())
            {
                case "initialized":
                    status = ExperimentStatus.Initialized;
                    return true;
                case "configured":
                    status = ExperimentStatus.Configured;
                    return true;
                case "running":
                    status = ExperimentStatus.Running;
                    return true;
                case "completed":
                    status = ExperimentStatus.Completed;
                    return true;
                case "failed":
                    status = ExperimentStatus.Failed;
                    return true;
                default:
                    status = default;
                    return false;
            }
        }

        public static string ToApiValue(this ExperimentStatus status)
        {
            switch (status)
            {
                case ExperimentStatus.Initialized:
                    return "initialized";
                case ExperimentStatus.Configured:
                    return "configured";
                case ExperimentStatus.Running:
                    return "running";
                case ExperimentStatus.Completed:
                    return "completed";
                case ExperimentStatus.Failed:
                    return "failed";
                default:
                    throw new ArgumentOutOfRangeException(nameof(status), status, "Unknown experiment status");
            }
        }
    }
}
