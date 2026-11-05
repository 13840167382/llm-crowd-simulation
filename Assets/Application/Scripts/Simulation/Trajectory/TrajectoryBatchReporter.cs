using System;
using System.Collections.Generic;
using Cysharp.Threading.Tasks;
using CrowdSim.Core;
using CrowdSim.Data.Api;
using CrowdSim.Data.Common;
using CrowdSim.Data.Trajectories;
using CrowdSim.Simulation.Decision;
using CrowdSim.Utils;
using UnityEngine;

namespace CrowdSim.Simulation.Trajectory
{
    public sealed class TrajectoryBatchReporter : MonoBehaviour
    {
        [Header("Dependencies")]
        [SerializeField] private ExperimentManager experimentManager;

        [Header("Agent Discovery")]
        [SerializeField] private bool autoCollectFromScene = true;
        [SerializeField] private bool includeInactiveAgents;
        [SerializeField] private DecisionAgentPoint[] agentPoints = Array.Empty<DecisionAgentPoint>();

        [Header("Reporting")]
        [SerializeField] private bool reportingEnabledOnStart = true;
        [SerializeField, Min(0.1f)] private float reportIntervalSeconds = 2f;
        [SerializeField, Min(1)] private int sampleIntervalMs = 2000;
        [SerializeField, Min(1)] private int maxPointsPerBatch = 2000;
        [SerializeField] private bool onlyWhenExperimentRunning = true;
        [SerializeField] private string explicitExperimentId = string.Empty;
        [SerializeField] private bool logToConsole = true;

        [Header("Runtime")]
        [SerializeField] private bool reportingEnabled;
        [SerializeField] private bool requestInFlight;
        [SerializeField] private string lastBatchId = string.Empty;
        [SerializeField] private int lastSampledPointCount;
        [SerializeField] private int lastAcceptedCount;
        [SerializeField] private int lastRejectedCount;
        [SerializeField] private int lastTotalStoredPoints;
        [SerializeField] private long lastReportAtUnixMs;
        [SerializeField] private string lastReportSummary = "Not started";

        private BackendApiClient apiClient;
        private int batchSequence;
        private float nextReportAtTime;
        private readonly List<TrajectoryPointIngestItemDto> pointsScratch = new List<TrajectoryPointIngestItemDto>(1024);
        private readonly HashSet<int> sampledInstanceIdsScratch = new HashSet<int>();

        private void Awake()
        {
            apiClient = new BackendApiClient(ExperimentManager.SharedBackendOptions);
            ResolveExperimentManager();
        }

        private void OnEnable()
        {
            if (!reportingEnabledOnStart)
            {
                return;
            }

            StartReporting();
        }

        private void OnDisable()
        {
            reportingEnabled = false;
            requestInFlight = false;
        }

        private void Update()
        {
            if (!Application.isPlaying || !reportingEnabled || requestInFlight)
            {
                return;
            }

            if (Time.unscaledTime < nextReportAtTime)
            {
                return;
            }

            ReportNowAsync().Forget();
        }

        [ContextMenu("Start Trajectory Reporting")]
        public void StartReporting()
        {
            reportingEnabled = true;
            nextReportAtTime = Time.unscaledTime;
        }

        [ContextMenu("Stop Trajectory Reporting")]
        public void StopReporting()
        {
            reportingEnabled = false;
        }

        [ContextMenu("Reset Runtime State")]
        public void ResetRuntimeState()
        {
            StopReporting();
            requestInFlight = false;
            batchSequence = 0;
            lastBatchId = string.Empty;
            lastSampledPointCount = 0;
            lastAcceptedCount = 0;
            lastRejectedCount = 0;
            lastTotalStoredPoints = 0;
            lastReportAtUnixMs = 0;
            lastReportSummary = "Not started";
            pointsScratch.Clear();
            sampledInstanceIdsScratch.Clear();
            Debug.Log("[TrajectoryBatchReporter] Runtime state reset.", this);
        }

        [ContextMenu("Report Trajectory Batch Now")]
        public void ReportNow()
        {
            ReportNowAsync().Forget();
        }

