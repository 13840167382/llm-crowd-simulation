using System;
using System.Collections.Generic;
using CrowdSim.Data.Allocation;
using CrowdSim.Data.Decisions;
using UnityEngine;

namespace CrowdSim.Data.Crowd
{
    [CreateAssetMenu(fileName = "CrowdDefaultProfile", menuName = "CrowdSim/Crowd/Default Crowd Profile")]
    public sealed class CrowdDefaultProfile : ScriptableObject
    {
        [SerializeField] private string profileId = "default";
        [SerializeField] private string displayName = "Default Crowd Profile";

        [Header("Initial Crowd")]
        [SerializeField] private string preferredCrowdTypeId = "tourist";
        [SerializeField, Min(1)] private int initialCrowdCount = 20;
        [SerializeField] private CrowdTypeAllocationConfig[] crowdTypeAllocations = Array.Empty<CrowdTypeAllocationConfig>();

        [Header("Preference Parameters")]
        [SerializeField] private LocalDecisionScoringConfig decisionPreference = new LocalDecisionScoringConfig();
        [SerializeField] private CrowdTypeDecisionScoringOverride[] crowdTypeDecisionPreferences = Array.Empty<CrowdTypeDecisionScoringOverride>();

        public string ProfileId
        {
            get { return string.IsNullOrWhiteSpace(profileId) ? string.Empty : profileId.Trim(); }
        }

        public string DisplayName
        {
            get
            {
                if (!string.IsNullOrWhiteSpace(displayName))
                {
                    return displayName.Trim();
                }

                if (!string.IsNullOrWhiteSpace(profileId))
                {
                    return profileId.Trim();
                }

                return name;
            }
        }

        public string PreferredCrowdTypeId
        {
            get { return string.IsNullOrWhiteSpace(preferredCrowdTypeId) ? string.Empty : preferredCrowdTypeId.Trim(); }
        }

        public int InitialCrowdCount
        {
            get { return Math.Max(1, initialCrowdCount); }
        }

        public CrowdTypeAllocationConfig[] CloneCrowdTypeAllocations()
        {
            if (crowdTypeAllocations == null || crowdTypeAllocations.Length <= 0)
            {
                return Array.Empty<CrowdTypeAllocationConfig>();
            }

            var cloned = new List<CrowdTypeAllocationConfig>(crowdTypeAllocations.Length);
            for (var i = 0; i < crowdTypeAllocations.Length; i++)
            {
                var source = crowdTypeAllocations[i];
                if (source == null)
                {
                    continue;
                }

                cloned.Add(new CrowdTypeAllocationConfig
                {
                    typeId = source.typeId,
                    count = Math.Max(0, source.count),
                });
            }

            return cloned.ToArray();
        }

        public LocalDecisionScoringConfig CloneDecisionPreference()
        {
            if (decisionPreference == null)
            {
                return new LocalDecisionScoringConfig();
            }

            return decisionPreference.Clone();
        }

        public CrowdTypeDecisionScoringOverride[] CloneCrowdTypeDecisionPreferences()
        {
            if (crowdTypeDecisionPreferences == null || crowdTypeDecisionPreferences.Length <= 0)
            {
                return Array.Empty<CrowdTypeDecisionScoringOverride>();
            }

            var cloned = new List<CrowdTypeDecisionScoringOverride>(crowdTypeDecisionPreferences.Length);
            for (var i = 0; i < crowdTypeDecisionPreferences.Length; i++)
            {
                var source = crowdTypeDecisionPreferences[i];
                if (source == null)
                {
                    continue;
                }

                cloned.Add(source.Clone());
            }

            return cloned.ToArray();
        }
    }
}
