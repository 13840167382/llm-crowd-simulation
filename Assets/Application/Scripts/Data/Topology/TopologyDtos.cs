using System;

namespace CrowdSim.Data.Topology
{
    [Serializable]
    public class Bounds2DDto
    {
        public float min_x;
        public float min_z;
        public float max_x;
        public float max_z;
    }

    [Serializable]
    public class Position2DDto
    {
        public float x;
        public float z;
    }

    [Serializable]
    public class TopologyZoneDto
    {
        public string zone_id;
        public string zone_name;
        public string zone_type;
        public Bounds2DDto bounds;
        public int capacity;
        public bool is_entry_zone;
    }

    [Serializable]
    public class TopologyExitDto
    {
        public string exit_id;
        public string source_zone_id;
        public string target_zone_id;
        public string exit_type;
        public Position2DDto position;
        public int pass_capacity_per_sec;
    }

    [Serializable]
    public class TopologyUpsertRequestDto
    {
        public TopologyZoneDto[] zones;
        public TopologyExitDto[] exits;
    }

    [Serializable]
    public class TopologyUpsertDataDto
    {
        public int zone_count;
        public int exit_count;
        public string[] entry_zones;
        public string[] validation_errors;
    }

    [Serializable]
    public class TopologyUpsertResponseDto
    {
        public int code;
        public string message;
        public TopologyUpsertDataDto data;
        public string request_id;
        public long timestamp_ms;
    }
}
