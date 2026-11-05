using System;

namespace CrowdSim.Data.CrowdTypes
{
    [Serializable]
    public class CrowdTypeAttractionWeightsDto
    {
        public float exhibit;
        public float commercial;
        public float rest;
        public float service;
        public float plaza;
        public float passage;
        public float exit_final;

        public static CrowdTypeAttractionWeightsDto CreateDefault()
        {
            return new CrowdTypeAttractionWeightsDto
            {
                exhibit = 0.4f,
                commercial = 0.4f,
                rest = 0.2f,
                service = 0.2f,
                plaza = 0.4f,
                passage = 0.5f,
                exit_final = 0.1f,
            };
        }

        public CrowdTypeAttractionWeightsDto Clone()
        {
            return new CrowdTypeAttractionWeightsDto
            {
                exhibit = exhibit,
                commercial = commercial,
                rest = rest,
                service = service,
                plaza = plaza,
                passage = passage,
                exit_final = exit_final,
            };
        }

        public static bool ApproximatelyEquals(
            CrowdTypeAttractionWeightsDto left,
            CrowdTypeAttractionWeightsDto right,
            float epsilon = 0.0001f)
        {
            if (ReferenceEquals(left, right))
            {
                return true;
            }

            var normalizedLeft = left ?? CreateDefault();
            var normalizedRight = right ?? CreateDefault();

            return Math.Abs(normalizedLeft.exhibit - normalizedRight.exhibit) <= epsilon
                && Math.Abs(normalizedLeft.commercial - normalizedRight.commercial) <= epsilon
                && Math.Abs(normalizedLeft.rest - normalizedRight.rest) <= epsilon
                && Math.Abs(normalizedLeft.service - normalizedRight.service) <= epsilon
                && Math.Abs(normalizedLeft.plaza - normalizedRight.plaza) <= epsilon
                && Math.Abs(normalizedLeft.passage - normalizedRight.passage) <= epsilon
                && Math.Abs(normalizedLeft.exit_final - normalizedRight.exit_final) <= epsilon;
        }
    }

    [Serializable]
    public class CrowdTypeTemplateDto
    {
        public string type_id;
        public string display_name;
        public string color_hex;
        public float base_speed;
        public float social_distance;
        public int tolerance_duration_ms;
        public CrowdTypeAttractionWeightsDto attraction_weights;
        public bool is_builtin;
        public long created_at_ms;
        public long updated_at_ms;
    }

    [Serializable]
    public class CrowdTypeCreateRequestDto
    {
        public string type_id;
        public string display_name;
        public string color_hex;
        public float base_speed;
        public float social_distance;
        public int tolerance_duration_ms;
        public CrowdTypeAttractionWeightsDto attraction_weights;
    }

    [Serializable]
    public class CrowdTypeUpdateRequestDto
    {
        public string display_name;
        public string color_hex;
        public float base_speed;
        public float social_distance;
        public int tolerance_duration_ms;
        public CrowdTypeAttractionWeightsDto attraction_weights;
    }

    [Serializable]
    public class CrowdTypesListDataDto
    {
        public int total_count;
        public CrowdTypeTemplateDto[] templates;
        public CrowdTypeTemplateDto[] items;
    }

    [Serializable]
    public class CrowdTypesListResponseDto
    {
        public int code;
        public string message;
        public CrowdTypesListDataDto data;
        public string request_id;
        public long timestamp_ms;
    }

    [Serializable]
    public class CrowdTypeTemplateResponseDto
    {
        public int code;
        public string message;
        public CrowdTypeTemplateDto data;
        public string request_id;
        public long timestamp_ms;
    }

    [Serializable]
    public class CrowdTypeDeleteDataDto
    {
        public string type_id;
        public bool deleted;
    }

    [Serializable]
    public class CrowdTypeDeleteResponseDto
    {
        public int code;
        public string message;
        public CrowdTypeDeleteDataDto data;
        public string request_id;
        public long timestamp_ms;
    }

    public static class CrowdTypeDataExtensions
    {
        public static CrowdTypeTemplateDto[] GetTemplateList(this CrowdTypesListDataDto data)
        {
            if (data == null)
            {
                return null;
            }

            if (data.templates != null && data.templates.Length > 0)
            {
                return data.templates;
            }

            return data.items;
        }
    }
}
