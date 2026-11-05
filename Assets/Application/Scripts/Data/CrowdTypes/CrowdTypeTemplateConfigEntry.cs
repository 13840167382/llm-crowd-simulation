using System;
using UnityEngine;

namespace CrowdSim.Data.CrowdTypes
{
    [Serializable]
    public class CrowdTypeTemplateConfigEntry
    {
        private const float MinBaseSpeed = 0.1f;
        private const float MaxBaseSpeed = 5f;
        private const float MinSocialDistance = 0.3f;
        private const float MaxSocialDistance = 5f;
        private const int MinToleranceDurationMs = 1000;
        private const int MaxToleranceDurationMs = 300000;

        public string typeId = "custom_visitor";
        public string displayName = "Custom Visitor";
        public string colorHex = "#336699";
        [Min(MinBaseSpeed)] public float baseSpeed = 1f;
        [Min(MinSocialDistance)] public float socialDistance = 0.8f;
        [Min(MinToleranceDurationMs)] public int toleranceDurationMs = 30000;
        public CrowdTypeAttractionWeightsConfig attractionWeights = new CrowdTypeAttractionWeightsConfig();

        public string GetNormalizedTypeId()
        {
            return NormalizeTypeId(typeId);
        }

        public bool TryBuildCreateRequest(out CrowdTypeCreateRequestDto request, out string normalizedTypeId, out string error)
        {
            normalizedTypeId = NormalizeTypeId(typeId);
            if (string.IsNullOrWhiteSpace(normalizedTypeId))
            {
                request = null;
                error = "Crowd template config entry has empty typeId.";
                return false;
            }

            request = new CrowdTypeCreateRequestDto
            {
                type_id = normalizedTypeId,
                display_name = NormalizeDisplayName(displayName, normalizedTypeId),
                color_hex = NormalizeColorHex(colorHex),
                base_speed = NormalizeBaseSpeed(baseSpeed),
                social_distance = NormalizeSocialDistance(socialDistance),
                tolerance_duration_ms = NormalizeToleranceDurationMs(toleranceDurationMs),
                attraction_weights = ResolveAttractionWeights().BuildDto(),
            };

            error = string.Empty;
            return true;
        }

        public CrowdTypeUpdateRequestDto BuildUpdateRequest()
        {
            var normalizedTypeId = NormalizeTypeId(typeId);
            return new CrowdTypeUpdateRequestDto
            {
                display_name = NormalizeDisplayName(displayName, normalizedTypeId),
                color_hex = NormalizeColorHex(colorHex),
                base_speed = NormalizeBaseSpeed(baseSpeed),
                social_distance = NormalizeSocialDistance(socialDistance),
                tolerance_duration_ms = NormalizeToleranceDurationMs(toleranceDurationMs),
                attraction_weights = ResolveAttractionWeights().BuildDto(),
            };
        }

        public bool IsSemanticallyEqualTo(CrowdTypeTemplateDto remoteTemplate)
        {
            if (remoteTemplate == null)
            {
                return false;
            }

            var normalizedRemoteTypeId = NormalizeTypeId(remoteTemplate.type_id);
            var normalizedLocalTypeId = NormalizeTypeId(typeId);
            if (!string.Equals(normalizedRemoteTypeId, normalizedLocalTypeId, StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }

            if (!string.Equals(
                    NormalizeDisplayName(remoteTemplate.display_name, normalizedRemoteTypeId),
                    NormalizeDisplayName(displayName, normalizedLocalTypeId),
                    StringComparison.Ordinal))
            {
                return false;
            }

            if (!string.Equals(
                    NormalizeColorHex(remoteTemplate.color_hex),
                    NormalizeColorHex(colorHex),
                    StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }

            if (Mathf.Abs(NormalizeBaseSpeed(remoteTemplate.base_speed) - NormalizeBaseSpeed(baseSpeed)) > 0.0001f)
            {
                return false;
            }

            if (Mathf.Abs(NormalizeSocialDistance(remoteTemplate.social_distance) - NormalizeSocialDistance(socialDistance)) > 0.0001f)
            {
                return false;
            }

            if (NormalizeToleranceDurationMs(remoteTemplate.tolerance_duration_ms) != NormalizeToleranceDurationMs(toleranceDurationMs))
            {
                return false;
            }

            return CrowdTypeAttractionWeightsDto.ApproximatelyEquals(
                remoteTemplate.attraction_weights,
                ResolveAttractionWeights().BuildDto());
        }

        public CrowdTypeTemplateConfigEntry Clone()
        {
            return new CrowdTypeTemplateConfigEntry
            {
                typeId = typeId,
                displayName = displayName,
                colorHex = colorHex,
                baseSpeed = baseSpeed,
                socialDistance = socialDistance,
                toleranceDurationMs = toleranceDurationMs,
                attractionWeights = attractionWeights != null
                    ? attractionWeights.Clone()
                    : new CrowdTypeAttractionWeightsConfig(),
            };
        }

        private CrowdTypeAttractionWeightsConfig ResolveAttractionWeights()
        {
            return attractionWeights ?? new CrowdTypeAttractionWeightsConfig();
        }

        private static string NormalizeTypeId(string rawTypeId)
        {
            return string.IsNullOrWhiteSpace(rawTypeId)
                ? string.Empty
                : rawTypeId.Trim().ToLowerInvariant();
        }

        private static string NormalizeDisplayName(string rawDisplayName, string normalizedTypeId)
        {
            if (!string.IsNullOrWhiteSpace(rawDisplayName))
            {
                return rawDisplayName.Trim();
            }

            if (!string.IsNullOrWhiteSpace(normalizedTypeId))
            {
                return normalizedTypeId;
            }

            return "Unnamed Crowd Type";
        }

        private static string NormalizeColorHex(string rawColorHex)
        {
            var candidate = string.IsNullOrWhiteSpace(rawColorHex)
                ? string.Empty
                : rawColorHex.Trim();

            if (candidate.Length == 7
                && candidate[0] == '#'
                && IsHex(candidate[1])
                && IsHex(candidate[2])
                && IsHex(candidate[3])
                && IsHex(candidate[4])
                && IsHex(candidate[5])
                && IsHex(candidate[6]))
            {
                return candidate.ToUpperInvariant();
            }

            return "#336699";
        }

        private static bool IsHex(char value)
        {
            return (value >= '0' && value <= '9')
                || (value >= 'a' && value <= 'f')
                || (value >= 'A' && value <= 'F');
        }

        private static float NormalizeBaseSpeed(float value)
        {
            return Mathf.Clamp(value, MinBaseSpeed, MaxBaseSpeed);
        }

        private static float NormalizeSocialDistance(float value)
        {
            return Mathf.Clamp(value, MinSocialDistance, MaxSocialDistance);
        }

        private static int NormalizeToleranceDurationMs(int value)
        {
            return Mathf.Clamp(value, MinToleranceDurationMs, MaxToleranceDurationMs);
        }
    }
}
