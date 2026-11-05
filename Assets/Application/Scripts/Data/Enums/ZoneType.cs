using System;

namespace CrowdSim.Data.Enums
{
    public enum ZoneType
    {
        Passage,
        Plaza,
        Exhibit,
        Rest,
        Commercial,
        Service,
        ExitFinal,
        Other,
    }

    public static class ZoneTypeMapper
    {
        public static bool TryParse(string raw, out ZoneType zoneType)
        {
            switch ((raw ?? string.Empty).Trim().ToLowerInvariant())
            {
                case "passage":
                    zoneType = ZoneType.Passage;
                    return true;
                case "plaza":
                    zoneType = ZoneType.Plaza;
                    return true;
                case "exhibit":
                    zoneType = ZoneType.Exhibit;
                    return true;
                case "rest":
                    zoneType = ZoneType.Rest;
                    return true;
                case "commercial":
                    zoneType = ZoneType.Commercial;
                    return true;
                case "service":
                    zoneType = ZoneType.Service;
                    return true;
                case "exit_final":
                    zoneType = ZoneType.ExitFinal;
                    return true;
                case "other":
                    zoneType = ZoneType.Other;
                    return true;
                default:
                    zoneType = default;
                    return false;
            }
        }

        public static string ToApiValue(this ZoneType zoneType)
        {
            switch (zoneType)
            {
                case ZoneType.Passage:
                    return "passage";
                case ZoneType.Plaza:
                    return "plaza";
                case ZoneType.Exhibit:
                    return "exhibit";
                case ZoneType.Rest:
                    return "rest";
                case ZoneType.Commercial:
                    return "commercial";
                case ZoneType.Service:
                    return "service";
                case ZoneType.ExitFinal:
                    return "exit_final";
                case ZoneType.Other:
                    return "other";
                default:
                    throw new ArgumentOutOfRangeException(nameof(zoneType), zoneType, "Unknown zone type");
            }
        }
    }
}
