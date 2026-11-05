using System;
using System.Collections.Generic;

namespace CrowdSim.Data.Analytics
{
    [Serializable]
    public class HeatmapGridOriginDto
    {
        public float x;
        public float z;
    }

    [Serializable]
    public class HeatmapGridConfigDto
    {
        public HeatmapGridOriginDto origin;
        public float cell_size;
        public int cols;
        public int rows;
    }

    [Serializable]
    public class HeatmapStatisticsDto
    {
        public float min_value;
        public float max_value;
        public float raw_min;
        public float raw_max;
        public float percentile_99_raw;
        public int non_empty_cells;
        public int total_cells;
    }

    [Serializable]
    public class HeatmapRequestDto
    {
        public int time_start_ms;
        public int time_end_ms;
        public HeatmapGridConfigDto grid_config;
        public string metric;
        public string crowd_type_filter;
        public string zone_id_filter;
        public string normalization;
    }

    [Serializable]
    public class HeatmapDataDto
    {
        public HeatmapGridConfigDto grid_config;
        public string metric;
        public string normalization;
        public string crowd_type_filter;
        public HeatmapStatisticsDto statistics;
        public float[] cells;
    }

    [Serializable]
    public class HeatmapResponseDto
    {
        public int code;
        public string message;
        public HeatmapDataDto data;
        public string request_id;
        public long timestamp_ms;
    }

    [Serializable]
    public class HeatmapCompareRequestDto
    {
        public int time_start_ms;
        public int time_end_ms;
        public HeatmapGridConfigDto grid_config;
        public string metric;
        public string[] crowd_types;
        public string normalization;
    }

    [Serializable]
    public class HeatmapCompareLayerDto
    {
        public string crowd_type;
        public string display_name;
        public string color_hex;
        public HeatmapStatisticsDto statistics;
        public float[] cells;
    }

    [Serializable]
    public class HeatmapCompareDataDto
    {
        public HeatmapGridConfigDto grid_config;
        public string metric;
        public HeatmapCompareLayerDto[] layers;
    }

    [Serializable]
    public class HeatmapCompareResponseDto
    {
        public int code;
        public string message;
        public HeatmapCompareDataDto data;
        public string request_id;
        public long timestamp_ms;
    }

    [Serializable]
    public class PathTraceItemDto
    {
        public string agent_id;
        public int point_count;
    }

    [Serializable]
    public class PathTracesDataDto
    {
        public string crowd_type;
        public string display_name;
        public string color_hex;
        public int agent_count_in_response;
        public int total_agents_of_type;
        public int sample_rate;
        public PathTraceItemDto[] traces;
    }

    [Serializable]
    public class PathTracesResponseDto
    {
        public int code;
        public string message;
        public PathTracesDataDto data;
        public string request_id;
        public long timestamp_ms;
    }

    public struct PathTraceParsedPoint
    {
        public int timestampMs;
        public float x;
        public float z;
    }

    public class PathTraceParsedAgent
    {
        public string AgentId;
        public int PointCount;
        public readonly List<PathTraceParsedPoint> Points = new List<PathTraceParsedPoint>();
    }

    public class PathTracesParsedData
    {
        public string CrowdType;
        public string DisplayName;
        public string ColorHex;
        public int AgentCountInResponse;
        public int TotalAgentsOfType;
        public int SampleRate;
        public readonly List<PathTraceParsedAgent> Traces = new List<PathTraceParsedAgent>();
    }
}
