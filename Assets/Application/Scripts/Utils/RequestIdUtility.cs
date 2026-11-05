using System;

namespace CrowdSim.Utils
{
    public static class RequestIdUtility
    {
        public static string CreateRequestId(string prefix)
        {
            var safePrefix = string.IsNullOrWhiteSpace(prefix) ? "unity" : prefix.Trim();
            return safePrefix + "-" + Guid.NewGuid().ToString("N");
        }

        public static long GetUnixTimestampMs()
        {
            return DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        }
    }
}
