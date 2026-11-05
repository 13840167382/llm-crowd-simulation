namespace CrowdSim.Data.Api
{
    public sealed class BackendApiRawResponse
    {
        public string Method;
        public string Path;
        public string Url;
        public string ExperimentId;
        public string RequestBody;
        public bool TransportSuccess;
        public long StatusCode;
        public string Error;
        public string Body;
        public string RequestId;
        public int Attempt;
        public long SentAtUnixMs;
        public long ReceivedAtUnixMs;
        public long DurationMs;

        public bool IsHttpSuccess
        {
            get { return StatusCode >= 200 && StatusCode < 300; }
        }

        public bool IsSuccess
        {
            get { return TransportSuccess && IsHttpSuccess; }
        }
    }
}
