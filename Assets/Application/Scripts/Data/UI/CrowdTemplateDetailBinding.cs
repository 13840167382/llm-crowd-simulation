using System;
using System.Globalization;
using CrowdSim.Data.CrowdTypes;
using TMPro;
using UnityEngine;

namespace CrowdSim.Data.UI
{
    [Serializable]
    public sealed class CrowdTemplateDetailBinding
    {
        private const float MinBaseSpeed = 0.1f;
        private const float MaxBaseSpeed = 5f;
        private const float MinSocialDistance = 0.3f;
        private const float MaxSocialDistance = 5f;
        private const int MinToleranceDurationMs = 1000;
        private const int MaxToleranceDurationMs = 300000;

        [Header("Identity")]
        [SerializeField] private TMP_Text typeIdTmpText;

        [Header("Editable Fields")]
        [SerializeField] private TMP_InputField displayNameInput;
        [SerializeField] private TMP_InputField colorHexInput;
        [SerializeField] private TMP_InputField baseSpeedInput;
        [SerializeField] private TMP_InputField socialDistanceInput;
        [SerializeField] private TMP_InputField toleranceDurationMsInput;

        [Header("Attraction Weights")]
        [SerializeField] private TMP_InputField exhibitWeightInput;
        [SerializeField] private TMP_InputField commercialWeightInput;
        [SerializeField] private TMP_InputField restWeightInput;
        [SerializeField] private TMP_InputField serviceWeightInput;
        [SerializeField] private TMP_InputField plazaWeightInput;
        [SerializeField] private TMP_InputField passageWeightInput;
        [SerializeField] private TMP_InputField exitFinalWeightInput;

        [Header("Hints")]
        [SerializeField] private TMP_Text editHintTmpText;

        public void PopulateFromTemplate(string fixedTypeId, CrowdTypeTemplateDto template)
        {
            var normalizedTypeId = NormalizeTypeId(fixedTypeId);
            var resolvedTemplate = template ?? new CrowdTypeTemplateDto();
            var resolvedWeights = resolvedTemplate.attraction_weights ?? CrowdTypeAttractionWeightsDto.CreateDefault();

            if (typeIdTmpText != null)
            {
                typeIdTmpText.text = normalizedTypeId;
            }

            SetInputValue(displayNameInput, ResolveDisplayName(resolvedTemplate.display_name, normalizedTypeId));
            SetInputValue(colorHexInput, NormalizeColorHex(resolvedTemplate.color_hex));
            SetInputValue(baseSpeedInput, FormatFloat(ClampBaseSpeed(resolvedTemplate.base_speed)));
            SetInputValue(socialDistanceInput, FormatFloat(ClampSocialDistance(resolvedTemplate.social_distance)));
            SetInputValue(toleranceDurationMsInput, ClampToleranceDurationMs(resolvedTemplate.tolerance_duration_ms).ToString(CultureInfo.InvariantCulture));

            SetInputValue(exhibitWeightInput, FormatFloat01(resolvedWeights.exhibit));
            SetInputValue(commercialWeightInput, FormatFloat01(resolvedWeights.commercial));
            SetInputValue(restWeightInput, FormatFloat01(resolvedWeights.rest));
            SetInputValue(serviceWeightInput, FormatFloat01(resolvedWeights.service));
            SetInputValue(plazaWeightInput, FormatFloat01(resolvedWeights.plaza));
            SetInputValue(passageWeightInput, FormatFloat01(resolvedWeights.passage));
            SetInputValue(exitFinalWeightInput, FormatFloat01(resolvedWeights.exit_final));
        }

        public void SetEditable(bool editable, bool isBuiltin)
        {
            SetInputInteractable(displayNameInput, editable);
            SetInputInteractable(colorHexInput, editable);
            SetInputInteractable(baseSpeedInput, editable);
            SetInputInteractable(socialDistanceInput, editable);
            SetInputInteractable(toleranceDurationMsInput, editable);
            SetInputInteractable(exhibitWeightInput, editable);
            SetInputInteractable(commercialWeightInput, editable);
            SetInputInteractable(restWeightInput, editable);
            SetInputInteractable(serviceWeightInput, editable);
            SetInputInteractable(plazaWeightInput, editable);
            SetInputInteractable(passageWeightInput, editable);
            SetInputInteractable(exitFinalWeightInput, editable);

            if (editHintTmpText != null)
            {
                if (editable)
                {
                    editHintTmpText.text = "可编辑：修改后点击上传。";
                }
                else if (isBuiltin)
                {
                    editHintTmpText.text = "只读：后端 builtin 模板不可在 UI 中修改。";
                }
                else
                {
                    editHintTmpText.text = "当前不可编辑。";
                }
            }
        }

