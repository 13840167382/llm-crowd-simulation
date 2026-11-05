using System;
using UnityEngine;

namespace CrowdSim.Data.Api
{
    [Serializable]
    public class BackendApiClientOptions
    {
        public string baseUrl = "http://127.0.0.1:8987";
        public int timeoutSeconds = 60;
        [Range(0, 5)] public int maxRetries = 1;
        [Min(0f)] public float retryDelaySeconds = 0.25f;
        public string requestIdPrefix = "unity";
    }
}
