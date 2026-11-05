using System;

namespace CrowdSim.Data.Decisions
{
    [Serializable]
    public sealed class LocalDecisionScoringConfig
    {
        public float algorithmScoreWeight = 0.30f;
        public float crowdPressureWeight = 0.45f;
        public float distanceWeight = 0.20f;
        public float exitCapacityWeight = 0.05f;
        public float crowdTypeAttractionWeight = 1.10f;
        public float crowdTypeAttractionContrast = 2.00f;
        public int defaultZoneCapacity = 120;
        public float distanceNormalizationMeters = 15f;
        public int exitCapacityNormalization = 4;

        public LocalDecisionScoringConfig Clone()
        {
            return new LocalDecisionScoringConfig
            {
                algorithmScoreWeight = algorithmScoreWeight,
                crowdPressureWeight = crowdPressureWeight,
                distanceWeight = distanceWeight,
                exitCapacityWeight = exitCapacityWeight,
                crowdTypeAttractionWeight = crowdTypeAttractionWeight,
                crowdTypeAttractionContrast = crowdTypeAttractionContrast,
                defaultZoneCapacity = defaultZoneCapacity,
                distanceNormalizationMeters = distanceNormalizationMeters,
                exitCapacityNormalization = exitCapacityNormalization,
            };
        }

        public void OverwriteFrom(LocalDecisionScoringConfig source)
        {
            if (source == null)
            {
                return;
            }

            algorithmScoreWeight = source.algorithmScoreWeight;
            crowdPressureWeight = source.crowdPressureWeight;
            distanceWeight = source.distanceWeight;
            exitCapacityWeight = source.exitCapacityWeight;
            crowdTypeAttractionWeight = source.crowdTypeAttractionWeight;
            crowdTypeAttractionContrast = source.crowdTypeAttractionContrast;
            defaultZoneCapacity = source.defaultZoneCapacity;
            distanceNormalizationMeters = source.distanceNormalizationMeters;
            exitCapacityNormalization = source.exitCapacityNormalization;
        }
    }
}