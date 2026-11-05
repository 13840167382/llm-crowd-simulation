using System;

namespace CrowdSim.Data.Enums
{
    public enum EntryZoneAllocationMode
    {
        RandomEntry,
        ZoneWeighted,
        ZoneFixed,
    }

    public static class EntryZoneAllocationModeMapper
    {
        public static string ToApiValue(this EntryZoneAllocationMode mode)
        {
            switch (mode)
            {
                case EntryZoneAllocationMode.RandomEntry:
                    return "random_entry";
                case EntryZoneAllocationMode.ZoneWeighted:
                    return "zone_weighted";
                case EntryZoneAllocationMode.ZoneFixed:
                    return "zone_fixed";
                default:
                    throw new ArgumentOutOfRangeException(nameof(mode), mode, "Unknown entry zone allocation mode");
            }
        }
    }
}