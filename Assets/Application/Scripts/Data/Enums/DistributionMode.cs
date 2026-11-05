using System;

namespace CrowdSim.Data.Enums
{
    public enum DistributionMode
    {
        ZoneWeighted,
        RandomEntry,
        PointSpread,
        ZoneFixed,
    }

    public static class DistributionModeMapper
    {
        public static bool TryParse(string raw, out DistributionMode mode)
        {
            switch ((raw ?? string.Empty).Trim().ToLowerInvariant())
            {
                case "zone_weighted":
                    mode = DistributionMode.ZoneWeighted;
                    return true;
                case "random_entry":
                    mode = DistributionMode.RandomEntry;
                    return true;
                case "point_spread":
                    mode = DistributionMode.PointSpread;
                    return true;
                case "zone_fixed":
                    mode = DistributionMode.ZoneFixed;
                    return true;
                default:
                    mode = default;
                    return false;
            }
        }

        public static string ToApiValue(this DistributionMode mode)
        {
            switch (mode)
            {
                case DistributionMode.ZoneWeighted:
                    return "zone_weighted";
                case DistributionMode.RandomEntry:
                    return "random_entry";
                case DistributionMode.PointSpread:
                    return "point_spread";
                case DistributionMode.ZoneFixed:
                    return "zone_fixed";
                default:
                    throw new ArgumentOutOfRangeException(nameof(mode), mode, "Unknown distribution mode");
            }
        }
    }
}
