using System;
using System.Collections.Generic;
using CrowdSim.Data.Enums;

namespace CrowdSim.Data.Experiments
{
    [Serializable]
    public sealed class ExperimentOperationResult
    {
        public bool Success;
        public string Message;
        public string ExperimentId;
    }

    [Serializable]
    public sealed class ExperimentApiCallCacheRecord
    {
        public int Sequence;
        public string OperationName;
        public string Method;
        public string Path;
        public string Url;
        public string RequestId;
        public string ExperimentId;

        public string RequestBody;
        public string ResponseBody;
        public string TransportError;

        public long HttpStatusCode;
        public bool TransportSuccess;
        public int Attempt;

        public int ApiCode;
        public string ApiMessage;

        public bool Success;
        public string Note;

        public long StartedAtUnixMs;
        public long FinishedAtUnixMs;
        public long DurationMs;

        public ExperimentApiCallCacheRecord Clone()
        {
            return new ExperimentApiCallCacheRecord
            {
                Sequence = Sequence,
                OperationName = OperationName,
                Method = Method,
                Path = Path,
                Url = Url,
                RequestId = RequestId,
                ExperimentId = ExperimentId,
                RequestBody = RequestBody,
                ResponseBody = ResponseBody,
                TransportError = TransportError,
                HttpStatusCode = HttpStatusCode,
                TransportSuccess = TransportSuccess,
                Attempt = Attempt,
                ApiCode = ApiCode,
                ApiMessage = ApiMessage,
                Success = Success,
                Note = Note,
                StartedAtUnixMs = StartedAtUnixMs,
                FinishedAtUnixMs = FinishedAtUnixMs,
                DurationMs = DurationMs,
            };
        }
    }

    [Serializable]
    public sealed class ExperimentCacheRecord
    {
        public string ExperimentId;
        public string ExperimentName;
        public string SelectedCrowdTypeId;

        public int LastSpawnSeed;
        public int LastSpawnTotalCount;
        public int LastSpawnInstantiatedCount;
        public long LastSpawnAtUnixMs;

        public bool HasStatus;
        public ExperimentStatus Status;

        public string LastOperationName;
        public bool LastOperationSuccess;
        public string LastMessage;
        public string LastRequestId;

        public long CreatedAtUnixMs;
        public long LastUpdatedAtUnixMs;

        public List<ExperimentApiCallCacheRecord> Calls = new List<ExperimentApiCallCacheRecord>();

        public ExperimentCacheRecord Clone()
        {
            var clone = new ExperimentCacheRecord
            {
                ExperimentId = ExperimentId,
                ExperimentName = ExperimentName,
                SelectedCrowdTypeId = SelectedCrowdTypeId,
                LastSpawnSeed = LastSpawnSeed,
                LastSpawnTotalCount = LastSpawnTotalCount,
                LastSpawnInstantiatedCount = LastSpawnInstantiatedCount,
                LastSpawnAtUnixMs = LastSpawnAtUnixMs,
                HasStatus = HasStatus,
                Status = Status,
                LastOperationName = LastOperationName,
                LastOperationSuccess = LastOperationSuccess,
                LastMessage = LastMessage,
                LastRequestId = LastRequestId,
                CreatedAtUnixMs = CreatedAtUnixMs,
                LastUpdatedAtUnixMs = LastUpdatedAtUnixMs,
                Calls = new List<ExperimentApiCallCacheRecord>(),
            };

            if (Calls != null)
            {
                for (var i = 0; i < Calls.Count; i++)
                {
                    clone.Calls.Add(Calls[i] != null ? Calls[i].Clone() : null);
                }
            }

            return clone;
        }
    }
}
