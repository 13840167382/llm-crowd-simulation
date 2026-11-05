using System;
using UnityEngine;

namespace CrowdSim.Data.Allocation
{
    [Serializable]
    public class CrowdTypeAllocationConfig
    {
        public string typeId = "tourist";
        [Min(0)] public int count = 0;

        public bool TryGetNormalized(out string normalizedTypeId, out int normalizedCount)
        {
            normalizedTypeId = string.IsNullOrWhiteSpace(typeId) ? string.Empty : typeId.Trim();
            normalizedCount = Math.Max(0, count);
            return !string.IsNullOrWhiteSpace(normalizedTypeId) && normalizedCount > 0;
        }
    }
}
