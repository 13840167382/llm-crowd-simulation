using System;

namespace CrowdSim.Data.Common
{
    [Serializable]
    public class ApiResponseMeta
    {
        public int code;
        public string message;
        public string request_id;
        public long timestamp_ms;
    }
}
