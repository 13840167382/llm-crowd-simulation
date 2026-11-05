using System;
using Cysharp.Threading.Tasks;
using CrowdSim.Data.Allocation;
using CrowdSim.Data.Api;
using CrowdSim.Data.Common;
using CrowdSim.Data.CrowdTypes;
using CrowdSim.Data.Debug;
using CrowdSim.Data.Enums;
using CrowdSim.Data.Experiments;
using CrowdSim.Data.Topology;
using CrowdSim.Utils;
using UnityEngine;

namespace CrowdSim.Core
{
    public sealed class M1FlowExecutionResult
    {
        public bool Success;
        public string Message;
        public string ExperimentId;
        public string SelectedCrowdTypeId;
    }

    public sealed class M1MinimalFlowService
    {
        private readonly BackendApiClient _apiClient;
        private readonly M1FlowOptions _options;

        public M1MinimalFlowService(BackendApiClient apiClient, M1FlowOptions options)
        {
            _apiClient = apiClient;
            _options = options ?? new M1FlowOptions();
        }

        public async UniTask<M1FlowExecutionResult> RunAsync()
        {
            var result = new M1FlowExecutionResult();
            var runId = RequestIdUtility.CreateRequestId("m1-run");
            M1FlowDebugTraceStore.StartRun(runId, "M1 Minimal Flow");

            try
            {
                const string crowdTypesPath = "/api/v1/crowd-types";
                var crowdTypesStep = BeginStep(
                    runId,
                    1,
                    "Fetch Crowd Types",
                    "GET",
                    crowdTypesPath,
                    string.Empty,
                    string.Empty);

                var crowdTypesRaw = await _apiClient.Get(crowdTypesPath, null);

                if (!TryEnsureApiSuccess(crowdTypesRaw, "GET /api/v1/crowd-types", out var crowdTypesMeta, out var crowdTypesError))
                {
                    FinishStep(crowdTypesStep, crowdTypesRaw, crowdTypesMeta, false, crowdTypesError);
                    CompleteFailure(result, crowdTypesError, runId);
                    return result;
                }

                var crowdTypesResponse = JsonUtility.FromJson<CrowdTypesListResponseDto>(crowdTypesRaw.Body);
                var templates = crowdTypesResponse != null && crowdTypesResponse.data != null
                    ? crowdTypesResponse.data.GetTemplateList()
                    : null;
                if (templates == null || templates.Length == 0)
                {
                    const string message = "Crowd type templates are empty.";
                    FinishStep(crowdTypesStep, crowdTypesRaw, crowdTypesMeta, false, message);
                    CompleteFailure(result, message, runId);
                    return result;
                }

                var selectedTypeId = SelectCrowdTypeId(templates, _options.preferredCrowdTypeId);
                if (string.IsNullOrWhiteSpace(selectedTypeId))
                {
                    const string message = "Cannot resolve a valid crowd type id.";
                    FinishStep(crowdTypesStep, crowdTypesRaw, crowdTypesMeta, false, message);
                    CompleteFailure(result, message, runId);
                    return result;
                }

                FinishStep(crowdTypesStep, crowdTypesRaw, crowdTypesMeta, true, "Selected crowd type: " + selectedTypeId);

                var createRequest = BuildCreateRequest();
                var createRequestJson = JsonUtility.ToJson(createRequest);
                const string createPath = "/api/v1/experiments";
                var createStep = BeginStep(
                    runId,
                    2,
                    "Create Experiment",
                    "POST",
                    createPath,
                    createRequestJson,
                    string.Empty);

                var createRaw = await _apiClient.PostJson(createPath, createRequestJson, null);

                if (!TryEnsureApiSuccess(createRaw, "POST /api/v1/experiments", out var createMeta, out var createError))
                {
                    FinishStep(createStep, createRaw, createMeta, false, createError);
                    CompleteFailure(result, createError, runId);
                    return result;
                }

                var createResponse = JsonUtility.FromJson<ExperimentCreatedResponseDto>(createRaw.Body);
                if (createResponse == null || createResponse.data == null || string.IsNullOrWhiteSpace(createResponse.data.experiment_id))
                {
                    const string message = "Experiment create response is invalid.";
                    FinishStep(createStep, createRaw, createMeta, false, message);
                    CompleteFailure(result, message, runId);
                    return result;
                }

                var experimentId = createResponse.data.experiment_id;
                if (!ExperimentStatusMapper.TryParse(createResponse.data.status, out var createStatus)
                    || createStatus != ExperimentStatus.Initialized)
                {
                    var message = "Unexpected create status: " + createResponse.data.status;
                    FinishStep(createStep, createRaw, createMeta, false, message);
                    CompleteFailure(result, message, runId);
                    return result;
                }

                FinishStep(createStep, createRaw, createMeta, true, "Created experiment: " + experimentId);

                var topologyRequest = BuildMinimalTopologyRequest();
                var topologyRequestJson = JsonUtility.ToJson(topologyRequest);
                var topologyPath = "/api/v1/experiments/" + experimentId + "/topology";
                var topologyStep = BeginStep(
                    runId,
                    3,
                    "Put Topology",
                    "PUT",
                    topologyPath,
                    topologyRequestJson,
                    experimentId);

                var topologyRaw = await _apiClient.PutJson(topologyPath, topologyRequestJson, experimentId);

                if (!TryEnsureApiSuccess(topologyRaw, "PUT /api/v1/experiments/{id}/topology", out var topologyMeta, out var topologyError))
                {
                    FinishStep(topologyStep, topologyRaw, topologyMeta, false, topologyError);
                    CompleteFailure(result, topologyError, runId);
                    return result;
                }

                var topologyResponse = JsonUtility.FromJson<TopologyUpsertResponseDto>(topologyRaw.Body);
                if (topologyResponse == null || topologyResponse.data == null || topologyResponse.data.zone_count <= 0)
                {
                    const string message = "Topology upsert response is invalid.";
                    FinishStep(topologyStep, topologyRaw, topologyMeta, false, message);
                    CompleteFailure(result, message, runId);
                    return result;
                }

                FinishStep(topologyStep, topologyRaw, topologyMeta, true, "Zones: " + topologyResponse.data.zone_count);

                var allocationRequest = BuildMinimalAllocationRequest(selectedTypeId, experimentId);
                var allocationRequestJson = JsonUtility.ToJson(allocationRequest);
                var allocationPath = "/api/v1/experiments/" + experimentId + "/crowd-allocation";
                var allocationStep = BeginStep(
                    runId,
                    4,
                    "Put Crowd Allocation",
                    "PUT",
                    allocationPath,
                    allocationRequestJson,
                    experimentId);

                var allocationRaw = await _apiClient.PutJson(allocationPath, allocationRequestJson, experimentId);

                if (!TryEnsureApiSuccess(allocationRaw, "PUT /api/v1/experiments/{id}/crowd-allocation", out var allocationMeta, out var allocationError))
                {
                    FinishStep(allocationStep, allocationRaw, allocationMeta, false, allocationError);
                    CompleteFailure(result, allocationError, runId);
                    return result;
                }

                var allocationResponse = JsonUtility.FromJson<CrowdAllocationResponseDto>(allocationRaw.Body);
                if (allocationResponse == null || allocationResponse.data == null)
                {
                    const string message = "Crowd allocation response is invalid.";
                    FinishStep(allocationStep, allocationRaw, allocationMeta, false, message);
                    CompleteFailure(result, message, runId);
                    return result;
                }

                if (!ExperimentStatusMapper.TryParse(allocationResponse.data.status, out var allocationStatus)
                    || allocationStatus != ExperimentStatus.Configured)
                {
                    var message = "Unexpected allocation status: " + allocationResponse.data.status;
                    FinishStep(allocationStep, allocationRaw, allocationMeta, false, message);
                    CompleteFailure(result, message, runId);
                    return result;
                }

                FinishStep(allocationStep, allocationRaw, allocationMeta, true, "Allocation total_count: " + allocationResponse.data.total_count);

                var startRequest = new ExperimentStartRequestDto
                {
                    actual_start_time_ms = RequestIdUtility.GetUnixTimestampMs(),
                };
                var startRequestJson = JsonUtility.ToJson(startRequest);
                var startPath = "/api/v1/experiments/" + experimentId + "/start";
                var startStep = BeginStep(
                    runId,
                    5,
                    "Start Experiment",
                    "POST",
                    startPath,
                    startRequestJson,
                    experimentId);

                var startRaw = await _apiClient.PostJson(startPath, startRequestJson, experimentId);

                if (!TryEnsureApiSuccess(startRaw, "POST /api/v1/experiments/{id}/start", out var startMeta, out var startError))
                {
                    FinishStep(startStep, startRaw, startMeta, false, startError);
                    CompleteFailure(result, startError, runId);
                    return result;
                }

                var startResponse = JsonUtility.FromJson<ExperimentStartedResponseDto>(startRaw.Body);
                if (startResponse == null || startResponse.data == null)
                {
                    const string message = "Experiment start response is invalid.";
                    FinishStep(startStep, startRaw, startMeta, false, message);
                    CompleteFailure(result, message, runId);
                    return result;
                }

                if (!ExperimentStatusMapper.TryParse(startResponse.data.status, out var runningStatus)
                    || runningStatus != ExperimentStatus.Running)
                {
                    var message = "Unexpected start status: " + startResponse.data.status;
                    FinishStep(startStep, startRaw, startMeta, false, message);
                    CompleteFailure(result, message, runId);
                    return result;
                }

                FinishStep(startStep, startRaw, startMeta, true, "Experiment running at: " + startResponse.data.started_at_ms);

                var completeRequest = BuildCompleteRequest();
                var completeRequestJson = JsonUtility.ToJson(completeRequest);
                var completePath = "/api/v1/experiments/" + experimentId + "/complete";
                var completeStep = BeginStep(
                    runId,
                    6,
                    "Complete Experiment",
                    "POST",
                    completePath,
                    completeRequestJson,
                    experimentId);

                var completeRaw = await _apiClient.PostJson(completePath, completeRequestJson, experimentId);

                if (!TryEnsureApiSuccess(completeRaw, "POST /api/v1/experiments/{id}/complete", out var completeMeta, out var completeError))
                {
                    FinishStep(completeStep, completeRaw, completeMeta, false, completeError);
                    CompleteFailure(result, completeError, runId);
                    return result;
                }

                var completeResponse = JsonUtility.FromJson<ExperimentCompletedResponseDto>(completeRaw.Body);
                if (completeResponse == null || completeResponse.data == null)
                {
                    const string message = "Experiment complete response is invalid.";
                    FinishStep(completeStep, completeRaw, completeMeta, false, message);
                    CompleteFailure(result, message, runId);
                    return result;
                }

                if (!ExperimentStatusMapper.TryParse(completeResponse.data.status, out var completeStatus)
                    || completeStatus != ExperimentStatus.Completed)
                {
                    var message = "Unexpected complete status: " + completeResponse.data.status;
                    FinishStep(completeStep, completeRaw, completeMeta, false, message);
                    CompleteFailure(result, message, runId);
                    return result;
                }

                FinishStep(completeStep, completeRaw, completeMeta, true, "Experiment completed.");

                result.Success = true;
                result.ExperimentId = experimentId;
                result.SelectedCrowdTypeId = selectedTypeId;
                result.Message = "M1 flow success. experiment_id=" + experimentId + ", selected_type=" + selectedTypeId;
                M1FlowDebugTraceStore.CompleteRun(runId, M1FlowRunStatus.Succeeded, result.Message);
                return result;
            }
            catch (Exception exception)
            {
                Debug.LogException(exception);
                CompleteFailure(result, "M1 flow exception: " + exception.Message, runId);
                return result;
            }
        }

