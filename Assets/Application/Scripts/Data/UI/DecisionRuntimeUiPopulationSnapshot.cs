using System;

namespace CrowdSim.Data.UI
{
    [Serializable]
    public sealed class DecisionRuntimeUiPopulationSnapshot
    {
        public long snapshot_at_unix_ms;
        public int visible_agent_count;
        public ZonePopulationSnapshotItem[] zone_populations = Array.Empty<ZonePopulationSnapshotItem>();
        public TotalCrowdTypePopulationSnapshotItem[] total_crowd_type_populations = Array.Empty<TotalCrowdTypePopulationSnapshotItem>();

        public static DecisionRuntimeUiPopulationSnapshot Empty()
        {
            return new DecisionRuntimeUiPopulationSnapshot();
        }
    }

    [Serializable]
    public sealed class ZonePopulationSnapshotItem
    {
        public string zone_id;
        public bool is_final_exit_zone;
        public int total_count;
        public CrowdTypePopulationSnapshotItem[] crowd_type_populations = Array.Empty<CrowdTypePopulationSnapshotItem>();
    }

    [Serializable]
    public sealed class CrowdTypePopulationSnapshotItem
    {
        public string crowd_type;
        public int count;
    }

    [Serializable]
    public sealed class TotalCrowdTypePopulationSnapshotItem
    {
        public string crowd_type;
        public int total_count;
        public int visible_agent_count;
    }
}