        public async UniTask ReportNowAsync()
        {
            if (requestInFlight)
            {
                return;
            }

            requestInFlight = true;
            try
            {
                var experimentId = ResolveExperimentId();
                if (string.IsNullOrWhiteSpace(experimentId))
                {
                    lastReportSummary = "Trajectory report skipped: experiment id is empty.";
                    LogSummary(false);
                    return;
                }

                CollectTrajectoryPoints(pointsScratch);
                var maxPoints = Math.Max(1, maxPointsPerBatch);
                if (pointsScratch.Count > maxPoints)
                {
                    pointsScratch.RemoveRange(maxPoints, pointsScratch.Count - maxPoints);
                }

                if (pointsScratch.Count <= 0)
                {
                    lastSampledPointCount = 0;
                    lastReportSummary = "Trajectory report skipped: no trajectory points sampled.";
                    LogSummary();
                    return;
                }

                var request = new TrajectoryBatchRequestDto
                {
                    batch_id = BuildBatchId(),
                    simulation_time_ms = Mathf.Max(0, Mathf.RoundToInt(Time.timeSinceLevelLoad * 1000f)),
                    sample_interval_ms = ResolveSampleIntervalMs(),
                    points = pointsScratch.ToArray(),
                };

                lastSampledPointCount = request.points.Length;
                var path = "/api/v1/experiments/" + experimentId + "/trajectories";
                var requestJson = JsonUtility.ToJson(request);
                var raw = await apiClient.PostJson(path, requestJson, experimentId);

                ApiResponseMeta meta;
                string error;
                if (!TryEnsureApiSuccess(raw, "POST /api/v1/experiments/{id}/trajectories", out meta, out error))
                {
                    lastReportSummary = error;
                    LogSummary(true);
                    return;
                }

                var response = JsonUtility.FromJson<TrajectoryBatchResponseDto>(raw.Body);
                var data = response != null ? response.data : null;

                lastBatchId = data != null && !string.IsNullOrWhiteSpace(data.batch_id)
                    ? data.batch_id
                    : request.batch_id;
                lastAcceptedCount = data != null ? Math.Max(0, data.accepted_count) : request.points.Length;
                lastRejectedCount = data != null ? Math.Max(0, data.rejected_count) : 0;
                lastTotalStoredPoints = data != null ? Math.Max(0, data.total_stored_points) : 0;
                lastReportAtUnixMs = RequestIdUtility.GetUnixTimestampMs();
                lastReportSummary = "Trajectory batch reported. batch_id=" + lastBatchId
                    + ", sampled=" + lastSampledPointCount
                    + ", accepted=" + lastAcceptedCount
                    + ", rejected=" + lastRejectedCount
                    + ", total_stored=" + lastTotalStoredPoints;
                LogSummary();
            }
            catch (Exception exception)
            {
                lastReportSummary = "Trajectory batch exception: " + exception.Message;
                LogSummary(true);
                if (logToConsole)
                {
                    Debug.LogException(exception, this);
                }
            }
            finally
            {
                requestInFlight = false;
                nextReportAtTime = Time.unscaledTime + Mathf.Max(1f, reportIntervalSeconds);
                pointsScratch.Clear();
                sampledInstanceIdsScratch.Clear();
            }
        }

        private void CollectTrajectoryPoints(List<TrajectoryPointIngestItemDto> sampledPoints)
        {
            sampledPoints.Clear();
            sampledInstanceIdsScratch.Clear();

            if (agentPoints != null)
            {
                for (var i = 0; i < agentPoints.Length; i++)
                {
                    TryCollectPoint(agentPoints[i], sampledPoints);
                }
            }

            if (!autoCollectFromScene)
            {
                return;
            }

            var sceneAgents = FindObjectsOfType<DecisionAgentPoint>(includeInactiveAgents);
            if (sceneAgents == null || sceneAgents.Length <= 0)
            {
                return;
            }

            for (var i = 0; i < sceneAgents.Length; i++)
            {
                TryCollectPoint(sceneAgents[i], sampledPoints);
            }
        }

        private void TryCollectPoint(DecisionAgentPoint point, List<TrajectoryPointIngestItemDto> sampledPoints)
        {
            if (point == null || sampledPoints == null)
            {
                return;
            }

            if (!includeInactiveAgents && !point.isActiveAndEnabled)
            {
                return;
            }

            if (!point.HasReceivedFirstDecision)
            {
                return;
            }

            if (!sampledInstanceIdsScratch.Add(point.GetInstanceID()))
            {
                return;
            }

            TrajectoryPointIngestItemDto sample;
            if (!point.TryBuildTrajectoryPoint(out sample) || sample == null)
            {
                return;
            }

            SanitizeTrajectoryPoint(sample);
            if (string.IsNullOrWhiteSpace(sample.agent_id)
                || string.IsNullOrWhiteSpace(sample.crowd_type)
                || string.IsNullOrWhiteSpace(sample.zone_id)
                || string.IsNullOrWhiteSpace(sample.state))
            {
                return;
            }

            sampledPoints.Add(sample);
        }

