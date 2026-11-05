using System;
using CrowdSim.Data.Topology;

namespace CrowdSim.Data.Spawning
{
    [Serializable]
    public class SpawnPositionsRequestDto
    {
        public int seed;
    }

    [Serializable]
    public class SpawnedAgentDto
    {
        public string agent_id;
        public string type_id;
        public string zone_id;
        public Position2DDto position;
        public float rotation_y;
    }

    [Serializable]
    public class SpawnPositionsResponseDataDto
    {
        public int seed;
        public int total_count;
        public SpawnedAgentDto[] agents;
        public string[] warnings;
    }

    [Serializable]
    public class SpawnPositionsResponseDto
    {
        public int code;
        public string message;
        public SpawnPositionsResponseDataDto data;
        public string request_id;
        public long timestamp_ms;
    }
}
