using System;

namespace CrowdSim.Data.Experiments
{
    [Serializable]
    public class LlmConfigDto
    {
        public string model;
        public float temperature;
        public int max_tokens;
        public int timeout_ms;
    }

    [Serializable]
    public class DecisionConfigDto
    {
        public int batch_interval_ms;
        public int batch_size;
        public bool fallback_to_algorithm;
    }

    [Serializable]
    public class ExperimentCreateRequestDto
    {
        public string name;
        public string description;
        public string map_id;
        public LlmConfigDto llm_config;
        public DecisionConfigDto decision_config;
        public string[] tags;
    }

    [Serializable]
    public class ExperimentCreatedDataDto
    {
        public string experiment_id;
        public string status;
        public long created_at_ms;
    }

    [Serializable]
    public class ExperimentCreatedResponseDto
    {
        public int code;
        public string message;
        public ExperimentCreatedDataDto data;
        public string request_id;
        public long timestamp_ms;
    }

    [Serializable]
    public class ExperimentStartRequestDto
    {
        public long actual_start_time_ms;
    }

    [Serializable]
    public class ExperimentStartedDataDto
    {
        public string experiment_id;
        public string status;
        public long started_at_ms;
    }

    [Serializable]
    public class ExperimentStartedResponseDto
    {
        public int code;
        public string message;
        public ExperimentStartedDataDto data;
        public string request_id;
        public long timestamp_ms;
    }

    [Serializable]
    public class ExperimentCompleteRequestDto
    {
        public string reason;
        public int actual_duration_ms;
        public int final_agent_count;
    }

    [Serializable]
    public class ExperimentCompletedDataDto
    {
        public string experiment_id;
        public string status;
    }

    [Serializable]
    public class ExperimentCompletedResponseDto
    {
        public int code;
        public string message;
        public ExperimentCompletedDataDto data;
        public string request_id;
        public long timestamp_ms;
    }
}