        private static void SanitizeTrajectoryPoint(TrajectoryPointIngestItemDto sample)
        {
            if (sample == null)
            {
                return;
            }

            sample.agent_id = NormalizeRequiredValue(sample.agent_id);
            sample.crowd_type = NormalizeRequiredValue(sample.crowd_type);
            sample.zone_id = NormalizeRequiredValue(sample.zone_id);
            sample.state = NormalizeRequiredValue(sample.state);
            sample.target_exit_id = NormalizeOptionalValue(sample.target_exit_id);
        }

        private static string NormalizeRequiredValue(string value)
        {
            return string.IsNullOrWhiteSpace(value) ? string.Empty : value.Trim();
        }

        private static string NormalizeOptionalValue(string value)
        {
            return string.IsNullOrWhiteSpace(value) ? null : value.Trim();
        }

        private string ResolveExperimentId()
        {
            if (!string.IsNullOrWhiteSpace(explicitExperimentId))
            {
                return explicitExperimentId.Trim();
            }

            var manager = ResolveExperimentManager();
            if (manager == null)
            {
                return string.Empty;
            }

            var runningId = string.IsNullOrWhiteSpace(manager.RunningExperimentId)
                ? string.Empty
                : manager.RunningExperimentId.Trim();
            if (!string.IsNullOrWhiteSpace(runningId))
            {
                return runningId;
            }

            if (onlyWhenExperimentRunning)
            {
                return string.Empty;
            }

            return string.IsNullOrWhiteSpace(manager.LastCreatedExperimentId)
                ? string.Empty
                : manager.LastCreatedExperimentId.Trim();
        }

        private ExperimentManager ResolveExperimentManager()
        {
            if (experimentManager != null)
            {
                return experimentManager;
            }

            experimentManager = ExperimentManager.ActiveInstance;
            if (experimentManager != null)
            {
                return experimentManager;
            }

            experimentManager = FindObjectOfType<ExperimentManager>();
            return experimentManager;
        }

        private int ResolveSampleIntervalMs()
        {
            if (sampleIntervalMs > 0)
            {
                return sampleIntervalMs;
            }

            return Math.Max(1, Mathf.RoundToInt(Mathf.Max(1f, reportIntervalSeconds) * 1000f));
        }

        private string BuildBatchId()
        {
            batchSequence++;
            return "unity_trajectory_" + RequestIdUtility.GetUnixTimestampMs() + "_" + batchSequence;
        }

        private void LogSummary(bool warn = false)
        {
            if (!logToConsole)
            {
                return;
            }

            if (warn)
            {
                Debug.LogWarning(lastReportSummary, this);
                return;
            }

            Debug.Log(lastReportSummary, this);
        }

        private static bool TryEnsureApiSuccess(
            BackendApiRawResponse raw,
            string operation,
            out ApiResponseMeta meta,
            out string error)
        {
            meta = null;

            if (raw == null)
            {
                error = operation + " failed: empty response.";
                return false;
            }

            if (!raw.TransportSuccess)
            {
                error = operation + " transport error: " + raw.Error;
                return false;
            }

            if (raw.StatusCode < 200 || raw.StatusCode >= 300)
            {
                var detail = string.IsNullOrWhiteSpace(raw.Body)
                    ? raw.Error
                    : raw.Body;
                error = operation + " failed with HTTP " + raw.StatusCode + ". " + detail;
                return false;
            }

            if (string.IsNullOrWhiteSpace(raw.Body))
            {
                error = operation + " failed: response body is empty.";
                return false;
            }

            meta = JsonUtility.FromJson<ApiResponseMeta>(raw.Body);
            if (meta == null)
            {
                error = operation + " failed: cannot parse response meta.";
                return false;
            }

            if (meta.code != 0)
            {
                error = operation + " failed: code=" + meta.code + ", message=" + meta.message;
                return false;
            }

            error = string.Empty;
            return true;
        }
    }
}