        private static bool TryEnsureApiSuccess(
            BackendApiRawResponse raw,
            string endpoint,
            out ApiResponseMeta meta,
            out string error)
        {
            meta = TryParseMeta(raw != null ? raw.Body : null);

            if (raw == null)
            {
                error = endpoint + " failed: no response.";
                return false;
            }

            if (!raw.IsSuccess)
            {
                if (meta != null)
                {
                    error = endpoint + " failed: status=" + raw.StatusCode + ", api_code=" + meta.code + ", message=" + meta.message;
                    return false;
                }

                error = endpoint + " failed: status=" + raw.StatusCode + ", transport=" + raw.TransportSuccess + ", error=" + raw.Error;
                return false;
            }

            if (meta == null)
            {
                error = endpoint + " failed: cannot parse response envelope.";
                return false;
            }

            if (meta.code != 0)
            {
                error = endpoint + " failed: api_code=" + meta.code + ", message=" + meta.message;
                return false;
            }

            error = string.Empty;
            return true;
        }

        private static ApiResponseMeta TryParseMeta(string body)
        {
            if (string.IsNullOrWhiteSpace(body))
            {
                return null;
            }

            try
            {
                return JsonUtility.FromJson<ApiResponseMeta>(body);
            }
            catch
            {
                return null;
            }
        }

