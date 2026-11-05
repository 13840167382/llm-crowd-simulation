using System;
using System.Text;
using Cysharp.Threading.Tasks;
using CrowdSim.Data.Api;
using CrowdSim.Utils;
using UnityEngine;
using UnityEngine.Networking;

namespace CrowdSim.Core
{
    public sealed class BackendApiClient
    {
        private readonly BackendApiClientOptions _options;

        public BackendApiClient(BackendApiClientOptions options)
        {
            _options = options ?? new BackendApiClientOptions();
        }

        public UniTask<BackendApiRawResponse> Get(string path, string experimentId)
        {
            return SendWithRetry(UnityWebRequest.kHttpVerbGET, path, null, experimentId);
        }

        public UniTask<BackendApiRawResponse> PostJson(string path, string jsonBody, string experimentId)
        {
            return SendWithRetry(UnityWebRequest.kHttpVerbPOST, path, jsonBody, experimentId);
        }

        public UniTask<BackendApiRawResponse> PutJson(string path, string jsonBody, string experimentId)
        {
            return SendWithRetry(UnityWebRequest.kHttpVerbPUT, path, jsonBody, experimentId);
        }

        public UniTask<BackendApiRawResponse> Delete(string path, string experimentId)
        {
            return SendWithRetry(UnityWebRequest.kHttpVerbDELETE, path, null, experimentId);
        }

        private async UniTask<BackendApiRawResponse> SendWithRetry(
            string method,
            string path,
            string jsonBody,
            string experimentId)
        {
            var url = BuildUrl(path);
            var requestId = RequestIdUtility.CreateRequestId(_options.requestIdPrefix);
            var maxAttempts = Math.Max(1, _options.maxRetries + 1);
            var requestBody = string.IsNullOrEmpty(jsonBody) ? string.Empty : jsonBody;

            BackendApiRawResponse latestResponse = null;
            for (var attempt = 1; attempt <= maxAttempts; attempt++)
            {
                using (var request = BuildRequest(method, url, jsonBody))
                {
                    request.timeout = Math.Max(1, _options.timeoutSeconds);
                    request.SetRequestHeader("Accept", "application/json");
                    request.SetRequestHeader("X-Request-Id", requestId);
                    if (!string.IsNullOrWhiteSpace(experimentId))
                    {
                        request.SetRequestHeader("X-Experiment-Id", experimentId);
                    }

                    var sentAtUnixMs = RequestIdUtility.GetUnixTimestampMs();
                    await request.SendWebRequest();
                    var receivedAtUnixMs = RequestIdUtility.GetUnixTimestampMs();

                    latestResponse = BuildResponse(
                        request,
                        method,
                        path,
                        url,
                        experimentId,
                        requestBody,
                        requestId,
                        attempt,
                        sentAtUnixMs,
                        receivedAtUnixMs);
                }

                var canRetry = attempt < maxAttempts && ShouldRetry(latestResponse);
                if (!canRetry)
                {
                    break;
                }

                if (_options.retryDelaySeconds > 0f)
                {
                    await UniTask.Delay(TimeSpan.FromSeconds(_options.retryDelaySeconds));
                }
            }

            return latestResponse ?? new BackendApiRawResponse
            {
                Method = method,
                Path = path,
                Url = url,
                ExperimentId = experimentId,
                RequestBody = requestBody,
                TransportSuccess = false,
                StatusCode = 0,
                Error = "No response received.",
                Body = string.Empty,
                RequestId = requestId,
                Attempt = 0,
                SentAtUnixMs = 0,
                ReceivedAtUnixMs = 0,
                DurationMs = 0,
            };
        }

        private UnityWebRequest BuildRequest(string method, string url, string jsonBody)
        {
            if (string.Equals(method, UnityWebRequest.kHttpVerbGET, StringComparison.OrdinalIgnoreCase))
            {
                return UnityWebRequest.Get(url);
            }

            if (string.Equals(method, UnityWebRequest.kHttpVerbDELETE, StringComparison.OrdinalIgnoreCase))
            {
                return UnityWebRequest.Delete(url);
            }

            var request = new UnityWebRequest(url, method)
            {
                downloadHandler = new DownloadHandlerBuffer(),
            };

            var payload = string.IsNullOrEmpty(jsonBody) ? "{}" : jsonBody;
            request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(payload));
            request.SetRequestHeader("Content-Type", "application/json");
            return request;
        }

        private static BackendApiRawResponse BuildResponse(
            UnityWebRequest request,
            string method,
            string path,
            string url,
            string experimentId,
            string requestBody,
            string requestId,
            int attempt,
            long sentAtUnixMs,
            long receivedAtUnixMs)
        {
            var transportSuccess = request.result != UnityWebRequest.Result.ConnectionError
                && request.result != UnityWebRequest.Result.DataProcessingError;

            return new BackendApiRawResponse
            {
                Method = method,
                Path = path,
                Url = url,
                ExperimentId = experimentId,
                RequestBody = requestBody,
                TransportSuccess = transportSuccess,
                StatusCode = request.responseCode,
                Error = request.error,
                Body = request.downloadHandler != null ? request.downloadHandler.text : string.Empty,
                RequestId = requestId,
                Attempt = attempt,
                SentAtUnixMs = sentAtUnixMs,
                ReceivedAtUnixMs = receivedAtUnixMs,
                DurationMs = Math.Max(0, receivedAtUnixMs - sentAtUnixMs),
            };
        }

        private static bool ShouldRetry(BackendApiRawResponse response)
        {
            if (response == null)
            {
                return true;
            }

            if (!response.TransportSuccess)
            {
                return true;
            }

            return response.StatusCode >= 500;
        }

        private string BuildUrl(string path)
        {
            var baseUrl = (_options.baseUrl ?? string.Empty).Trim().TrimEnd('/');
            var suffix = string.IsNullOrWhiteSpace(path)
                ? string.Empty
                : (path.StartsWith("/") ? path : "/" + path);
            return baseUrl + suffix;
        }
    }
}
