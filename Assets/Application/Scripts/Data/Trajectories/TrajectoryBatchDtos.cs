using System;
using CrowdSim.Data.Common;
using CrowdSim.Data.Topology;

namespace CrowdSim.Data.Trajectories
{
    [Serializable]
    public class TrajectoryPointIngestItemDto
    {
        public string agent_id;
        public string crowd_type;
        public Position2DDto position;
        public string zone_id;
        public float speed;
        public string state;
        public string target_exit_id;
        public bool is_queuing;
    }

    [Serializable]
    public class TrajectoryBatchRequestDto
    {
        public string batch_id;
        public int simulation_time_ms;
        public int sample_interval_ms;
        public TrajectoryPointIngestItemDto[] points;
    }

    [Serializable]
    public class TrajectoryBatchResponseDataDto
    {
        public string batch_id;
        public int accepted_count;
        public int rejected_count;
        public int total_stored_points;
    }

    [Serializable]
    public class TrajectoryBatchResponseDto : ApiResponseMeta
    {
        public TrajectoryBatchResponseDataDto data;
    }
}
