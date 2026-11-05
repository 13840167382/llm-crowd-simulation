using System;

namespace CrowdSim.Data.Debug
{
    public enum M1FlowRunStatus
    {
        Running,
        Succeeded,
        Failed,
    }

    public enum M1FlowStepStatus
    {
        Running,
        Succeeded,
        Failed,
    }

    [Serializable]
    public sealed class M1FlowRunDebugInfo
    {
        public string RunId;
        public string Title;
        public M1FlowRunStatus Status;
        public string Summary;
        public long StartedAtUnixMs;
        public long FinishedAtUnixMs;
        public long DurationMs;

        public M1FlowRunDebugInfo Clone()
        {
            return new M1FlowRunDebugInfo
            {
                RunId = RunId,
                Title = Title,
                Status = Status,
                Summary = Summary,
                StartedAtUnixMs = StartedAtUnixMs,
                FinishedAtUnixMs = FinishedAtUnixMs,
                DurationMs = DurationMs,
            };
        }
    }

    [Serializable]
    public sealed class M1FlowStepDebugInfo
    {
        public string RunId;
        public int StepOrder;
        public string StepName;
        public M1FlowStepStatus Status;

        public string Method;
        public string Path;
        public string Url;
        public string RequestBody;
        public string RequestId;
        public string ExperimentId;

        public long HttpStatusCode;
        public bool TransportSuccess;
        public int Attempt;
        public int ApiCode;
        public string ApiMessage;
        public string TransportError;
        public string ResponseBody;
        public string Note;

        public long StartedAtUnixMs;
        public long FinishedAtUnixMs;
        public long DurationMs;

        public M1FlowStepDebugInfo Clone()
        {
            return new M1FlowStepDebugInfo
            {
                RunId = RunId,
                StepOrder = StepOrder,
                StepName = StepName,
                Status = Status,
                Method = Method,
                Path = Path,
                Url = Url,
                RequestBody = RequestBody,
                RequestId = RequestId,
                ExperimentId = ExperimentId,
                HttpStatusCode = HttpStatusCode,
                TransportSuccess = TransportSuccess,
                Attempt = Attempt,
                ApiCode = ApiCode,
                ApiMessage = ApiMessage,
                TransportError = TransportError,
                ResponseBody = ResponseBody,
                Note = Note,
                StartedAtUnixMs = StartedAtUnixMs,
                FinishedAtUnixMs = FinishedAtUnixMs,
                DurationMs = DurationMs,
            };
        }
    }
}
