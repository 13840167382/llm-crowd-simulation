using System;
using UnityEngine;

namespace CrowdSim.Data.CrowdTypes
{
    [Serializable]
    public class CrowdTypeAttractionWeightsConfig
    {
        [Range(0f, 1f)] public float exhibit = 0.4f;
        [Range(0f, 1f)] public float commercial = 0.4f;
        [Range(0f, 1f)] public float rest = 0.2f;
        [Range(0f, 1f)] public float service = 0.2f;
        [Range(0f, 1f)] public float plaza = 0.4f;
        [Range(0f, 1f)] public float passage = 0.5f;
        [Range(0f, 1f)] public float exitFinal = 0.1f;

        public CrowdTypeAttractionWeightsDto BuildDto()
        {
            return new CrowdTypeAttractionWeightsDto
            {
                exhibit = Mathf.Clamp01(exhibit),
                commercial = Mathf.Clamp01(commercial),
                rest = Mathf.Clamp01(rest),
                service = Mathf.Clamp01(service),
                plaza = Mathf.Clamp01(plaza),
                passage = Mathf.Clamp01(passage),
                exit_final = Mathf.Clamp01(exitFinal),
            };
        }

        public CrowdTypeAttractionWeightsConfig Clone()
        {
            return new CrowdTypeAttractionWeightsConfig
            {
                exhibit = exhibit,
                commercial = commercial,
                rest = rest,
                service = service,
                plaza = plaza,
                passage = passage,
                exitFinal = exitFinal,
            };
        }
    }
}
