using System;

namespace CrowdSim.Data.Decisions
{
    [Serializable]
    public class DecisionExitOptionConfig
    {
        public string exitId;
        public string targetZoneId;
        public float algorithmScore = 0.5f;
        public string nlDescription = "Use this exit by current algorithm recommendation.";

        public bool TryBuildDto(out DecisionAvailableExitOptionDto dto)
        {
            var normalizedExitId = string.IsNullOrWhiteSpace(exitId) ? string.Empty : exitId.Trim();
            var normalizedTargetZoneId = string.IsNullOrWhiteSpace(targetZoneId) ? string.Empty : targetZoneId.Trim();
            if (string.IsNullOrWhiteSpace(normalizedExitId) || string.IsNullOrWhiteSpace(normalizedTargetZoneId))
            {
                dto = null;
                return false;
            }

            dto = new DecisionAvailableExitOptionDto
            {
                exit_id = normalizedExitId,
                target_zone_id = normalizedTargetZoneId,
                algorithm_score = algorithmScore,
                nl_description = string.IsNullOrWhiteSpace(nlDescription)
                    ? "No description"
                    : nlDescription.Trim(),
            };
            return true;
        }
    }
}