        private static M1FlowStepDebugInfo BeginStep(
            string runId,
            int stepOrder,
            string stepName,
            string method,
            string path,
            string requestBody,
            string experimentId)
        {
            var step = new M1FlowStepDebugInfo
            {
                RunId = runId,
                StepOrder = stepOrder,
                StepName = stepName,
                Status = M1FlowStepStatus.Running,
                Method = method,
                Path = path,
                Url = string.Empty,
                RequestBody = string.IsNullOrEmpty(requestBody) ? string.Empty : requestBody,
                RequestId = string.Empty,
                ExperimentId = string.IsNullOrEmpty(experimentId) ? string.Empty : experimentId,
                HttpStatusCode = 0,
                TransportSuccess = false,
                Attempt = 0,
                ApiCode = int.MinValue,
                ApiMessage = string.Empty,
                TransportError = string.Empty,
                ResponseBody = string.Empty,
                Note = "Request sent",
                StartedAtUnixMs = RequestIdUtility.GetUnixTimestampMs(),
                FinishedAtUnixMs = 0,
                DurationMs = 0,
            };

            M1FlowDebugTraceStore.UpsertStep(step);
            return step;
        }

        private static void FinishStep(
            M1FlowStepDebugInfo step,
            BackendApiRawResponse raw,
            ApiResponseMeta meta,
            bool success,
            string note)
        {
            if (step == null)
            {
                return;
            }

            var output = step.Clone();
            output.Status = success ? M1FlowStepStatus.Succeeded : M1FlowStepStatus.Failed;
            output.Note = string.IsNullOrWhiteSpace(note) ? string.Empty : note;

            if (raw != null)
            {
                output.Method = string.IsNullOrEmpty(raw.Method) ? output.Method : raw.Method;
                output.Path = string.IsNullOrEmpty(raw.Path) ? output.Path : raw.Path;
                output.Url = string.IsNullOrEmpty(raw.Url) ? output.Url : raw.Url;
                output.ExperimentId = string.IsNullOrEmpty(raw.ExperimentId) ? output.ExperimentId : raw.ExperimentId;
                output.RequestBody = string.IsNullOrEmpty(raw.RequestBody) ? output.RequestBody : raw.RequestBody;
                output.RequestId = raw.RequestId;
                output.HttpStatusCode = raw.StatusCode;
                output.TransportSuccess = raw.TransportSuccess;
                output.Attempt = raw.Attempt;
                output.TransportError = raw.Error;
                output.ResponseBody = raw.Body;

                if (raw.SentAtUnixMs > 0)
                {
                    output.StartedAtUnixMs = raw.SentAtUnixMs;
                }

                if (raw.ReceivedAtUnixMs > 0)
                {
                    output.FinishedAtUnixMs = raw.ReceivedAtUnixMs;
                }

                if (raw.DurationMs > 0)
                {
                    output.DurationMs = raw.DurationMs;
                }
            }

            if (meta != null)
            {
                output.ApiCode = meta.code;
                output.ApiMessage = meta.message;
            }

            var now = RequestIdUtility.GetUnixTimestampMs();
            if (output.StartedAtUnixMs <= 0)
            {
                output.StartedAtUnixMs = now;
            }

            if (output.FinishedAtUnixMs <= 0)
            {
                output.FinishedAtUnixMs = now;
            }

            if (output.DurationMs <= 0)
            {
                output.DurationMs = Math.Max(0, output.FinishedAtUnixMs - output.StartedAtUnixMs);
            }

            M1FlowDebugTraceStore.UpsertStep(output);
        }

