using System;

namespace CrowdSim.Data.Enums
{
    public enum ExitType
    {
        Portal,
        Ramp,
        Elevator,
        Stair,
    }

    public static class ExitTypeMapper
    {
        public static bool TryParse(string raw, out ExitType exitType)
        {
            switch ((raw ?? string.Empty).Trim().ToLowerInvariant())
            {
                case "portal":
                    exitType = ExitType.Portal;
                    return true;
                case "ramp":
                    exitType = ExitType.Ramp;
                    return true;
                case "elevator":
                    exitType = ExitType.Elevator;
                    return true;
                case "stair":
                    exitType = ExitType.Stair;
                    return true;
                default:
                    exitType = default;
                    return false;
            }
        }

        public static string ToApiValue(this ExitType exitType)
        {
            switch (exitType)
            {
                case ExitType.Portal:
                    return "portal";
                case ExitType.Ramp:
                    return "ramp";
                case ExitType.Elevator:
                    return "elevator";
                case ExitType.Stair:
                    return "stair";
                default:
                    throw new ArgumentOutOfRangeException(nameof(exitType), exitType, "Unknown exit type");
            }
        }
    }
}