        public bool TryBuildRequests(
            string fixedTypeId,
            out CrowdTypeCreateRequestDto createRequest,
            out CrowdTypeUpdateRequestDto updateRequest,
            out string error)
        {
            var normalizedTypeId = NormalizeTypeId(fixedTypeId);
            if (string.IsNullOrWhiteSpace(normalizedTypeId))
            {
                createRequest = null;
                updateRequest = null;
                error = "上传失败：模板 ID 为空。";
                return false;
            }

            var displayName = ResolveDisplayName(ReadInputValue(displayNameInput), normalizedTypeId);
            var colorHex = NormalizeColorHex(ReadInputValue(colorHexInput));
            if (!IsValidColorHex(colorHex))
            {
                createRequest = null;
                updateRequest = null;
                error = "上传失败：color hex 格式无效，需为 #RRGGBB。";
                return false;
            }

            float baseSpeed;
            if (!TryParseFloatInRange(baseSpeedInput, MinBaseSpeed, MaxBaseSpeed, 1f, out baseSpeed))
            {
                createRequest = null;
                updateRequest = null;
                error = "上传失败：base_speed 需为 0.1-5.0。";
                return false;
            }

            float socialDistance;
            if (!TryParseFloatInRange(socialDistanceInput, MinSocialDistance, MaxSocialDistance, 0.8f, out socialDistance))
            {
                createRequest = null;
                updateRequest = null;
                error = "上传失败：social_distance 需为 0.3-5.0。";
                return false;
            }

            int toleranceDurationMs;
            if (!TryParseIntInRange(toleranceDurationMsInput, MinToleranceDurationMs, MaxToleranceDurationMs, 30000, out toleranceDurationMs))
            {
                createRequest = null;
                updateRequest = null;
                error = "上传失败：tolerance_duration_ms 需为 1000-300000。";
                return false;
            }

            float exhibit;
            float commercial;
            float rest;
            float service;
            float plaza;
            float passage;
            float exitFinal;
            if (!TryParseFloatInRange(exhibitWeightInput, 0f, 1f, 0.4f, out exhibit)
                || !TryParseFloatInRange(commercialWeightInput, 0f, 1f, 0.4f, out commercial)
                || !TryParseFloatInRange(restWeightInput, 0f, 1f, 0.2f, out rest)
                || !TryParseFloatInRange(serviceWeightInput, 0f, 1f, 0.2f, out service)
                || !TryParseFloatInRange(plazaWeightInput, 0f, 1f, 0.4f, out plaza)
                || !TryParseFloatInRange(passageWeightInput, 0f, 1f, 0.5f, out passage)
                || !TryParseFloatInRange(exitFinalWeightInput, 0f, 1f, 0.1f, out exitFinal))
            {
                createRequest = null;
                updateRequest = null;
                error = "上传失败：attraction_weights 的每个值都需在 0-1。";
                return false;
            }

            var weights = new CrowdTypeAttractionWeightsDto
            {
                exhibit = exhibit,
                commercial = commercial,
                rest = rest,
                service = service,
                plaza = plaza,
                passage = passage,
                exit_final = exitFinal,
            };

            createRequest = new CrowdTypeCreateRequestDto
            {
                type_id = normalizedTypeId,
                display_name = displayName,
                color_hex = colorHex,
                base_speed = baseSpeed,
                social_distance = socialDistance,
                tolerance_duration_ms = toleranceDurationMs,
                attraction_weights = weights,
            };

            updateRequest = new CrowdTypeUpdateRequestDto
            {
                display_name = displayName,
                color_hex = colorHex,
                base_speed = baseSpeed,
                social_distance = socialDistance,
                tolerance_duration_ms = toleranceDurationMs,
                attraction_weights = weights.Clone(),
            };

            error = string.Empty;
            return true;
        }

        public void ClearView()
        {
            if (typeIdTmpText != null)
            {
                typeIdTmpText.text = string.Empty;
            }

            SetInputValue(displayNameInput, string.Empty);
            SetInputValue(colorHexInput, string.Empty);
            SetInputValue(baseSpeedInput, string.Empty);
            SetInputValue(socialDistanceInput, string.Empty);
            SetInputValue(toleranceDurationMsInput, string.Empty);
            SetInputValue(exhibitWeightInput, string.Empty);
            SetInputValue(commercialWeightInput, string.Empty);
            SetInputValue(restWeightInput, string.Empty);
            SetInputValue(serviceWeightInput, string.Empty);
            SetInputValue(plazaWeightInput, string.Empty);
            SetInputValue(passageWeightInput, string.Empty);
            SetInputValue(exitFinalWeightInput, string.Empty);

            if (editHintTmpText != null)
            {
                editHintTmpText.text = string.Empty;
            }
        }