        private ExperimentCreateRequestDto BuildCreateRequest()
        {
            var ticks = DateTime.UtcNow.ToString("yyyyMMdd_HHmmss");
            return new ExperimentCreateRequestDto
            {
                name = "unity_m1_" + ticks,
                description = "Unity M1 minimal flow",
                map_id = _options.mapId,
                tags = _options.experimentTags ?? Array.Empty<string>(),
                llm_config = new LlmConfigDto
                {
                    model = "gpt-4o-mini",
                    temperature = 0.4f,
                    max_tokens = 256,
                    timeout_ms = 5000,
                },
                decision_config = new DecisionConfigDto
                {
                    batch_interval_ms = 1000,
                    batch_size = 32,
                    fallback_to_algorithm = true,
                },
            };
        }

        private ExperimentCompleteRequestDto BuildCompleteRequest()
        {
            return new ExperimentCompleteRequestDto
            {
                reason = CompletionReason.SimulationFinished.ToApiValue(),
                actual_duration_ms = 1000,
                final_agent_count = Math.Max(1, _options.crowdCount),
            };
        }

        private TopologyUpsertRequestDto BuildMinimalTopologyRequest()
        {
            return new TopologyUpsertRequestDto
            {
                zones = new[]
                {
                    new TopologyZoneDto
                    {
                        zone_id = _options.zoneId,
                        zone_name = _options.zoneName,
                        zone_type = ZoneType.Plaza.ToApiValue(),
                        bounds = new Bounds2DDto
                        {
                            min_x = _options.zoneMinX,
                            min_z = _options.zoneMinZ,
                            max_x = _options.zoneMaxX,
                            max_z = _options.zoneMaxZ,
                        },
                        capacity = Math.Max(50, _options.crowdCount * 2),
                        is_entry_zone = true,
                    },
                },
                exits = Array.Empty<TopologyExitDto>(),
            };
        }

