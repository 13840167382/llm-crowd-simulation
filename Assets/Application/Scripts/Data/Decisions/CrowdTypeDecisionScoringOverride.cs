using System;

namespace CrowdSim.Data.Decisions
{
    [Serializable]
    public sealed class CrowdTypeDecisionScoringOverride
    {
        public string crowdType = "tourist";
        public LocalDecisionScoringConfig scoring = new LocalDecisionScoringConfig();

        public bool TryGetNormalizedCrowdType(out string normalizedCrowdType)
        {
            normalizedCrowdType = string.IsNullOrWhiteSpace(crowdType)
                ? string.Empty
                : crowdType.Trim();
            return !string.IsNullOrWhiteSpace(normalizedCrowdType);
        }

        public LocalDecisionScoringConfig GetScoringOrDefault(LocalDecisionScoringConfig fallback)
        {
            if (scoring != null)
            {
                return scoring;
            }

            return fallback ?? new LocalDecisionScoringConfig();
        }

        public CrowdTypeDecisionScoringOverride Clone()
        {
            return new CrowdTypeDecisionScoringOverride
            {
                crowdType = crowdType,
                scoring = scoring != null ? scoring.Clone() : new LocalDecisionScoringConfig(),
            };
        }
    }
}
