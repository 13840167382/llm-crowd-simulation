using System;

namespace CrowdSim.Data.Allocation
{
    [Serializable]
    public class RandomEntryDistributionDto
    {
        public string mode = "random_entry";
        public bool avoid_exits = true;
    }

    [Serializable]
    public class ZoneFixedDistributionDto
    {
        public string mode = "zone_fixed";
        public string fixed_zone_id;
        public bool avoid_exits = true;
    }

    [Serializable]
    public class ZoneWeightDto
    {
        public string zone_id;
        public float weight;
    }

    [Serializable]
    public class ZoneWeightedDistributionDto
    {
        public string mode = "zone_weighted";
        public ZoneWeightDto[] zone_weights;
        public bool avoid_exits = true;
    }

    [Serializable]
    public class AllocationEntryZoneWeightConfig
    {
        public string zoneId;
        public float weight = 1f;

        public bool TryBuildDto(out ZoneWeightDto dto)
        {
            var normalizedZoneId = string.IsNullOrWhiteSpace(zoneId) ? string.Empty : zoneId.Trim();
            var normalizedWeight = Math.Max(0f, Math.Min(1f, weight));
            if (string.IsNullOrWhiteSpace(normalizedZoneId) || normalizedWeight <= 0f)
            {
                dto = null;
                return false;
            }

            dto = new ZoneWeightDto
            {
                zone_id = normalizedZoneId,
                weight = normalizedWeight,
            };
            return true;
        }
    }

    [Serializable]
    public class CrowdAllocationGroupDto
    {
        public string type_id;
        public int count;
        public RandomEntryDistributionDto distribution;
    }

    [Serializable]
    public class CrowdAllocationGroupZoneFixedDto
    {
        public string type_id;
        public int count;
        public ZoneFixedDistributionDto distribution;
    }

    [Serializable]
    public class CrowdAllocationGroupZoneWeightedDto
    {
        public string type_id;
        public int count;
        public ZoneWeightedDistributionDto distribution;
    }

    [Serializable]
    public class CrowdAllocationRequestDto
    {
        public CrowdAllocationGroupDto[] groups;
        public string agent_id_prefix;
    }

    [Serializable]
    public class CrowdAllocationRequestZoneFixedDto
    {
        public CrowdAllocationGroupZoneFixedDto[] groups;
        public string agent_id_prefix;
    }

    [Serializable]
    public class CrowdAllocationRequestZoneWeightedDto
    {
        public CrowdAllocationGroupZoneWeightedDto[] groups;
        public string agent_id_prefix;
    }

    [Serializable]
    public class CrowdAllocationResponseDataDto
    {
        public string experiment_id;
        public string status;
        public int total_count;
        public int group_count;
        public string[] validation_errors;
    }

    [Serializable]
    public class CrowdAllocationResponseDto
    {
        public int code;
        public string message;
        public CrowdAllocationResponseDataDto data;
        public string request_id;
        public long timestamp_ms;
    }
}
