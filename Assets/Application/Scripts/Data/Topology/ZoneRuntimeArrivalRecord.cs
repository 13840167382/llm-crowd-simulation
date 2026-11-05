using System;

namespace CrowdSim.Data.Topology
{
    [Serializable]
    public sealed class ZoneRuntimeArrivalRecord
    {
        public string zone_id;
        public int arrived_count;
    }
}