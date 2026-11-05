using System;

namespace CrowdSim.Data.Decisions
{
    [Serializable]
    public sealed class DecisionAgentReportCacheItem
    {
        public string agent_id;
        public DecisionAgentRequestDto request;
        public bool needs_llm_decision;
        public bool is_valid;
        public string error;
        public long reported_at_unix_ms;
        public int source_instance_id;
    }
}