        private CrowdAllocationRequestDto BuildMinimalAllocationRequest(string crowdTypeId, string experimentId)
        {
            return new CrowdAllocationRequestDto
            {
                groups = new[]
                {
                    new CrowdAllocationGroupDto
                    {
                        type_id = crowdTypeId,
                        count = Math.Max(1, _options.crowdCount),
                        distribution = new RandomEntryDistributionDto
                        {
                            avoid_exits = true,
                        },
                    },
                },
                agent_id_prefix = BuildAgentPrefix(experimentId),
            };
        }

        private static string BuildAgentPrefix(string experimentId)
        {
            if (string.IsNullOrWhiteSpace(experimentId))
            {
                return "uagent";
            }

            var normalized = experimentId.Replace("-", string.Empty).ToLowerInvariant();
            var sliceLength = Math.Min(8, normalized.Length);
            return "u" + normalized.Substring(0, sliceLength);
        }

        private static string SelectCrowdTypeId(CrowdTypeTemplateDto[] templates, string preferredTypeId)
        {
            if (!string.IsNullOrWhiteSpace(preferredTypeId))
            {
                for (var i = 0; i < templates.Length; i++)
                {
                    if (string.Equals(templates[i].type_id, preferredTypeId, StringComparison.OrdinalIgnoreCase))
                    {
                        return templates[i].type_id;
                    }
                }
            }

            for (var i = 0; i < templates.Length; i++)
            {
                if (templates[i].is_builtin)
                {
                    return templates[i].type_id;
                }
            }

            return templates[0].type_id;
        }

        private static void CompleteFailure(
            M1FlowExecutionResult result,
            string message,
            string runId)
        {
            result.Success = false;
            result.Message = message;
            M1FlowDebugTraceStore.CompleteRun(runId, M1FlowRunStatus.Failed, message);
        }
    }
}