        private static void SetInputInteractable(TMP_InputField input, bool interactable)
        {
            if (input != null)
            {
                input.interactable = interactable;
            }
        }

        private static void SetInputValue(TMP_InputField input, string value)
        {
            if (input != null)
            {
                input.text = value;
            }
        }

        private static string ReadInputValue(TMP_InputField input)
        {
            if (input == null || string.IsNullOrWhiteSpace(input.text))
            {
                return string.Empty;
            }

            return input.text.Trim();
        }

        private static string NormalizeTypeId(string rawTypeId)
        {
            return string.IsNullOrWhiteSpace(rawTypeId)
                ? string.Empty
                : rawTypeId.Trim().ToLowerInvariant();
        }

        private static string ResolveDisplayName(string rawDisplayName, string normalizedTypeId)
        {
            if (!string.IsNullOrWhiteSpace(rawDisplayName))
            {
                return rawDisplayName.Trim();
            }

            return string.IsNullOrWhiteSpace(normalizedTypeId)
                ? "Unnamed Crowd Type"
                : normalizedTypeId;
        }

        private static string NormalizeColorHex(string rawColorHex)
        {
            var candidate = string.IsNullOrWhiteSpace(rawColorHex)
                ? string.Empty
                : rawColorHex.Trim();
            if (!IsValidColorHex(candidate))
            {
                return "#336699";
            }

            return candidate.ToUpperInvariant();
        }

        private static bool IsValidColorHex(string value)
        {
            if (string.IsNullOrWhiteSpace(value) || value.Length != 7 || value[0] != '#')
            {
                return false;
            }

            for (var i = 1; i < value.Length; i++)
            {
                var c = value[i];
                var isHex = (c >= '0' && c <= '9')
                    || (c >= 'a' && c <= 'f')
                    || (c >= 'A' && c <= 'F');
                if (!isHex)
                {
                    return false;
                }
            }

            return true;
        }

        private static bool TryParseFloatInRange(
            TMP_InputField input,
            float min,
            float max,
            float fallback,
            out float value)
        {
            var raw = ReadInputValue(input);
            if (string.IsNullOrWhiteSpace(raw))
            {
                value = Mathf.Clamp(fallback, min, max);
                return true;
            }

            float parsed;
            if (!float.TryParse(raw, NumberStyles.Float, CultureInfo.InvariantCulture, out parsed)
                && !float.TryParse(raw, NumberStyles.Float, CultureInfo.CurrentCulture, out parsed))
            {
                value = fallback;
                return false;
            }

            if (parsed < min || parsed > max)
            {
                value = fallback;
                return false;
            }

            value = parsed;
            return true;
        }

        private static bool TryParseIntInRange(TMP_InputField input, int min, int max, int fallback, out int value)
        {
            var raw = ReadInputValue(input);
            if (string.IsNullOrWhiteSpace(raw))
            {
                value = Mathf.Clamp(fallback, min, max);
                return true;
            }

            int parsed;
            if (!int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out parsed)
                && !int.TryParse(raw, NumberStyles.Integer, CultureInfo.CurrentCulture, out parsed))
            {
                value = fallback;
                return false;
            }

            if (parsed < min || parsed > max)
            {
                value = fallback;
                return false;
            }

            value = parsed;
            return true;
        }

        private static float ClampBaseSpeed(float value)
        {
            return Mathf.Clamp(value <= 0f ? 1f : value, MinBaseSpeed, MaxBaseSpeed);
        }

        private static float ClampSocialDistance(float value)
        {
            return Mathf.Clamp(value <= 0f ? 0.8f : value, MinSocialDistance, MaxSocialDistance);
        }

        private static int ClampToleranceDurationMs(int value)
        {
            var fallback = value <= 0 ? 30000 : value;
            return Mathf.Clamp(fallback, MinToleranceDurationMs, MaxToleranceDurationMs);
        }

        private static string FormatFloat(float value)
        {
            return value.ToString("0.###", CultureInfo.InvariantCulture);
        }

        private static string FormatFloat01(float value)
        {
            return Mathf.Clamp01(value).ToString("0.###", CultureInfo.InvariantCulture);
        }
    }
}
