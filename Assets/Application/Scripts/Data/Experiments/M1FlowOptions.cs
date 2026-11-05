using System;
using UnityEngine;

namespace CrowdSim.Data.Experiments
{
    [Serializable]
    public class M1FlowOptions
    {
        public string preferredCrowdTypeId = "tourist";
        [Min(1)] public int crowdCount = 20;
        public string mapId = "unity_2d_map";
        public string[] experimentTags = new[] { "unity", "m1", "smoke" };

        public string zoneId = "entry_zone_01";
        public string zoneName = "Entry Zone";
        public float zoneMinX = -5f;
        public float zoneMinZ = -5f;
        public float zoneMaxX = 5f;
        public float zoneMaxZ = 5f;
    }
}
