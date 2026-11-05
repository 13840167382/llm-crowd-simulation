using System;
using System.Collections.Generic;
using Cysharp.Threading.Tasks;
using CrowdSim.Core;
using CrowdSim.Data.Api;
using CrowdSim.Data.Common;
using CrowdSim.Data.CrowdTypes;
using CrowdSim.Data.Decisions;
using CrowdSim.Data.Enums;
using CrowdSim.Data.Topology;
using CrowdSim.Data.UI;
using CrowdSim.Simulation.Topology;
using CrowdSim.Utils;
using UnityEngine;

namespace CrowdSim.Simulation.Decision
{
    public sealed class DecisionBatchScheduler : MonoBehaviour
    {
        [Header("Dependencies")]
        [SerializeField] private ExperimentManager experimentManager;

        [Header("Decision Engine")]
        [SerializeField] private DecisionDispatchMode dispatchMode = DecisionDispatchMode.HybridPreferLlm;

        public bool IsLlmOnly
        {
            get { return dispatchMode == DecisionDispatchMode.LlmOnly; }
        }

        [SerializeField] private TopologySceneConfig topologySceneConfig;
        [SerializeField] private bool autoFindTopologySceneConfig = true;
        [SerializeField] private CrowdTypeTemplateConfig crowdTypeTemplateConfig;
        [SerializeField] private LocalDecisionScoringConfig localDecisionScoring = new LocalDecisionScoringConfig();
        [SerializeField] private CrowdTypeDecisionScoringOverride[] crowdTypeDecisionScoringOverrides = Array.Empty<CrowdTypeDecisionScoringOverride>();

        [Header("Dispatch Config")]
        [SerializeField] private bool dispatchEnabledOnStart;
        [SerializeField, Min(0.2f)] private float collectIntervalSeconds = 5f;
        [SerializeField, Min(0.05f)] private float decisionIntervalSeconds = 5f;
        [SerializeField] private bool ensureSingleInFlightRequest = false;
        [SerializeField, Min(1)] private int maxConcurrentRequestsWhenUnlocked = 4;
        [SerializeField, Min(0f)] private float backendErrorBaseCooldownSeconds = 1f;
        [SerializeField, Min(0f)] private float backendErrorMaxCooldownSeconds = 60f;
        [SerializeField] private bool hybridPreferLocalDuringBackendCooldown = true;
        [SerializeField, Min(1)] private int maxAgentsPerBatch = 64;
        [SerializeField, Min(0)] private int minAgentsPerBatch;
        [SerializeField] private bool onlyWhenExperimentRunning = true;
        [SerializeField] private bool collectFromAgentReports = true;
        [SerializeField] private bool allowFallbackCollectFromScene;
        [SerializeField] private bool autoCollectFromChildren = true;
        [SerializeField] private bool includeInactiveAgents;
        [SerializeField] private bool balanceInitialDecisionsAcrossEntries = true;
        [SerializeField, Min(1)] private int maxInitialDecisionsPerEntryPerBatch = 6;
        [SerializeField, Min(1)] private int maxInitialDecisionAgentsPerBatch = 24;
        [SerializeField] private bool randomizeBatchCandidateOrder = true;
        [SerializeField, Min(0.1f)] private float randomizationFairnessAgeWindowSeconds = 8f;
        [SerializeField] private DecisionAgentPoint[] decisionAgents = Array.Empty<DecisionAgentPoint>();
        [SerializeField] private string explicitExperimentId = string.Empty;
        [SerializeField] private bool logToConsole = true;

        [Header("Debug")]
        [SerializeField] private bool enableDetailedDispatchLogs;
        [SerializeField, Min(0.2f)] private float detailedDispatchLogIntervalSeconds = 2f;
        [SerializeField, Min(1)] private int detailedDispatchSampleAgentCount = 6;

        [Header("Batch Bundle Log")]
        [SerializeField] private bool enableBatchBundleLog = true;
        [SerializeField] private bool suppressLegacyLogsWhenBatchBundle = true;
        [SerializeField, Min(1)] private int batchBundleMaxAgentPayloadCount = 128;
        [SerializeField] private bool includeBatchBundlePayloadJson;

        [Header("Optimization")]
        [SerializeField, Min(0.2f)] private float topologyLookupRefreshIntervalSeconds = 2f;
        [SerializeField] private bool useRegisteredAgentDiscovery = true;
        [SerializeField, Min(0.2f)] private float collectTimeSliceBudgetMs = 1.2f;
        [SerializeField, Min(0.2f)] private float applyResultsTimeSliceBudgetMs = 1.2f;
        [SerializeField, Min(0.2f)] private float crowdTypeAttractionLookupRefreshIntervalSeconds = 2f;

        [Header("Runtime")]
        [SerializeField] private bool dispatchEnabled;
        [SerializeField] private bool requestInFlight;
        [SerializeField] private int inFlightRequestCount;
        [SerializeField] private string lastBatchId = string.Empty;
        [SerializeField] private int lastRequestedAgentCount;
        [SerializeField] private int lastResponseDecisionCount;
        [SerializeField] private int lastSuccessCount;
        [SerializeField] private int lastFallbackCount;
        [SerializeField] private int cachedReportCount;
        [SerializeField] private int cachedValidReportCount;
        [SerializeField] private int cachedPendingDecisionCount;
        [SerializeField] private string lastInvalidReasonSummary = string.Empty;
        [SerializeField] private long lastDispatchAtUnixMs;
        [SerializeField] private string lastDispatchSummary = "Not started";
        [SerializeField] private int consecutiveBackendConflictCount;
        [SerializeField] private int consecutiveBackendServerErrorCount;
        [SerializeField] private float backendCooldownUntilTime;

        private readonly Dictionary<string, DecisionAgentReportCacheItem> reportedAgentCache =
            new Dictionary<string, DecisionAgentReportCacheItem>(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, DecisionAgentPoint> reportedAgentPoints =
            new Dictionary<string, DecisionAgentPoint>(StringComparer.OrdinalIgnoreCase);
        private readonly HashSet<string> inFlightAgentIds =
            new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        private BackendApiClient apiClient;
        private int batchSequence;
        private float nextCollectAtTime;
        private float nextDispatchAtTime;
        private float nextDetailedDispatchLogAtTime;
        private float nextTopologyLookupRefreshAtTime;
        private float nextCrowdTypeAttractionLookupRefreshAtTime;
        private bool collectPassInProgress;
        private int collectPassIndex;
        private DecisionAgentPoint[] resolvedDecisionAgentsCache = Array.Empty<DecisionAgentPoint>();
        private bool resolvedDecisionAgentsCacheDirty = true;

        private readonly Stack<DispatchWorkBuffer> dispatchWorkBufferPool =
            new Stack<DispatchWorkBuffer>(4);

        private readonly HashSet<string> collectActiveAgentIdsScratch =
            new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        private readonly List<string> staleAgentIdsScratch = new List<string>(256);
        private readonly Dictionary<string, int> invalidReasonCountScratch =
            new Dictionary<string, int>(StringComparer.Ordinal);
        private readonly List<KeyValuePair<string, int>> sortedInvalidReasonCountScratch =
            new List<KeyValuePair<string, int>>(16);
        private readonly Dictionary<string, int> topologyZoneCapacityByIdCache =
            new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, TopologyExitDto> topologyExitByIdCache =
            new Dictionary<string, TopologyExitDto>(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, string> topologyZoneTypeByIdCache =
            new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        private readonly HashSet<string> topologyFinalExitZoneIdsCache =
            new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, CrowdTypeAttractionWeightsDto> crowdTypeAttractionWeightsByTypeIdCache =
            new Dictionary<string, CrowdTypeAttractionWeightsDto>(StringComparer.OrdinalIgnoreCase);
        private readonly List<DecisionAgentPoint> resolvedDecisionAgentsScratch =
            new List<DecisionAgentPoint>(512);
        private readonly HashSet<int> resolvedDecisionAgentInstanceIdsScratch = new HashSet<int>();
        private readonly Dictionary<string, ZonePopulationAccumulator> uiZonePopulationAccumulatorScratch =
            new Dictionary<string, ZonePopulationAccumulator>(StringComparer.OrdinalIgnoreCase);
        private readonly List<ZonePopulationSnapshotItem> uiZonePopulationItemsScratch =
            new List<ZonePopulationSnapshotItem>(32);
        private readonly List<CrowdTypePopulationSnapshotItem> uiCrowdTypePopulationItemsScratch =
            new List<CrowdTypePopulationSnapshotItem>(64);
        private readonly Dictionary<string, CrowdTypePopulationAccumulator> uiTotalCrowdTypePopulationAccumulatorScratch =
            new Dictionary<string, CrowdTypePopulationAccumulator>(StringComparer.OrdinalIgnoreCase);
        private readonly List<TotalCrowdTypePopulationSnapshotItem> uiTotalCrowdTypePopulationItemsScratch =
            new List<TotalCrowdTypePopulationSnapshotItem>(32);

        private readonly List<string> decisionTranscriptLines = new List<string>(4096);

        private readonly Dictionary<string, int> fixedPathAssignedCountByType =
            new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);

        private sealed class ZonePopulationAccumulator
        {
            public bool IsFinalExitZone;
            public int TotalCount;
            public readonly Dictionary<string, int> CrowdTypeCounts =
                new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        }

        private sealed class CrowdTypePopulationAccumulator
        {
            public int TotalCount;
            public int VisibleCount;
        }

        private struct SceneDispatchCandidate
        {
            public DecisionAgentPoint Point;
            public DecisionAgentRequestDto Request;
            public string AgentId;
        }

        private sealed class DispatchWorkBuffer
        {
            public readonly List<string> reservedAgentIds = new List<string>(128);
            public readonly List<DecisionAgentRequestDto> requestAgents = new List<DecisionAgentRequestDto>(128);
            public readonly Dictionary<string, DecisionAgentPoint> requestAgentMap =
                new Dictionary<string, DecisionAgentPoint>(StringComparer.OrdinalIgnoreCase);
            public readonly List<DecisionAgentReportCacheItem> orderedReports = new List<DecisionAgentReportCacheItem>(128);
            public readonly Dictionary<string, int> selectedInitialByEntry =
                new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            public readonly List<SceneDispatchCandidate> sceneDispatchCandidates = new List<SceneDispatchCandidate>(128);
            public readonly List<DecisionResultItemDto> localDecisions = new List<DecisionResultItemDto>(128);
            public readonly Dictionary<string, int> currentZonePopulation =
                new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            public readonly Dictionary<string, int> predictedTargetInflow =
                new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            public int selectedInitialCount;

            public void Reset()
            {
                reservedAgentIds.Clear();
                requestAgents.Clear();
                requestAgentMap.Clear();
                orderedReports.Clear();
                selectedInitialByEntry.Clear();
                sceneDispatchCandidates.Clear();
                localDecisions.Clear();
                currentZonePopulation.Clear();
                predictedTargetInflow.Clear();
                selectedInitialCount = 0;
            }
        }

        public bool ShouldSuppressAgentBehaviorLogs
        {
            get { return enableBatchBundleLog && suppressLegacyLogsWhenBatchBundle; }
        }

        private void Awake()
        {
            apiClient = new BackendApiClient(ExperimentManager.SharedBackendOptions);
        }

        private void OnEnable()
        {
            if (dispatchEnabledOnStart)
            {
                StartDispatching();
            }
        }

        private void OnDisable()
        {
            dispatchEnabled = false;
            requestInFlight = false;
            inFlightRequestCount = 0;
            consecutiveBackendConflictCount = 0;
            consecutiveBackendServerErrorCount = 0;
            backendCooldownUntilTime = 0f;
            nextTopologyLookupRefreshAtTime = 0f;
            nextCrowdTypeAttractionLookupRefreshAtTime = 0f;
            collectPassInProgress = false;
            collectPassIndex = 0;
            resolvedDecisionAgentsCacheDirty = true;
            resolvedDecisionAgentsCache = Array.Empty<DecisionAgentPoint>();
            reportedAgentCache.Clear();
            reportedAgentPoints.Clear();
            inFlightAgentIds.Clear();
            topologyZoneCapacityByIdCache.Clear();
            topologyExitByIdCache.Clear();
            topologyZoneTypeByIdCache.Clear();
            topologyFinalExitZoneIdsCache.Clear();
            crowdTypeAttractionWeightsByTypeIdCache.Clear();
            RefreshReportCacheStats();
        }

        private DispatchWorkBuffer AcquireDispatchWorkBuffer()
        {
            DispatchWorkBuffer buffer;
            if (dispatchWorkBufferPool.Count > 0)
            {
                buffer = dispatchWorkBufferPool.Pop();
            }
            else
            {
                buffer = new DispatchWorkBuffer();
            }

            buffer.Reset();
            return buffer;
        }

        private void ReleaseDispatchWorkBuffer(DispatchWorkBuffer buffer)
        {
            if (buffer == null)
            {
                return;
            }

            buffer.Reset();
            dispatchWorkBufferPool.Push(buffer);
        }

        private void EnsureTopologyLookupCache()
        {
            if (!Application.isPlaying
                && topologyZoneCapacityByIdCache.Count > 0
                && topologyExitByIdCache.Count > 0)
            {
                return;
            }

            var now = Time.unscaledTime;
            if (topologyZoneCapacityByIdCache.Count > 0
                && topologyExitByIdCache.Count > 0
                && now < nextTopologyLookupRefreshAtTime)
            {
                return;
            }

            topologyZoneCapacityByIdCache.Clear();
            topologyExitByIdCache.Clear();
            topologyZoneTypeByIdCache.Clear();
            topologyFinalExitZoneIdsCache.Clear();

            ResolveTopologySceneConfig();
            if (topologySceneConfig == null)
            {
                nextTopologyLookupRefreshAtTime = now + Mathf.Max(0.2f, topologyLookupRefreshIntervalSeconds);
                return;
            }

            TopologyUpsertRequestDto topologyRequest;
            string error;
            if (!topologySceneConfig.TryBuildTopologyRequest(out topologyRequest, out error)
                || topologyRequest == null)
            {
                nextTopologyLookupRefreshAtTime = now + Mathf.Max(0.2f, topologyLookupRefreshIntervalSeconds);
                return;
            }

            if (topologyRequest.zones != null)
            {
                for (var i = 0; i < topologyRequest.zones.Length; i++)
                {
                    var zone = topologyRequest.zones[i];
                    if (zone == null)
                    {
                        continue;
                    }

                    var zoneId = NormalizeAgentId(zone.zone_id);
                    if (string.IsNullOrWhiteSpace(zoneId))
                    {
                        continue;
                    }

                    topologyZoneCapacityByIdCache[zoneId] = Math.Max(1, zone.capacity);

                    var normalizedZoneType = NormalizeZoneTypeKey(zone.zone_type);
                    if (!string.IsNullOrWhiteSpace(normalizedZoneType))
                    {
                        topologyZoneTypeByIdCache[zoneId] = normalizedZoneType;
                    }

                    ZoneType zoneType;
                    if (ZoneTypeMapper.TryParse(zone.zone_type, out zoneType)
                        && zoneType == ZoneType.ExitFinal)
                    {
                        topologyFinalExitZoneIdsCache.Add(zoneId);
                    }
                    else if (!string.IsNullOrWhiteSpace(zone.zone_type)
                        && string.Equals(zone.zone_type.Trim(), "exit_final", StringComparison.OrdinalIgnoreCase))
                    {
                        topologyFinalExitZoneIdsCache.Add(zoneId);
                    }
                }
            }

            if (topologyRequest.exits != null)
            {
                for (var i = 0; i < topologyRequest.exits.Length; i++)
                {
                    var exit = topologyRequest.exits[i];
                    if (exit == null)
                    {
                        continue;
                    }

                    var exitId = NormalizeAgentId(exit.exit_id);
                    if (string.IsNullOrWhiteSpace(exitId))
                    {
                        continue;
                    }

                    topologyExitByIdCache[exitId] = exit;
                }
            }

            nextTopologyLookupRefreshAtTime = now + Mathf.Max(0.2f, topologyLookupRefreshIntervalSeconds);
        }

        private void EnsureCrowdTypeAttractionLookupCache()
        {
            if (!Application.isPlaying && crowdTypeAttractionWeightsByTypeIdCache.Count > 0)
            {
                return;
            }

            var now = Time.unscaledTime;
            if (crowdTypeAttractionWeightsByTypeIdCache.Count > 0
                && now < nextCrowdTypeAttractionLookupRefreshAtTime)
            {
                return;
            }

            crowdTypeAttractionWeightsByTypeIdCache.Clear();
            var loadedFromBackendTemplates = TryBuildAttractionWeightsFromBackendTemplates();
            if (!loadedFromBackendTemplates)
            {
                TryBuildAttractionWeightsFromLocalConfig();
            }

            if (crowdTypeAttractionWeightsByTypeIdCache.Count <= 0)
            {
                nextCrowdTypeAttractionLookupRefreshAtTime = now
                    + Mathf.Max(0.2f, crowdTypeAttractionLookupRefreshIntervalSeconds);
                return;
            }

            if (logToConsole)
            {
                Debug.Log(
                    "[DecisionBatchScheduler] Attraction weights cache refreshed. source="
                    + (loadedFromBackendTemplates ? "backend_templates" : "local_config")
                    + ", crowd_types=" + crowdTypeAttractionWeightsByTypeIdCache.Count,
                    this);
            }

            nextCrowdTypeAttractionLookupRefreshAtTime = now
                + Mathf.Max(0.2f, crowdTypeAttractionLookupRefreshIntervalSeconds);
        }

        private bool TryBuildAttractionWeightsFromBackendTemplates()
        {
            if (experimentManager == null)
            {
                return false;
            }

            var templates = experimentManager.GetRuntimeCrowdTypeTemplatesSnapshot();
            if (templates == null || templates.Length <= 0)
            {
                return false;
            }

            for (var i = 0; i < templates.Length; i++)
            {
                var template = templates[i];
                if (template == null)
                {
                    continue;
                }

                var typeId = NormalizeAgentId(template.type_id);
                if (string.IsNullOrWhiteSpace(typeId))
                {
                    continue;
                }

                crowdTypeAttractionWeightsByTypeIdCache[typeId] = template.attraction_weights != null
                    ? template.attraction_weights.Clone()
                    : CrowdTypeAttractionWeightsDto.CreateDefault();
            }

            return crowdTypeAttractionWeightsByTypeIdCache.Count > 0;
        }

        private void TryBuildAttractionWeightsFromLocalConfig()
        {
            if (crowdTypeTemplateConfig == null)
            {
                return;
            }

            var entries = crowdTypeTemplateConfig.CloneTemplates();
            if (entries != null)
            {
                for (var i = 0; i < entries.Length; i++)
                {
                    var entry = entries[i];
                    if (entry == null)
                    {
                        continue;
                    }

                    var typeId = NormalizeAgentId(entry.GetNormalizedTypeId());
                    if (string.IsNullOrWhiteSpace(typeId))
                    {
                        continue;
                    }

                    var weights = entry.attractionWeights != null
                        ? entry.attractionWeights.BuildDto()
                        : CrowdTypeAttractionWeightsDto.CreateDefault();
                    crowdTypeAttractionWeightsByTypeIdCache[typeId] = weights;
                }
            }
        }

        public DecisionRuntimeUiPopulationSnapshot GetUiPopulationSnapshot()
        {
            var snapshot = DecisionRuntimeUiPopulationSnapshot.Empty();
            snapshot.snapshot_at_unix_ms = RequestIdUtility.GetUnixTimestampMs();

            if (reportedAgentCache.Count <= 0)
            {
                return snapshot;
            }

            EnsureTopologyLookupCache();
            uiZonePopulationAccumulatorScratch.Clear();
            uiTotalCrowdTypePopulationAccumulatorScratch.Clear();
            var visibleAgentCount = 0;

            foreach (var pair in reportedAgentCache)
            {
                var report = pair.Value;
                if (report == null || report.request == null)
                {
                    continue;
                }

                var zoneId = NormalizeAgentId(report.request.current_zone_id);
                if (string.IsNullOrWhiteSpace(zoneId))
                {
                    zoneId = "(unknown_zone)";
                }

                var crowdType = NormalizeAgentId(report.request.crowd_type);
                if (string.IsNullOrWhiteSpace(crowdType))
                {
                    crowdType = "(unknown)";
                }

                ZonePopulationAccumulator zoneAccumulator;
                if (!uiZonePopulationAccumulatorScratch.TryGetValue(zoneId, out zoneAccumulator)
                    || zoneAccumulator == null)
                {
                    zoneAccumulator = new ZonePopulationAccumulator
                    {
                        IsFinalExitZone = topologyFinalExitZoneIdsCache.Contains(zoneId),
                        TotalCount = 0,
                    };
                    uiZonePopulationAccumulatorScratch[zoneId] = zoneAccumulator;
                }

                zoneAccumulator.TotalCount++;
                IncrementCount(zoneAccumulator.CrowdTypeCounts, crowdType);

                CrowdTypePopulationAccumulator totalCrowdTypeAccumulator;
                if (!uiTotalCrowdTypePopulationAccumulatorScratch.TryGetValue(crowdType, out totalCrowdTypeAccumulator)
                    || totalCrowdTypeAccumulator == null)
                {
                    totalCrowdTypeAccumulator = new CrowdTypePopulationAccumulator();
                    uiTotalCrowdTypePopulationAccumulatorScratch[crowdType] = totalCrowdTypeAccumulator;
                }

                totalCrowdTypeAccumulator.TotalCount++;

                DecisionAgentPoint point;
                if (reportedAgentPoints.TryGetValue(pair.Key, out point)
                    && point != null
                    && point.HasReceivedFirstDecision)
                {
                    visibleAgentCount++;
                    totalCrowdTypeAccumulator.VisibleCount++;
                }
            }

            snapshot.visible_agent_count = visibleAgentCount;
            uiZonePopulationItemsScratch.Clear();
            foreach (var pair in uiZonePopulationAccumulatorScratch)
            {
                var zoneId = pair.Key;
                var zoneAccumulator = pair.Value;
                if (zoneAccumulator == null)
                {
                    continue;
                }

                uiCrowdTypePopulationItemsScratch.Clear();
                foreach (var crowdTypePair in zoneAccumulator.CrowdTypeCounts)
                {
                    uiCrowdTypePopulationItemsScratch.Add(new CrowdTypePopulationSnapshotItem
                    {
                        crowd_type = crowdTypePair.Key,
                        count = crowdTypePair.Value,
                    });
                }

                uiCrowdTypePopulationItemsScratch.Sort((left, right) =>
                    string.Compare(left.crowd_type, right.crowd_type, StringComparison.OrdinalIgnoreCase));

                uiZonePopulationItemsScratch.Add(new ZonePopulationSnapshotItem
                {
                    zone_id = zoneId,
                    is_final_exit_zone = zoneAccumulator.IsFinalExitZone,
                    total_count = zoneAccumulator.TotalCount,
                    crowd_type_populations = uiCrowdTypePopulationItemsScratch.ToArray(),
                });
            }

            uiZonePopulationItemsScratch.Sort((left, right) =>
                string.Compare(left.zone_id, right.zone_id, StringComparison.OrdinalIgnoreCase));
            snapshot.zone_populations = uiZonePopulationItemsScratch.ToArray();

            uiTotalCrowdTypePopulationItemsScratch.Clear();
            foreach (var pair in uiTotalCrowdTypePopulationAccumulatorScratch)
            {
                var accumulator = pair.Value;
                if (accumulator == null)
                {
                    continue;
                }

                uiTotalCrowdTypePopulationItemsScratch.Add(new TotalCrowdTypePopulationSnapshotItem
                {
                    crowd_type = pair.Key,
                    total_count = Math.Max(0, accumulator.TotalCount),
                    visible_agent_count = Math.Max(0, accumulator.VisibleCount),
                });
            }

            uiTotalCrowdTypePopulationItemsScratch.Sort((left, right) =>
                string.Compare(left.crowd_type, right.crowd_type, StringComparison.OrdinalIgnoreCase));
            snapshot.total_crowd_type_populations = uiTotalCrowdTypePopulationItemsScratch.ToArray();

            foreach (var pair in uiZonePopulationAccumulatorScratch)
            {
                var zoneAccumulator = pair.Value;
                if (zoneAccumulator == null)
                {
                    continue;
                }

                zoneAccumulator.TotalCount = 0;
                zoneAccumulator.CrowdTypeCounts.Clear();
            }

            foreach (var pair in uiTotalCrowdTypePopulationAccumulatorScratch)
            {
                var accumulator = pair.Value;
                if (accumulator == null)
                {
                    continue;
                }

                accumulator.TotalCount = 0;
                accumulator.VisibleCount = 0;
            }

            return snapshot;
        }

        private void Update()
        {
            if (!Application.isPlaying || !dispatchEnabled)
            {
                return;
            }

            if (!collectPassInProgress && Time.unscaledTime >= nextCollectAtTime)
            {
                BeginCollectPass();
            }

            if (collectPassInProgress)
            {
                ProcessCollectPassTimeSlice();
            }

            if (Time.unscaledTime < nextDispatchAtTime)
            {
                return;
            }

            if (!CanStartDispatchRequest())
            {
                return;
            }

            DispatchNowAsync().Forget();
        }

        [ContextMenu("Start Decision Dispatch")]
        public void StartDispatching()
        {
            dispatchEnabled = true;
            BeginCollectPass();
            ProcessCollectPassTimeSlice(true);
            if (collectPassInProgress)
            {
                nextCollectAtTime = Time.unscaledTime;
            }
            else
            {
                nextCollectAtTime = Time.unscaledTime + Mathf.Max(0.2f, collectIntervalSeconds);
            }
            nextDispatchAtTime = Time.unscaledTime;
        }

        [ContextMenu("Stop Decision Dispatch")]
        public void StopDispatching()
        {
            dispatchEnabled = false;
        }

        [ContextMenu("Reset Runtime State")]
        public void ResetRuntimeState()
        {
            StopDispatching();
            requestInFlight = false;
            inFlightRequestCount = 0;
            consecutiveBackendConflictCount = 0;
            consecutiveBackendServerErrorCount = 0;
            backendCooldownUntilTime = 0f;
            nextTopologyLookupRefreshAtTime = 0f;
            nextCrowdTypeAttractionLookupRefreshAtTime = 0f;
            collectPassInProgress = false;
            collectPassIndex = 0;
            resolvedDecisionAgentsCacheDirty = true;
            resolvedDecisionAgentsCache = Array.Empty<DecisionAgentPoint>();
            reportedAgentCache.Clear();
            reportedAgentPoints.Clear();
            inFlightAgentIds.Clear();
            topologyZoneCapacityByIdCache.Clear();
            topologyExitByIdCache.Clear();
            topologyZoneTypeByIdCache.Clear();
            topologyFinalExitZoneIdsCache.Clear();
            crowdTypeAttractionWeightsByTypeIdCache.Clear();
            batchSequence = 0;
            lastBatchId = string.Empty;
            lastRequestedAgentCount = 0;
            lastResponseDecisionCount = 0;
            lastSuccessCount = 0;
            lastFallbackCount = 0;
            lastInvalidReasonSummary = string.Empty;
            lastDispatchAtUnixMs = 0;
            lastDispatchSummary = "Not started";
            RefreshReportCacheStats();
            decisionTranscriptLines.Clear();
            fixedPathAssignedCountByType.Clear();
            Debug.Log("[DecisionBatchScheduler] Runtime state reset.", this);
        }

        public string GetDecisionTranscript()
        {
            if (decisionTranscriptLines.Count <= 0)
            {
                return string.Empty;
            }

            return string.Join("\n", decisionTranscriptLines);
        }

        private void AppendDecisionTranscript(
            string batchId,
            List<DecisionAgentRequestDto> requestAgents,
            DecisionResultItemDto[] decisions)
        {
            if (requestAgents == null || requestAgents.Count <= 0)
            {
                return;
            }

            var timestamp = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff");
            decisionTranscriptLines.Add($"=== Batch: {batchId} | Time: {timestamp} ===");

            var decisionByAgentId = new Dictionary<string, DecisionResultItemDto>(StringComparer.OrdinalIgnoreCase);
            if (decisions != null)
            {
                for (var i = 0; i < decisions.Length; i++)
                {
                    var decision = decisions[i];
                    if (decision == null || string.IsNullOrWhiteSpace(decision.agent_id))
                    {
                        continue;
                    }

                    decisionByAgentId[decision.agent_id.Trim()] = decision;
                }
            }

            for (var i = 0; i < requestAgents.Count; i++)
            {
                var agent = requestAgents[i];
                if (agent == null || string.IsNullOrWhiteSpace(agent.agent_id))
                {
                    continue;
                }

                var agentId = agent.agent_id.Trim();
                decisionTranscriptLines.Add($"[Agent: {agentId}]");
                decisionTranscriptLines.Add($"  CrowdType: {agent.crowd_type ?? "?"} | Zone: {agent.current_zone_id ?? "?"} | Stay: {agent.stay_duration_ms}ms | Patience: {agent.patience_ratio:F2}");
                decisionTranscriptLines.Add($"  Dilemma: {agent.dilemma_summary ?? "?"}");
                decisionTranscriptLines.Add($"  AlgorithmRec: {agent.algorithm_recommendation ?? "?"}");

                if (agent.available_exits != null && agent.available_exits.Length > 0)
                {
                    decisionTranscriptLines.Add($"  Exits ({agent.available_exits.Length}):");
                    for (var j = 0; j < agent.available_exits.Length; j++)
                    {
                        var exit = agent.available_exits[j];
                        if (exit == null)
                        {
                            continue;
                        }

                        decisionTranscriptLines.Add($"    [{j + 1}] {exit.exit_id} → {exit.target_zone_id} {exit.nl_description ?? "?"}");
                    }
                }

                DecisionResultItemDto matchedDecision;
                if (decisionByAgentId.TryGetValue(agentId, out matchedDecision) && matchedDecision != null)
                {
                    var sourceLabel = string.IsNullOrWhiteSpace(matchedDecision.decision_source)
                        ? "?"
                        : matchedDecision.decision_source;
                    decisionTranscriptLines.Add($"  [{sourceLabel}] exit={matchedDecision.chosen_exit_id ?? "?"} confidence={matchedDecision.confidence:F2}");
                    decisionTranscriptLines.Add($"  Reasoning: {matchedDecision.reasoning ?? "?"}");
                }
                else
                {
                    decisionTranscriptLines.Add("  [no_match] no matching decision in response");
                }

                decisionTranscriptLines.Add(string.Empty);
            }

            decisionTranscriptLines.Add("---");
            decisionTranscriptLines.Add(string.Empty);
        }

        private bool TryAssignAgentToFixedPath(string agentId, string crowdType)
        {
            if (string.IsNullOrWhiteSpace(agentId) || string.IsNullOrWhiteSpace(crowdType))
            {
                return false;
            }

            // 检查 agent 是否已被分配覆写模式
            DecisionAgentPoint existingPoint;
            if (reportedAgentPoints.TryGetValue(agentId, out existingPoint)
                && existingPoint != null
                && existingPoint.OverrideMode != DecisionOverrideMode.Normal)
            {
                return true; // 已分配
            }

            ResolveTopologySceneConfig();
            var configs = topologySceneConfig != null ? topologySceneConfig.FixedPathConfigs : Array.Empty<FixedPathConfigSO>();
            if (configs.Length <= 0)
            {
                return false;
            }

            for (var i = 0; i < configs.Length; i++)
            {
                var config = configs[i];
                if (config == null || !config.IsValid())
                {
                    continue;
                }

                if (config.mode == DecisionOverrideMode.Normal)
                {
                    continue; // Normal 模式走正常决策流程
                }

                if (!string.Equals(config.crowdType.Trim(), crowdType.Trim(), StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                var normalizedType = config.crowdType.Trim();
                int assigned;
                fixedPathAssignedCountByType.TryGetValue(normalizedType, out assigned);

                if (assigned >= config.count)
                {
                    return false; // 该类型已满额
                }

                fixedPathAssignedCountByType[normalizedType] = assigned + 1;

                // 将配置写入 agent 本身
                DecisionAgentPoint point;
                if (reportedAgentPoints.TryGetValue(agentId, out point) && point != null)
                {
                    point.ApplyOverrideConfig(config.mode, config.zoneIds);
                }

                return true;
            }

            return false;
        }

        private async UniTask<List<DecisionResultItemDto>> ApplyFixedPathDecisionsAsync(
            List<DecisionAgentRequestDto> requestAgents,
            Dictionary<string, DecisionAgentPoint> requestAgentMap,
            bool removeFromBatch = true)
        {
            var fixedPathDecisions = new List<DecisionResultItemDto>();
            if (requestAgents == null || requestAgents.Count <= 0)
            {
                return fixedPathDecisions;
            }

            ResolveTopologySceneConfig();
            var configs = topologySceneConfig != null ? topologySceneConfig.FixedPathConfigs : Array.Empty<FixedPathConfigSO>();
            if (configs.Length <= 0)
            {
                return fixedPathDecisions;
            }

            // 找出固定路径 agent
            var fixedPathAgents = new List<DecisionAgentRequestDto>();
            for (var i = requestAgents.Count - 1; i >= 0; i--)
            {
                var agent = requestAgents[i];
                if (agent == null || string.IsNullOrWhiteSpace(agent.agent_id))
                {
                    continue;
                }

                var agentId = agent.agent_id.Trim();
                var crowdType = (agent.crowd_type ?? string.Empty).Trim();

                if (!TryAssignAgentToFixedPath(agentId, crowdType))
                {
                    continue;
                }

                fixedPathAgents.Add(agent);
                if (removeFromBatch)
                {
                    requestAgents.RemoveAt(i);
                }
            }

            if (fixedPathAgents.Count <= 0)
            {
                return fixedPathDecisions;
            }

            // 为每个覆写 agent 构建决策（利用 agent 自身的 OverrideMode 和 OverrideZoneIds）
            for (var i = 0; i < fixedPathAgents.Count; i++)
            {
                var agent = fixedPathAgents[i];
                var agentId = agent.agent_id.Trim();

                DecisionAgentPoint point;
                if (!requestAgentMap.TryGetValue(agentId, out point)
                    && !reportedAgentPoints.TryGetValue(agentId, out point))
                {
                    continue;
                }

                if (point == null)
                {
                    continue;
                }

                string nextZoneId;
                string reasoning;
                if (!point.TryResolveOverrideNextZone(out nextZoneId, out reasoning))
                {
                    continue;
                }

                // 根据目标区域查找实际出口 ID，用于后续寻路
                var currentZoneId = NormalizeAgentId(agent.current_zone_id);
                var chosenExitId = ResolveExitIdToZone(currentZoneId, nextZoneId) ?? nextZoneId;

                var decision = new DecisionResultItemDto
                {
                    agent_id = agentId,
                    chosen_exit_id = chosenExitId,
                    decision_source = point.OverrideMode == DecisionOverrideMode.Random ? "random" : "fixed_path",
                    reasoning = reasoning,
                    confidence = 1f,
                };
                fixedPathDecisions.Add(decision);
            }

            if (fixedPathDecisions.Count > 0)
            {
                await ApplyDecisionResultsAsync(fixedPathDecisions.ToArray(), requestAgentMap);
                // LLM Only 模式下覆写 agent 仍会走 LLM，transcript 由 LLM 响应阶段输出
                if (removeFromBatch)
                {
                    AppendDecisionTranscript("fixed_path_" + BuildBatchId(), fixedPathAgents, fixedPathDecisions.ToArray());
                }
            }

            return fixedPathDecisions;
        }

        /// <summary>
        /// LLM Only 模式下，对已分配覆写模式的 agent，将其覆写出口的 algorithm_score 设为 1，
        /// 其余出口设为 0，并设置 algorithm_recommendation。
        /// </summary>
        private void ApplyOverrideExitScoreOverrides(
            List<DecisionAgentRequestDto> requestAgents,
            Dictionary<string, DecisionAgentPoint> requestAgentMap)
        {
            if (requestAgents == null || requestAgents.Count <= 0)
            {
                return;
            }

            for (var a = 0; a < requestAgents.Count; a++)
            {
                var agent = requestAgents[a];
                if (agent?.available_exits == null || agent.available_exits.Length <= 0)
                {
                    continue;
                }

                var agentId = NormalizeAgentId(agent.agent_id);
                DecisionAgentPoint point;
                if (!requestAgentMap.TryGetValue(agentId, out point)
                    && !reportedAgentPoints.TryGetValue(agentId, out point))
                {
                    continue;
                }

                if (point == null || point.OverrideMode == DecisionOverrideMode.Normal)
                {
                    continue;
                }

                // 解析覆写出口
                string nextZoneId;
                string reasoning;
                if (!point.TryResolveOverrideNextZone(out nextZoneId, out reasoning))
                {
                    continue;
                }

                var currentZoneId = NormalizeAgentId(agent.current_zone_id);
                var overrideExitId = ResolveExitIdToZone(currentZoneId, nextZoneId) ?? nextZoneId;

                // 设置 algorithm_recommendation
                agent.algorithm_recommendation = overrideExitId;

                // 强制评分：覆写出口 = 1，其余 = 0
                for (var e = 0; e < agent.available_exits.Length; e++)
                {
                    var exit = agent.available_exits[e];
                    if (exit == null)
                    {
                        continue;
                    }

                    exit.algorithm_score = string.Equals(
                        NormalizeAgentId(exit.exit_id),
                        NormalizeAgentId(overrideExitId),
                        StringComparison.OrdinalIgnoreCase) ? 1f : 0f;
                }
            }
        }

        [ContextMenu("Dispatch Decision Batch Now")]
        public void DispatchNow()
        {
            DispatchNowAsync().Forget();
        }

        [ContextMenu("Collect Agent Cache Now")]
        public void CollectAgentCacheNow()
        {
            BeginCollectPass();
            ProcessCollectPassTimeSlice(true);
            if (!collectPassInProgress)
            {
                nextCollectAtTime = Time.unscaledTime + Mathf.Max(0.2f, collectIntervalSeconds);
            }
        }

        public void ApplyLocalDecisionScoringConfig(LocalDecisionScoringConfig config)
        {
            if (config == null)
            {
                return;
            }

            if (localDecisionScoring == null)
            {
                localDecisionScoring = new LocalDecisionScoringConfig();
            }

            localDecisionScoring.OverwriteFrom(config);
        }

        public LocalDecisionScoringConfig GetLocalDecisionScoringConfigSnapshot()
        {
            if (localDecisionScoring == null)
            {
                return new LocalDecisionScoringConfig();
            }

            return localDecisionScoring.Clone();
        }

        public void ApplyCrowdTypeDecisionScoringOverrides(CrowdTypeDecisionScoringOverride[] overrides)
        {
            if (overrides == null || overrides.Length <= 0)
            {
                crowdTypeDecisionScoringOverrides = Array.Empty<CrowdTypeDecisionScoringOverride>();
                return;
            }

            var cloned = new List<CrowdTypeDecisionScoringOverride>(overrides.Length);
            for (var i = 0; i < overrides.Length; i++)
            {
                var item = overrides[i];
                if (item == null)
                {
                    continue;
                }

                cloned.Add(item.Clone());
            }

            crowdTypeDecisionScoringOverrides = cloned.ToArray();
        }

        public CrowdTypeDecisionScoringOverride[] GetCrowdTypeDecisionScoringOverridesSnapshot()
        {
            if (crowdTypeDecisionScoringOverrides == null || crowdTypeDecisionScoringOverrides.Length <= 0)
            {
                return Array.Empty<CrowdTypeDecisionScoringOverride>();
            }

            var cloned = new List<CrowdTypeDecisionScoringOverride>(crowdTypeDecisionScoringOverrides.Length);
            for (var i = 0; i < crowdTypeDecisionScoringOverrides.Length; i++)
            {
                var item = crowdTypeDecisionScoringOverrides[i];
                if (item == null)
                {
                    continue;
                }

                cloned.Add(item.Clone());
            }

            return cloned.ToArray();
        }

        public async UniTask DispatchNowAsync()
        {
            if (!CanStartDispatchRequest())
            {
                return;
            }

            var hadInFlightBeforeDispatch = inFlightRequestCount > 0;
            inFlightRequestCount++;
            requestInFlight = inFlightRequestCount > 0;

            var hasSuccessfulDispatch = false;
            var dispatchBuffer = AcquireDispatchWorkBuffer();
            var reservedAgentIds = dispatchBuffer.reservedAgentIds;
            var requestAgents = dispatchBuffer.requestAgents;
            var requestAgentMap = dispatchBuffer.requestAgentMap;
            try
            {
                var manager = ResolveExperimentManager();
                var experimentId = ResolveExperimentId(manager);
                var backendCooldownActive = IsBackendCooldownActive();
                var runClientSimulation = dispatchMode == DecisionDispatchMode.LocalOnly
                    || (dispatchMode == DecisionDispatchMode.HybridPreferLlm && hadInFlightBeforeDispatch)
                    || (dispatchMode == DecisionDispatchMode.HybridPreferLlm
                        && hybridPreferLocalDuringBackendCooldown
                        && backendCooldownActive);
                var requiresBackendDispatch = dispatchMode == DecisionDispatchMode.LlmOnly
                    || dispatchMode == DecisionDispatchMode.HybridPreferLlm;

                if (dispatchMode == DecisionDispatchMode.LlmOnly && backendCooldownActive)
                {
                    var waitSeconds = Mathf.Max(0f, backendCooldownUntilTime - Time.unscaledTime);
                    lastDispatchSummary = "Decision batch skipped: backend cooldown active for "
                        + waitSeconds.ToString("0.00")
                        + "s.";
                    LogSummary();
                    return;
                }

                if (requiresBackendDispatch && !runClientSimulation && string.IsNullOrWhiteSpace(experimentId))
                {
                    LogDetailedDispatchState(
                        "skip_no_experiment_id",
                        experimentId,
                        runClientSimulation,
                        requiresBackendDispatch,
                        null,
                        string.Empty,
                        true);
                    lastDispatchSummary = "Decision batch skipped: experiment id is empty.";
                    LogSummary();
                    return;
                }

                if (requiresBackendDispatch && !runClientSimulation && onlyWhenExperimentRunning)
                {
                    var runningExperimentId = manager != null ? manager.RunningExperimentId : string.Empty;
                    if (string.IsNullOrWhiteSpace(runningExperimentId)
                        || !string.Equals(runningExperimentId, experimentId, StringComparison.Ordinal))
                    {
                        LogDetailedDispatchState(
                            "skip_experiment_not_running",
                            experimentId,
                            runClientSimulation,
                            requiresBackendDispatch,
                            null,
                            "running_experiment_id=" + runningExperimentId,
                            true);
                        lastDispatchSummary = "Decision batch skipped: experiment is not running.";
                        LogSummary();
                        return;
                    }
                }

                if (collectFromAgentReports)
                {
                    CollectRequestAgentsFromReportCache(requestAgents, requestAgentMap, reservedAgentIds, dispatchBuffer);
                }

                if (requestAgents.Count <= 0 && allowFallbackCollectFromScene)
                {
                    CollectRequestAgentsFromScene(requestAgents, requestAgentMap, reservedAgentIds, dispatchBuffer);
                }

                RandomizeAndClampBatchRequests(requestAgents);

                // 固定路径 agent：根据模式处理
                if (dispatchMode == DecisionDispatchMode.LlmOnly)
                {
                    // LLM Only：保留 agent 在批次中，但立即应用覆写决策，并强制算法评分为覆写出口
                    await ApplyFixedPathDecisionsAsync(requestAgents, requestAgentMap, removeFromBatch: false);
                    ApplyOverrideExitScoreOverrides(requestAgents, requestAgentMap);
                }
                else
                {
                    // 其他模式：覆写 agent 直接决策，从批次中移除
                    await ApplyFixedPathDecisionsAsync(requestAgents, requestAgentMap);
                }

                var minBatchExclusive = ResolveMinBatchCountExclusive();
                if (requestAgents.Count > 0 && requestAgents.Count <= minBatchExclusive)
                {
                    lastDispatchSummary = "Decision batch skipped: candidate count " + requestAgents.Count
                        + " is not greater than min batch count " + minBatchExclusive
                        + " (strict rule: min < count < max).";
                    LogSummary();
                    return;
                }

                LogDetailedDispatchState(
                    "prepared_candidates",
                    experimentId,
                    runClientSimulation,
                    requiresBackendDispatch,
                    requestAgents,
                    string.Empty,
                    false);

                if (requestAgents.Count <= 0)
                {
                    LogDetailedDispatchState(
                        "skip_no_candidates",
                        experimentId,
                        runClientSimulation,
                        requiresBackendDispatch,
                        requestAgents,
                        "invalid_summary=" + BuildInvalidReasonSummary(),
                        true);
                    RefreshReportCacheStats();
                    if (collectFromAgentReports)
                    {
                        var invalidCount = Math.Max(0, cachedReportCount - cachedValidReportCount);
                        lastInvalidReasonSummary = BuildInvalidReasonSummary();
                        lastDispatchSummary = "Decision batch skipped: no reported agents require decision."
                            + " cache=" + cachedReportCount
                            + ", valid=" + cachedValidReportCount
                            + ", pending=" + cachedPendingDecisionCount
                            + ", invalid=" + invalidCount;
                        if (!string.IsNullOrWhiteSpace(lastInvalidReasonSummary))
                        {
                            lastDispatchSummary += ", reasons=" + lastInvalidReasonSummary;
                        }
                    }
                    else
                    {
                        lastDispatchSummary = "Decision batch skipped: no agents require decision.";
                        lastInvalidReasonSummary = string.Empty;
                    }

                    LogSummary();
                    return;
                }

                if (runClientSimulation)
                {
                    var dispatchReason = dispatchMode == DecisionDispatchMode.LocalOnly
                        ? "local_only_mode"
                        : (backendCooldownActive ? "backend_cooldown_fallback" : "llm_in_flight_fallback");
                    LogDetailedDispatchState(
                        "dispatch_local",
                        experimentId,
                        runClientSimulation,
                        requiresBackendDispatch,
                        requestAgents,
                        "reason=" + dispatchReason,
                        true);
                    var localBatchId = BuildBatchId();
                    var localDecisions = await ExecuteClientSimulationDecisionBatch(
                        localBatchId,
                        requestAgents,
                        requestAgentMap,
                        dispatchReason,
                        dispatchBuffer);
                    AppendDecisionTranscript(localBatchId, requestAgents, localDecisions);
                    EmitBatchBundleLog(
                        localBatchId,
                        experimentId,
                        "local",
                        dispatchReason,
                        requestAgents,
                        localDecisions,
                        true,
                        string.Empty,
                        lastSuccessCount,
                        lastFallbackCount);
                    hasSuccessfulDispatch = true;
                    return;
                }

                LogDetailedDispatchState(
                    "dispatch_llm",
                    experimentId,
                    runClientSimulation,
                    requiresBackendDispatch,
                    requestAgents,
                    string.Empty,
                    true);

                // 用实时人群分布重新富化出口描述和算法评分
                EnsureTopologyLookupCache();
                EnsureCrowdTypeAttractionLookupCache();
                var llmPopMap = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
                BuildCurrentZonePopulationMap(llmPopMap);
                LocalCrowdDecisionEngine.EnrichExitOptionsWithRuntimePopulation(
                    requestAgents,
                    llmPopMap,
                    topologyZoneCapacityByIdCache,
                    topologyZoneTypeByIdCache,
                    topologyExitByIdCache);
                LocalCrowdDecisionEngine.EnrichAlgorithmRecommendations(
                    requestAgents,
                    llmPopMap,
                    new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase),
                    topologyZoneCapacityByIdCache,
                    topologyZoneTypeByIdCache,
                    topologyExitByIdCache,
                    crowdTypeAttractionWeightsByTypeIdCache,
                    localDecisionScoring);

                var request = new DecisionBatchRequestDto
                {
                    batch_id = BuildBatchId(),
                    simulation_time_ms = Mathf.Max(0, Mathf.RoundToInt(Time.timeSinceLevelLoad * 1000f)),
                    agents = requestAgents.ToArray(),
                };
                var path = "/api/v1/experiments/" + experimentId + "/decisions";
                var requestJson = JsonUtility.ToJson(request);
                var raw = await apiClient.PostJson(path, requestJson, experimentId);

                ApiResponseMeta meta;
                string errorMessage;
                if (!TryEnsureApiSuccess(raw, "POST /api/v1/experiments/{id}/decisions", out meta, out errorMessage))
                {
                    RegisterBackendCooldown(raw, meta);
                    lastDispatchSummary = errorMessage;
                    var cooldownRemainSeconds = Mathf.Max(0f, backendCooldownUntilTime - Time.unscaledTime);
                    if (cooldownRemainSeconds > 0f)
                    {
                        lastDispatchSummary += " | unity_backoff=" + cooldownRemainSeconds.ToString("0.00") + "s";
                    }
                    EmitBatchBundleLog(
                        request.batch_id,
                        experimentId,
                        "llm",
                        "llm_dispatch_failed",
                        requestAgents,
                        null,
                        false,
                        errorMessage,
                        0,
                        requestAgents.Count);
                    LogSummary(true);
                    return;
                }

                var response = JsonUtility.FromJson<DecisionBatchResponseDto>(raw.Body);
                if (response == null || response.data == null)
                {
                    lastDispatchSummary = "Decision batch failed: response is invalid.";
                    EmitBatchBundleLog(
                        request.batch_id,
                        experimentId,
                        "llm",
                        "llm_response_invalid",
                        requestAgents,
                        null,
                        false,
                        lastDispatchSummary,
                        0,
                        requestAgents.Count);
                    LogSummary(true);
                    return;
                }

                await ApplyDecisionResultsAsync(response.data.decisions, requestAgentMap);

                AppendDecisionTranscript(request.batch_id, requestAgents, response.data.decisions);

                lastBatchId = response.data.batch_id;
                lastRequestedAgentCount = requestAgents.Count;
                lastResponseDecisionCount = response.data.decisions != null ? response.data.decisions.Length : 0;
                lastSuccessCount = response.data.success_count;
                lastFallbackCount = response.data.fallback_count;
                lastDispatchAtUnixMs = RequestIdUtility.GetUnixTimestampMs();
                lastInvalidReasonSummary = string.Empty;

                lastDispatchSummary = "Decision batch sent. batch_id=" + lastBatchId
                    + ", requested=" + lastRequestedAgentCount
                    + ", returned=" + lastResponseDecisionCount
                    + ", success=" + lastSuccessCount
                    + ", fallback=" + lastFallbackCount;
                LogSummary();
                LogDetailedDispatchState(
                    "llm_response_applied",
                    experimentId,
                    false,
                    true,
                    requestAgents,
                    "batch_id=" + lastBatchId + ", fallback_count=" + lastFallbackCount,
                    true);
                EmitBatchBundleLog(
                    lastBatchId,
                    experimentId,
                    "llm",
                    "llm_response_applied",
                    requestAgents,
                    response.data.decisions,
                    true,
                    string.Empty,
                    lastSuccessCount,
                    lastFallbackCount);
                ResetBackendCooldownState();
                hasSuccessfulDispatch = true;
            }
            catch (Exception exception)
            {
                lastDispatchSummary = "Decision batch exception: " + exception.Message;
                if (!(enableBatchBundleLog && suppressLegacyLogsWhenBatchBundle))
                {
                    Debug.LogException(exception);
                }
                LogDetailedDispatchState(
                    "dispatch_exception",
                    string.Empty,
                    false,
                    dispatchMode != DecisionDispatchMode.LocalOnly,
                    null,
                    "exception=" + exception.Message,
                    true);
                EmitBatchBundleLog(
                    string.Empty,
                    string.Empty,
                    "unknown",
                    "dispatch_exception",
                    null,
                    null,
                    false,
                    exception.Message,
                    0,
                    0);
            }
            finally
            {
                ReleaseReservedAgentIds(reservedAgentIds);
                ReleaseDispatchWorkBuffer(dispatchBuffer);

                inFlightRequestCount = Math.Max(0, inFlightRequestCount - 1);
                requestInFlight = inFlightRequestCount > 0;

                if (!ensureSingleInFlightRequest
                    && hasSuccessfulDispatch
                    && HasDispatchableCandidates()
                    && CanStartDispatchRequest())
                {
                    nextDispatchAtTime = Time.unscaledTime;
                }
                else
                {
                    nextDispatchAtTime = Time.unscaledTime + Mathf.Max(0.2f, decisionIntervalSeconds);
                }

                if (IsBackendCooldownActive())
                {
                    nextDispatchAtTime = Mathf.Max(nextDispatchAtTime, backendCooldownUntilTime);
                }
            }
        }

        private int ResolveMaxBatchCountExclusive()
        {
            return Mathf.Max(2, maxAgentsPerBatch);
        }

        private int ResolveMinBatchCountExclusive()
        {
            var maxExclusive = ResolveMaxBatchCountExclusive();
            return Mathf.Clamp(minAgentsPerBatch, 0, maxExclusive - 2);
        }

        private void RandomizeAndClampBatchRequests(List<DecisionAgentRequestDto> requestAgents)
        {
            if (requestAgents == null || requestAgents.Count <= 0)
            {
                return;
            }

            if (randomizeBatchCandidateOrder && requestAgents.Count > 1)
            {
                var shuffleSalt = BuildDispatchShuffleSalt(RequestIdUtility.GetUnixTimestampMs());
                requestAgents.Sort((left, right) =>
                {
                    var leftAgentId = NormalizeAgentId(left != null ? left.agent_id : string.Empty);
                    var rightAgentId = NormalizeAgentId(right != null ? right.agent_id : string.Empty);
                    return CompareRequestShuffle(left, leftAgentId, right, rightAgentId, shuffleSalt);
                });
            }

            var maxExclusive = ResolveMaxBatchCountExclusive();
            var maxAllowedCount = Mathf.Max(1, maxExclusive - 1);
            if (requestAgents.Count > maxAllowedCount)
            {
                requestAgents.RemoveRange(maxAllowedCount, requestAgents.Count - maxAllowedCount);
            }
        }

        public void RegisterAgentReporter(DecisionAgentPoint point)
        {
            if (point == null)
            {
                return;
            }

            var agentId = ResolveEffectiveAgentId(point, null);
            if (string.IsNullOrWhiteSpace(agentId))
            {
                return;
            }

            reportedAgentPoints[agentId] = point;
            resolvedDecisionAgentsCacheDirty = true;
        }

        public void UnregisterAgentReporter(DecisionAgentPoint point)
        {
            if (point == null)
            {
                return;
            }

            var agentId = ResolveEffectiveAgentId(point, null);
            if (string.IsNullOrWhiteSpace(agentId))
            {
                return;
            }

            DecisionAgentPoint existing;
            if (reportedAgentPoints.TryGetValue(agentId, out existing)
                && existing == point)
            {
                reportedAgentPoints.Remove(agentId);
                resolvedDecisionAgentsCacheDirty = true;
            }

            reportedAgentCache.Remove(agentId);
            RefreshReportCacheStats();
        }

        public void ReportAgentState(
            DecisionAgentPoint point,
            DecisionAgentRequestDto request,
            bool needsDecision,
            bool isValid,
            string error)
        {
            if (point == null)
            {
                return;
            }

            var agentId = ResolveEffectiveAgentId(point, request);
            if (string.IsNullOrWhiteSpace(agentId))
            {
                return;
            }

            if (request != null)
            {
                request.agent_id = agentId;
            }

            reportedAgentPoints[agentId] = point;
            reportedAgentCache[agentId] = new DecisionAgentReportCacheItem
            {
                agent_id = agentId,
                request = request,
                needs_llm_decision = needsDecision,
                is_valid = isValid,
                error = string.IsNullOrWhiteSpace(error) ? string.Empty : error.Trim(),
                reported_at_unix_ms = RequestIdUtility.GetUnixTimestampMs(),
                source_instance_id = point.GetInstanceID(),
            };

            RefreshReportCacheStats();
        }

        public void SetAllAgentsVisibleInScene(bool visible)
        {
            foreach (var pair in reportedAgentPoints)
            {
                var point = pair.Value;
                if (point != null)
                {
                    point.SetAgentRenderersVisible(false);
                }
            }
        }

        private bool CanStartDispatchRequest()
        {
            if (dispatchMode == DecisionDispatchMode.HybridPreferLlm && inFlightRequestCount > 0)
            {
                // Allow local fallback cycles while an LLM request is still in-flight.
                return true;
            }

            if (dispatchMode == DecisionDispatchMode.LlmOnly && IsBackendCooldownActive())
            {
                return false;
            }

            if (ensureSingleInFlightRequest)
            {
                return inFlightRequestCount <= 0;
            }

            return inFlightRequestCount < Math.Max(1, maxConcurrentRequestsWhenUnlocked);
        }

        private bool IsBackendCooldownActive()
        {
            return Time.unscaledTime < backendCooldownUntilTime;
        }

        private void RegisterBackendCooldown(BackendApiRawResponse raw, ApiResponseMeta meta)
        {
            if (raw == null)
            {
                return;
            }

            var statusCode = raw.StatusCode;
            var body = raw.Body ?? string.Empty;
            var message = meta != null ? (meta.message ?? string.Empty) : string.Empty;
            var isInflightConflict = statusCode == 409
                && (body.IndexOf("decision_request_inflight", StringComparison.OrdinalIgnoreCase) >= 0
                    || message.IndexOf("decision_request_inflight", StringComparison.OrdinalIgnoreCase) >= 0
                    || body.IndexOf("\"code\":2002", StringComparison.OrdinalIgnoreCase) >= 0);

            if (isInflightConflict)
            {
                consecutiveBackendConflictCount = Mathf.Max(1, consecutiveBackendConflictCount + 1);
                consecutiveBackendServerErrorCount = 0;
                var delay = ComputeCooldownDelaySeconds(consecutiveBackendConflictCount);
                backendCooldownUntilTime = Mathf.Max(backendCooldownUntilTime, Time.unscaledTime + delay);
                return;
            }

            if (statusCode >= 500)
            {
                consecutiveBackendServerErrorCount = Mathf.Max(1, consecutiveBackendServerErrorCount + 1);
                consecutiveBackendConflictCount = 0;
                var delay = ComputeCooldownDelaySeconds(consecutiveBackendServerErrorCount);
                backendCooldownUntilTime = Mathf.Max(backendCooldownUntilTime, Time.unscaledTime + delay);
            }
        }

        private float ComputeCooldownDelaySeconds(int consecutiveCount)
        {
            var baseDelay = Mathf.Max(0f, backendErrorBaseCooldownSeconds);
            var maxDelay = Mathf.Max(baseDelay, backendErrorMaxCooldownSeconds);
            if (baseDelay <= 0f)
            {
                return 0f;
            }

            var exponent = Mathf.Clamp(consecutiveCount - 1, 0, 8);
            var delay = baseDelay * Mathf.Pow(2f, exponent);
            return Mathf.Min(delay, maxDelay);
        }

        private void ResetBackendCooldownState()
        {
            consecutiveBackendConflictCount = 0;
            consecutiveBackendServerErrorCount = 0;
            backendCooldownUntilTime = 0f;
        }

        private void ReleaseReservedAgentIds(List<string> reservedAgentIds)
        {
            if (reservedAgentIds == null || reservedAgentIds.Count <= 0)
            {
                return;
            }

            for (var i = 0; i < reservedAgentIds.Count; i++)
            {
                inFlightAgentIds.Remove(reservedAgentIds[i]);
            }
        }

        private bool HasDispatchableCandidates()
        {
            if (collectFromAgentReports && HasDispatchableCacheCandidates())
            {
                return true;
            }

            if (!allowFallbackCollectFromScene)
            {
                return false;
            }

            var candidates = ResolveDecisionAgents();
            for (var i = 0; i < candidates.Length; i++)
            {
                var candidate = candidates[i];
                if (candidate == null || !candidate.NeedsLlmDecision)
                {
                    continue;
                }

                var agentId = ResolveEffectiveAgentId(candidate, null);
                if (string.IsNullOrWhiteSpace(agentId)
                    || inFlightAgentIds.Contains(agentId))
                {
                    continue;
                }

                DecisionAgentRequestDto request;
                string error;
                if (candidate.TryBuildDecisionRequest(out request, out error) && request != null)
                {
                    return true;
                }
            }

            return false;
        }

        private bool HasDispatchableCacheCandidates()
        {
            foreach (var pair in reportedAgentCache)
            {
                var report = pair.Value;
                if (report == null
                    || !report.is_valid
                    || !report.needs_llm_decision
                    || report.request == null
                    || string.IsNullOrWhiteSpace(report.request.agent_id))
                {
                    continue;
                }

                var agentId = NormalizeAgentId(report.request.agent_id);
                if (string.IsNullOrWhiteSpace(agentId)
                    || inFlightAgentIds.Contains(agentId))
                {
                    continue;
                }

                return true;
            }

            return false;
        }

        private void BeginCollectPass()
        {
            collectActiveAgentIdsScratch.Clear();
            collectPassIndex = 0;
            collectPassInProgress = true;
            ResolveDecisionAgents();
        }

        private void ProcessCollectPassTimeSlice(bool forceComplete = false)
        {
            if (!collectPassInProgress)
            {
                return;
            }

            var candidates = resolvedDecisionAgentsCache ?? Array.Empty<DecisionAgentPoint>();
            var nowUnixMs = RequestIdUtility.GetUnixTimestampMs();
            var startTime = Time.realtimeSinceStartupAsDouble;
            var timeBudgetSeconds = Mathf.Max(0.2f, collectTimeSliceBudgetMs) / 1000f;

            for (; collectPassIndex < candidates.Length; collectPassIndex++)
            {
                var candidate = candidates[collectPassIndex];
                if (candidate == null)
                {
                    continue;
                }

                DecisionAgentRequestDto request;
                string error;
                var isValid = candidate.TryBuildDecisionRequest(out request, out error);
                var agentId = ResolveEffectiveAgentId(candidate, request);
                if (string.IsNullOrWhiteSpace(agentId))
                {
                    continue;
                }

                if (request != null)
                {
                    request.agent_id = agentId;
                }

                collectActiveAgentIdsScratch.Add(agentId);
                reportedAgentPoints[agentId] = candidate;

                var reportedAtUnixMs = nowUnixMs;
                DecisionAgentReportCacheItem existing;
                if (reportedAgentCache.TryGetValue(agentId, out existing)
                    && existing != null
                    && IsDispatchableReport(existing)
                    && IsDispatchableCandidate(candidate.NeedsLlmDecision, isValid, request))
                {
                    // Keep original enqueue time while the agent stays pending,
                    // so periodic collect does not break FIFO order.
                    reportedAtUnixMs = existing.reported_at_unix_ms;
                }

                reportedAgentCache[agentId] = new DecisionAgentReportCacheItem
                {
                    agent_id = agentId,
                    request = request,
                    needs_llm_decision = candidate.NeedsLlmDecision,
                    is_valid = isValid,
                    error = string.IsNullOrWhiteSpace(error) ? string.Empty : error.Trim(),
                    reported_at_unix_ms = reportedAtUnixMs,
                    source_instance_id = candidate.GetInstanceID(),
                };

                if (!forceComplete
                    && Time.realtimeSinceStartupAsDouble - startTime >= timeBudgetSeconds)
                {
                    collectPassIndex++;
                    return;
                }
            }

            RemoveStaleCollectedAgents(collectActiveAgentIdsScratch);
            RefreshReportCacheStats();
            collectPassInProgress = false;
            collectPassIndex = 0;
            nextCollectAtTime = Time.unscaledTime + Mathf.Max(0.2f, collectIntervalSeconds);
        }

        private void CollectAgentStatesToCache()
        {
            BeginCollectPass();
            ProcessCollectPassTimeSlice(true);
        }

        private void RemoveStaleCollectedAgents(HashSet<string> activeAgentIds)
        {
            if (activeAgentIds == null)
            {
                return;
            }

            if (activeAgentIds.Count <= 0)
            {
                reportedAgentCache.Clear();
                reportedAgentPoints.Clear();
                inFlightAgentIds.Clear();
                return;
            }

            staleAgentIdsScratch.Clear();
            foreach (var pair in reportedAgentCache)
            {
                if (!activeAgentIds.Contains(pair.Key))
                {
                    staleAgentIdsScratch.Add(pair.Key);
                }
            }

            for (var i = 0; i < staleAgentIdsScratch.Count; i++)
            {
                var staleAgentId = staleAgentIdsScratch[i];
                reportedAgentCache.Remove(staleAgentId);
                inFlightAgentIds.Remove(staleAgentId);
            }

            staleAgentIdsScratch.Clear();
            foreach (var pair in reportedAgentPoints)
            {
                if (!activeAgentIds.Contains(pair.Key))
                {
                    staleAgentIdsScratch.Add(pair.Key);
                }
            }

            for (var i = 0; i < staleAgentIdsScratch.Count; i++)
            {
                reportedAgentPoints.Remove(staleAgentIdsScratch[i]);
                resolvedDecisionAgentsCacheDirty = true;
            }

            staleAgentIdsScratch.Clear();
        }

        private ExperimentManager ResolveExperimentManager()
        {
            if (experimentManager != null)
            {
                return experimentManager;
            }

            return ExperimentManager.ActiveInstance;
        }

        private string ResolveExperimentId(ExperimentManager manager)
        {
            if (!string.IsNullOrWhiteSpace(explicitExperimentId))
            {
                return explicitExperimentId.Trim();
            }

            if (manager == null)
            {
                return string.Empty;
            }

            if (!string.IsNullOrWhiteSpace(manager.RunningExperimentId))
            {
                return manager.RunningExperimentId.Trim();
            }

            return string.IsNullOrWhiteSpace(manager.LastCreatedExperimentId)
                ? string.Empty
                : manager.LastCreatedExperimentId.Trim();
        }

        private DecisionAgentPoint[] ResolveDecisionAgents()
        {
            if (autoCollectFromChildren && !useRegisteredAgentDiscovery)
            {
                var sceneAgents = FindObjectsOfType<DecisionAgentPoint>(includeInactiveAgents);
                if (sceneAgents != null && sceneAgents.Length > 0)
                {
                    return sceneAgents;
                }
            }

            if (!resolvedDecisionAgentsCacheDirty)
            {
                return resolvedDecisionAgentsCache;
            }

            resolvedDecisionAgentsScratch.Clear();
            resolvedDecisionAgentInstanceIdsScratch.Clear();

            if (decisionAgents != null)
            {
                for (var i = 0; i < decisionAgents.Length; i++)
                {
                    var point = decisionAgents[i];
                    if (point == null
                        || (!includeInactiveAgents && !point.isActiveAndEnabled)
                        || !resolvedDecisionAgentInstanceIdsScratch.Add(point.GetInstanceID()))
                    {
                        continue;
                    }

                    resolvedDecisionAgentsScratch.Add(point);
                }
            }

            foreach (var pair in reportedAgentPoints)
            {
                var point = pair.Value;
                if (point == null
                    || (!includeInactiveAgents && !point.isActiveAndEnabled)
                    || !resolvedDecisionAgentInstanceIdsScratch.Add(point.GetInstanceID()))
                {
                    continue;
                }

                resolvedDecisionAgentsScratch.Add(point);
            }

            resolvedDecisionAgentsCache = resolvedDecisionAgentsScratch.Count > 0
                ? resolvedDecisionAgentsScratch.ToArray()
                : Array.Empty<DecisionAgentPoint>();
            resolvedDecisionAgentsScratch.Clear();
            resolvedDecisionAgentInstanceIdsScratch.Clear();
            resolvedDecisionAgentsCacheDirty = false;

            return resolvedDecisionAgentsCache;
        }

        private void CollectRequestAgentsFromReportCache(
            List<DecisionAgentRequestDto> requestAgents,
            Dictionary<string, DecisionAgentPoint> requestAgentMap,
            List<string> reservedAgentIds,
            DispatchWorkBuffer dispatchBuffer)
        {
            if (requestAgents == null || requestAgentMap == null || reservedAgentIds == null || dispatchBuffer == null)
            {
                return;
            }

            var maxBatch = Mathf.Max(1, ResolveMaxBatchCountExclusive() - 1);
            var orderedReports = dispatchBuffer.orderedReports;
            orderedReports.Clear();
            foreach (var pair in reportedAgentCache)
            {
                var report = pair.Value;
                if (!IsDispatchableReport(report))
                {
                    continue;
                }

                orderedReports.Add(report);
            }

            var nowUnixMs = RequestIdUtility.GetUnixTimestampMs();
            var fairnessAgeWindowMs = (long)Mathf.Round(Mathf.Max(0.1f, randomizationFairnessAgeWindowSeconds) * 1000f);
            var shuffleSalt = BuildDispatchShuffleSalt(nowUnixMs);

            orderedReports.Sort((left, right) =>
            {
                return CompareReportOrderForBatch(left, right, nowUnixMs, fairnessAgeWindowMs, shuffleSalt);
            });

            var initialSelectedByEntry = dispatchBuffer.selectedInitialByEntry;
            initialSelectedByEntry.Clear();
            var initialSelectedCount = 0;

            for (var i = 0; i < orderedReports.Count; i++)
            {
                if (requestAgents.Count >= maxBatch)
                {
                    break;
                }

                var report = orderedReports[i];
                if (!IsDispatchableReport(report))
                {
                    continue;
                }

                var agentId = NormalizeAgentId(report.request.agent_id);
                if (requestAgentMap.ContainsKey(agentId)
                    || inFlightAgentIds.Contains(agentId))
                {
                    continue;
                }

                DecisionAgentPoint point;
                reportedAgentPoints.TryGetValue(agentId, out point);

                var isInitialDecisionCandidate = point != null && !point.HasReceivedFirstDecision;
                if (isInitialDecisionCandidate
                    && !CanSelectInitialDecisionCandidate(
                        report.request.current_zone_id,
                        initialSelectedByEntry,
                        initialSelectedCount))
                {
                    continue;
                }

                requestAgents.Add(report.request);
                inFlightAgentIds.Add(agentId);
                reservedAgentIds.Add(agentId);

                if (isInitialDecisionCandidate)
                {
                    MarkInitialDecisionCandidateSelected(
                        report.request.current_zone_id,
                        initialSelectedByEntry,
                        ref initialSelectedCount);
                }

                if (point != null)
                {
                    requestAgentMap[agentId] = point;
                }
            }

            orderedReports.Clear();
            initialSelectedByEntry.Clear();
        }

        private static bool IsDispatchableCandidate(bool needsLlmDecision, bool isValid, DecisionAgentRequestDto request)
        {
            if (!needsLlmDecision || !isValid || request == null)
            {
                return false;
            }

            return !string.IsNullOrWhiteSpace(request.agent_id);
        }

        private static bool IsDispatchableReport(DecisionAgentReportCacheItem report)
        {
            if (report == null
                || !report.is_valid
                || !report.needs_llm_decision
                || report.request == null
                || string.IsNullOrWhiteSpace(report.agent_id)
                || string.IsNullOrWhiteSpace(report.request.agent_id))
            {
                return false;
            }

            return true;
        }

        private void CollectRequestAgentsFromScene(
            List<DecisionAgentRequestDto> requestAgents,
            Dictionary<string, DecisionAgentPoint> requestAgentMap,
            List<string> reservedAgentIds,
            DispatchWorkBuffer dispatchBuffer)
        {
            if (requestAgents == null || requestAgentMap == null || reservedAgentIds == null || dispatchBuffer == null)
            {
                return;
            }

            var maxBatch = Mathf.Max(1, ResolveMaxBatchCountExclusive() - 1);
            var candidates = ResolveDecisionAgents();
            var sceneDispatchCandidates = dispatchBuffer.sceneDispatchCandidates;
            sceneDispatchCandidates.Clear();
            var initialSelectedByEntry = dispatchBuffer.selectedInitialByEntry;
            initialSelectedByEntry.Clear();
            var initialSelectedCount = 0;

            for (var i = 0; i < candidates.Length; i++)
            {
                var candidate = candidates[i];
                if (candidate == null || !candidate.NeedsLlmDecision)
                {
                    continue;
                }

                DecisionAgentRequestDto requestAgent;
                string error;
                if (!candidate.TryBuildDecisionRequest(out requestAgent, out error) || requestAgent == null)
                {
                    continue;
                }

                var agentId = ResolveEffectiveAgentId(candidate, requestAgent);
                if (requestAgentMap.ContainsKey(agentId)
                    || inFlightAgentIds.Contains(agentId))
                {
                    continue;
                }

                sceneDispatchCandidates.Add(new SceneDispatchCandidate
                {
                    Point = candidate,
                    Request = requestAgent,
                    AgentId = agentId,
                });
            }

            if (sceneDispatchCandidates.Count <= 0)
            {
                return;
            }

            if (randomizeBatchCandidateOrder)
            {
                var shuffleSalt = BuildDispatchShuffleSalt(RequestIdUtility.GetUnixTimestampMs());
                sceneDispatchCandidates.Sort((left, right) =>
                {
                    var byShuffle = CompareRequestShuffle(left.Request, left.AgentId, right.Request, right.AgentId, shuffleSalt);
                    if (byShuffle != 0)
                    {
                        return byShuffle;
                    }

                    return string.Compare(left.AgentId, right.AgentId, StringComparison.OrdinalIgnoreCase);
                });
            }

            for (var i = 0; i < sceneDispatchCandidates.Count; i++)
            {
                if (requestAgents.Count >= maxBatch)
                {
                    break;
                }

                var sceneCandidate = sceneDispatchCandidates[i];
                var candidate = sceneCandidate.Point;
                var requestAgent = sceneCandidate.Request;
                var agentId = sceneCandidate.AgentId;

                if (candidate == null || requestAgent == null || string.IsNullOrWhiteSpace(agentId))
                {
                    continue;
                }

                requestAgent.agent_id = agentId;

                if (requestAgentMap.ContainsKey(agentId)
                    || inFlightAgentIds.Contains(agentId))
                {
                    continue;
                }

                var isInitialDecisionCandidate = !candidate.HasReceivedFirstDecision;
                if (isInitialDecisionCandidate
                    && !CanSelectInitialDecisionCandidate(
                        requestAgent.current_zone_id,
                        initialSelectedByEntry,
                        initialSelectedCount))
                {
                    continue;
                }

                requestAgents.Add(requestAgent);
                requestAgentMap[agentId] = candidate;
                inFlightAgentIds.Add(agentId);
                reservedAgentIds.Add(agentId);

                if (isInitialDecisionCandidate)
                {
                    MarkInitialDecisionCandidateSelected(
                        requestAgent.current_zone_id,
                        initialSelectedByEntry,
                        ref initialSelectedCount);
                }
            }

            sceneDispatchCandidates.Clear();
            initialSelectedByEntry.Clear();
        }

        private int CompareReportOrderForBatch(
            DecisionAgentReportCacheItem left,
            DecisionAgentReportCacheItem right,
            long nowUnixMs,
            long fairnessAgeWindowMs,
            int shuffleSalt)
        {
            if (ReferenceEquals(left, right))
            {
                return 0;
            }

            if (left == null)
            {
                return 1;
            }

            if (right == null)
            {
                return -1;
            }

            if (randomizeBatchCandidateOrder)
            {
                var leftAgeMs = Math.Max(0L, nowUnixMs - left.reported_at_unix_ms);
                var rightAgeMs = Math.Max(0L, nowUnixMs - right.reported_at_unix_ms);
                if (leftAgeMs > rightAgeMs + fairnessAgeWindowMs)
                {
                    return -1;
                }

                if (rightAgeMs > leftAgeMs + fairnessAgeWindowMs)
                {
                    return 1;
                }

                var byShuffle = CompareRequestShuffle(left.request, left.agent_id, right.request, right.agent_id, shuffleSalt);
                if (byShuffle != 0)
                {
                    return byShuffle;
                }
            }

            var byReportTime = left.reported_at_unix_ms.CompareTo(right.reported_at_unix_ms);
            if (byReportTime != 0)
            {
                return byReportTime;
            }

            var bySource = left.source_instance_id.CompareTo(right.source_instance_id);
            if (bySource != 0)
            {
                return bySource;
            }

            return string.Compare(left.agent_id, right.agent_id, StringComparison.OrdinalIgnoreCase);
        }

        private static string ResolveEffectiveAgentId(DecisionAgentPoint point, DecisionAgentRequestDto request)
        {
            if (point == null)
            {
                return NormalizeAgentId(request != null ? request.agent_id : string.Empty);
            }

            var baseAgentId = NormalizeAgentId(point.AgentId);
            if (string.IsNullOrWhiteSpace(baseAgentId))
            {
                baseAgentId = "agent";
            }

            return baseAgentId + "#" + point.GetInstanceID();
        }

        private static int CompareRequestShuffle(
            DecisionAgentRequestDto left,
            string leftAgentId,
            DecisionAgentRequestDto right,
            string rightAgentId,
            int shuffleSalt)
        {
            var leftKey = BuildRequestShuffleKey(left, leftAgentId, shuffleSalt);
            var rightKey = BuildRequestShuffleKey(right, rightAgentId, shuffleSalt);
            if (leftKey != rightKey)
            {
                return leftKey.CompareTo(rightKey);
            }

            return string.Compare(leftAgentId, rightAgentId, StringComparison.OrdinalIgnoreCase);
        }

        private static int BuildRequestShuffleKey(DecisionAgentRequestDto request, string agentId, int shuffleSalt)
        {
            unchecked
            {
                var hash = 17;
                hash = hash * 31 + shuffleSalt;
                hash = hash * 31 + ComputeStableHash(agentId);
                hash = hash * 31 + ComputeStableHash(request != null ? request.crowd_type : string.Empty);
                hash = hash * 31 + ComputeStableHash(request != null ? request.current_zone_id : string.Empty);
                hash = hash * 31 + ComputeStableHash(request != null ? request.algorithm_recommendation : string.Empty);
                return hash;
            }
        }

        private int BuildDispatchShuffleSalt(long nowUnixMs)
        {
            unchecked
            {
                var salt = (int)(nowUnixMs & 0x7fffffff);
                salt = salt * 397 ^ batchSequence;
                salt = salt * 397 ^ inFlightRequestCount;
                return salt;
            }
        }

        private static int ComputeStableHash(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return 0;
            }

            unchecked
            {
                var hash = 23;
                var normalized = value.Trim();
                for (var i = 0; i < normalized.Length; i++)
                {
                    hash = hash * 31 + char.ToUpperInvariant(normalized[i]);
                }

                return hash;
            }
        }

        private bool CanSelectInitialDecisionCandidate(
            string currentZoneId,
            Dictionary<string, int> selectedByEntry,
            int selectedInitialCount)
        {
            if (!balanceInitialDecisionsAcrossEntries)
            {
                return true;
            }

            if (selectedInitialCount >= Mathf.Max(1, maxInitialDecisionAgentsPerBatch))
            {
                return false;
            }

            if (selectedByEntry == null)
            {
                return true;
            }

            var entryKey = NormalizeEntryKey(currentZoneId);
            selectedByEntry.TryGetValue(entryKey, out var selectedCountForEntry);
            return selectedCountForEntry < Mathf.Max(1, maxInitialDecisionsPerEntryPerBatch);
        }

        private void MarkInitialDecisionCandidateSelected(
            string currentZoneId,
            Dictionary<string, int> selectedByEntry,
            ref int selectedInitialCount)
        {
            if (!balanceInitialDecisionsAcrossEntries)
            {
                return;
            }

            selectedInitialCount++;
            if (selectedByEntry == null)
            {
                return;
            }

            var entryKey = NormalizeEntryKey(currentZoneId);
            selectedByEntry.TryGetValue(entryKey, out var selectedCountForEntry);
            selectedByEntry[entryKey] = selectedCountForEntry + 1;
        }

        private static string NormalizeEntryKey(string currentZoneId)
        {
            return string.IsNullOrWhiteSpace(currentZoneId)
                ? "(unknown_entry)"
                : currentZoneId.Trim();
        }

        private string ResolveExitIdToZone(string currentZoneId, string targetZoneId)
        {
            if (string.IsNullOrWhiteSpace(currentZoneId) || string.IsNullOrWhiteSpace(targetZoneId))
            {
                return null;
            }

            ResolveTopologySceneConfig();
            if (topologySceneConfig == null)
            {
                return null;
            }

            var normalizedCurrent = NormalizeAgentId(currentZoneId);
            var normalizedTarget = NormalizeAgentId(targetZoneId);

            if (string.IsNullOrWhiteSpace(normalizedCurrent) || string.IsNullOrWhiteSpace(normalizedTarget))
            {
                return null;
            }

            // 尝试通过最近出口查找（优先匹配 source → target）
            TopologyExitDto nearestExit;
            float distance;
            if (topologySceneConfig.TryGetNearestExitBySourceZone(normalizedCurrent, Vector3.zero, out nearestExit, out distance)
                && nearestExit != null
                && !string.IsNullOrWhiteSpace(nearestExit.exit_id)
                && string.Equals(NormalizeAgentId(nearestExit.target_zone_id), normalizedTarget, StringComparison.OrdinalIgnoreCase))
            {
                return nearestExit.exit_id.Trim();
            }

            // 回退：遍历所有出口查找匹配的
            TopologyUpsertRequestDto topologyRequest;
            string error;
            if (topologySceneConfig.TryBuildTopologyRequest(out topologyRequest, out error)
                && topologyRequest != null
                && topologyRequest.exits != null)
            {
                for (var i = 0; i < topologyRequest.exits.Length; i++)
                {
                    var exit = topologyRequest.exits[i];
                    if (exit == null || string.IsNullOrWhiteSpace(exit.exit_id))
                    {
                        continue;
                    }

                    if (string.Equals(NormalizeAgentId(exit.source_zone_id), normalizedCurrent, StringComparison.OrdinalIgnoreCase)
                        && string.Equals(NormalizeAgentId(exit.target_zone_id), normalizedTarget, StringComparison.OrdinalIgnoreCase))
                    {
                        return exit.exit_id.Trim();
                    }
                }
            }

            return null;
        }

        private string BuildBatchId()
        {
            batchSequence++;
            return "unity_decision_" + RequestIdUtility.GetUnixTimestampMs() + "_" + batchSequence;
        }

        private async UniTask ApplyDecisionResultsAsync(
            DecisionResultItemDto[] decisions,
            Dictionary<string, DecisionAgentPoint> requestAgentMap)
        {
            if (decisions == null || requestAgentMap == null)
            {
                return;
            }

            var now = RequestIdUtility.GetUnixTimestampMs();
            var timeBudgetSeconds = Mathf.Max(0.2f, applyResultsTimeSliceBudgetMs) / 1000f;
            var startTime = Time.realtimeSinceStartupAsDouble;

            for (var i = 0; i < decisions.Length; i++)
            {
                var result = decisions[i];
                if (result == null || string.IsNullOrWhiteSpace(result.agent_id))
                {
                    continue;
                }

                var key = NormalizeAgentId(result.agent_id);
                if (string.IsNullOrWhiteSpace(key))
                {
                    continue;
                }

                DecisionAgentPoint point;
                if (!requestAgentMap.TryGetValue(key, out point) || point == null)
                {
                    DecisionAgentPoint fallbackPoint;
                    if (reportedAgentPoints.TryGetValue(key, out fallbackPoint))
                    {
                        point = fallbackPoint;
                    }
                }

                if (point != null)
                {
                    point.ApplyDecisionResult(result);
                }

                DecisionAgentReportCacheItem cachedReport;
                if (reportedAgentCache.TryGetValue(key, out cachedReport) && cachedReport != null)
                {
                    if (point != null)
                    {
                        cachedReport.needs_llm_decision = point.NeedsLlmDecision;
                    }

                    cachedReport.reported_at_unix_ms = now;
                    reportedAgentCache[key] = cachedReport;
                }

                if (Time.realtimeSinceStartupAsDouble - startTime < timeBudgetSeconds)
                {
                    continue;
                }

                await UniTask.Yield();
                startTime = Time.realtimeSinceStartupAsDouble;
            }

            RefreshReportCacheStats();
        }

        private async UniTask<DecisionResultItemDto[]> ExecuteClientSimulationDecisionBatch(
            string batchId,
            List<DecisionAgentRequestDto> requestAgents,
            Dictionary<string, DecisionAgentPoint> requestAgentMap,
            string dispatchReason,
            DispatchWorkBuffer dispatchBuffer)
        {
            var decisions = BuildClientSimulationDecisions(requestAgents, requestAgentMap, dispatchBuffer);
            await ApplyDecisionResultsAsync(decisions, requestAgentMap);

            lastBatchId = string.IsNullOrWhiteSpace(batchId) ? BuildBatchId() : batchId;
            lastRequestedAgentCount = requestAgents != null ? requestAgents.Count : 0;
            lastResponseDecisionCount = decisions.Length;
            lastSuccessCount = decisions.Length;
            lastFallbackCount = Math.Max(0, lastRequestedAgentCount - lastSuccessCount);
            lastDispatchAtUnixMs = RequestIdUtility.GetUnixTimestampMs();
            lastInvalidReasonSummary = string.Empty;
            lastDispatchSummary = "Client decision batch simulated. mode=" + dispatchMode.ToDisplayName()
                + ", batch_id=" + lastBatchId
                + ", requested=" + lastRequestedAgentCount
                + ", returned=" + lastResponseDecisionCount
                + ", success=" + lastSuccessCount
                + ", unresolved=" + lastFallbackCount;
            if (!string.IsNullOrWhiteSpace(dispatchReason))
            {
                lastDispatchSummary += ", reason=" + dispatchReason;
            }
            LogSummary(lastRequestedAgentCount > 0 && lastResponseDecisionCount <= 0);
            return decisions;
        }

        private DecisionResultItemDto[] BuildClientSimulationDecisions(
            List<DecisionAgentRequestDto> requestAgents,
            Dictionary<string, DecisionAgentPoint> requestAgentMap,
            DispatchWorkBuffer dispatchBuffer)
        {
            if (requestAgents == null || requestAgents.Count <= 0 || dispatchBuffer == null)
            {
                return Array.Empty<DecisionResultItemDto>();
            }

            var decisions = dispatchBuffer.localDecisions;
            decisions.Clear();
            BuildCurrentZonePopulationMap(dispatchBuffer.currentZonePopulation);
            dispatchBuffer.predictedTargetInflow.Clear();
            EnsureTopologyLookupCache();
            EnsureCrowdTypeAttractionLookupCache();

            // 用实时人群分布重新富化出口描述和算法评分
            LocalCrowdDecisionEngine.EnrichExitOptionsWithRuntimePopulation(
                requestAgents,
                dispatchBuffer.currentZonePopulation,
                topologyZoneCapacityByIdCache,
                topologyZoneTypeByIdCache,
                topologyExitByIdCache);
            LocalCrowdDecisionEngine.EnrichAlgorithmRecommendations(
                requestAgents,
                dispatchBuffer.currentZonePopulation,
                dispatchBuffer.predictedTargetInflow,
                topologyZoneCapacityByIdCache,
                topologyZoneTypeByIdCache,
                topologyExitByIdCache,
                crowdTypeAttractionWeightsByTypeIdCache,
                localDecisionScoring);

            for (var i = 0; i < requestAgents.Count; i++)
            {
                var requestAgent = requestAgents[i];
                if (requestAgent == null)
                {
                    continue;
                }

                var normalizedAgentId = NormalizeAgentId(requestAgent.agent_id);
                if (string.IsNullOrWhiteSpace(normalizedAgentId))
                {
                    continue;
                }

                DecisionAgentPoint point;
                Vector3? agentWorldPosition = null;
                if (requestAgentMap != null
                    && requestAgentMap.TryGetValue(normalizedAgentId, out point)
                    && point != null)
                {
                    agentWorldPosition = point.transform.position;
                }

                string chosenExitId;
                string chosenTargetZoneId;
                float confidence;
                string reasoning;
                var scoringConfig = ResolveScoringConfigForCrowdType(requestAgent.crowd_type);
                if (!LocalCrowdDecisionEngine.TrySelectBestExit(
                        requestAgent,
                        agentWorldPosition,
                        dispatchBuffer.currentZonePopulation,
                        dispatchBuffer.predictedTargetInflow,
                        topologyZoneCapacityByIdCache,
                        topologyZoneTypeByIdCache,
                        topologyExitByIdCache,
                        crowdTypeAttractionWeightsByTypeIdCache,
                        scoringConfig,
                        out chosenExitId,
                        out chosenTargetZoneId,
                        out confidence,
                        out reasoning))
                {
                    if (!TryResolveFallbackExit(requestAgent, out chosenExitId, out chosenTargetZoneId))
                    {
                        continue;
                    }

                    confidence = 0.05f;
                    reasoning = "client_simulation: fallback_to_best_available_exit";
                }
                else if (string.IsNullOrWhiteSpace(chosenTargetZoneId))
                {
                    TryResolveTargetZoneId(requestAgent, chosenExitId, out chosenTargetZoneId);
                }

                if (string.IsNullOrWhiteSpace(chosenExitId))
                {
                    continue;
                }

                decisions.Add(new DecisionResultItemDto
                {
                    agent_id = normalizedAgentId,
                    chosen_exit_id = chosenExitId,
                    decision_source = DecisionSource.Algorithm.ToApiValue(),
                    reasoning = reasoning,
                    confidence = Mathf.Clamp01(confidence),
                });

                if (!string.IsNullOrWhiteSpace(chosenTargetZoneId))
                {
                    IncrementCount(dispatchBuffer.predictedTargetInflow, chosenTargetZoneId);
                }

                // 从当前区域人口中减去本 agent，避免后续同批次 agent 看到虚高的人数
                var agentCurrentZone = NormalizeAgentId(requestAgent.current_zone_id);
                if (!string.IsNullOrWhiteSpace(agentCurrentZone))
                {
                    DecrementCount(dispatchBuffer.currentZonePopulation, agentCurrentZone);
                }
            }

            var result = decisions.ToArray();
            decisions.Clear();
            dispatchBuffer.currentZonePopulation.Clear();
            dispatchBuffer.predictedTargetInflow.Clear();
            return result;
        }

        private LocalDecisionScoringConfig ResolveScoringConfigForCrowdType(string crowdType)
        {
            var fallback = localDecisionScoring ?? new LocalDecisionScoringConfig();
            if (crowdTypeDecisionScoringOverrides == null || crowdTypeDecisionScoringOverrides.Length <= 0)
            {
                return fallback;
            }

            var normalizedCrowdType = NormalizeAgentId(crowdType);
            if (string.IsNullOrWhiteSpace(normalizedCrowdType))
            {
                return fallback;
            }

            for (var i = 0; i < crowdTypeDecisionScoringOverrides.Length; i++)
            {
                var item = crowdTypeDecisionScoringOverrides[i];
                if (item == null)
                {
                    continue;
                }

                string normalizedOverrideCrowdType;
                if (!item.TryGetNormalizedCrowdType(out normalizedOverrideCrowdType))
                {
                    continue;
                }

                if (!string.Equals(
                        normalizedOverrideCrowdType,
                        normalizedCrowdType,
                        StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                return item.GetScoringOrDefault(fallback);
            }

            return fallback;
        }

        private void BuildCurrentZonePopulationMap(Dictionary<string, int> population)
        {
            if (population == null)
            {
                return;
            }

            population.Clear();
            foreach (var pair in reportedAgentCache)
            {
                var report = pair.Value;
                if (report == null || report.request == null)
                {
                    continue;
                }

                var zoneId = NormalizeAgentId(report.request.current_zone_id);
                if (string.IsNullOrWhiteSpace(zoneId))
                {
                    continue;
                }

                IncrementCount(population, zoneId);
            }
        }

        private void ResolveTopologySceneConfig()
        {
            if (topologySceneConfig != null)
            {
                return;
            }

            if (!autoFindTopologySceneConfig)
            {
                return;
            }

            topologySceneConfig = FindObjectOfType<TopologySceneConfig>();
            if (topologySceneConfig != null)
            {
                return;
            }

            var candidates = FindObjectsOfType<TopologySceneConfig>(true);
            if (candidates != null && candidates.Length > 0)
            {
                topologySceneConfig = candidates[0];
            }
        }

        private static bool TryResolveTargetZoneId(
            DecisionAgentRequestDto request,
            string chosenExitId,
            out string targetZoneId)
        {
            targetZoneId = string.Empty;
            if (request == null
                || request.available_exits == null
                || request.available_exits.Length <= 0
                || string.IsNullOrWhiteSpace(chosenExitId))
            {
                return false;
            }

            var normalizedChosenExitId = NormalizeAgentId(chosenExitId);
            for (var i = 0; i < request.available_exits.Length; i++)
            {
                var option = request.available_exits[i];
                if (option == null)
                {
                    continue;
                }

                var optionExitId = NormalizeAgentId(option.exit_id);
                if (!string.Equals(optionExitId, normalizedChosenExitId, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                targetZoneId = NormalizeAgentId(option.target_zone_id);
                return !string.IsNullOrWhiteSpace(targetZoneId);
            }

            return false;
        }

        private static bool TryResolveFallbackExit(
            DecisionAgentRequestDto request,
            out string exitId,
            out string targetZoneId)
        {
            exitId = string.Empty;
            targetZoneId = string.Empty;
            if (request == null || request.available_exits == null || request.available_exits.Length <= 0)
            {
                return false;
            }

            var recommendation = NormalizeAgentId(request.algorithm_recommendation);
            var bestScore = float.NegativeInfinity;

            for (var i = 0; i < request.available_exits.Length; i++)
            {
                var option = request.available_exits[i];
                if (option == null)
                {
                    continue;
                }

                var optionExitId = NormalizeAgentId(option.exit_id);
                var optionTargetZoneId = NormalizeAgentId(option.target_zone_id);
                if (string.IsNullOrWhiteSpace(optionExitId) || string.IsNullOrWhiteSpace(optionTargetZoneId))
                {
                    continue;
                }

                if (!string.IsNullOrWhiteSpace(recommendation)
                    && string.Equals(optionExitId, recommendation, StringComparison.OrdinalIgnoreCase))
                {
                    exitId = optionExitId;
                    targetZoneId = optionTargetZoneId;
                    return true;
                }

                if (string.IsNullOrWhiteSpace(exitId) || option.algorithm_score > bestScore)
                {
                    exitId = optionExitId;
                    targetZoneId = optionTargetZoneId;
                    bestScore = option.algorithm_score;
                }
            }

            return !string.IsNullOrWhiteSpace(exitId);
        }

        private static void IncrementCount(Dictionary<string, int> counts, string key)
        {
            if (counts == null || string.IsNullOrWhiteSpace(key))
            {
                return;
            }

            int current;
            if (!counts.TryGetValue(key, out current))
            {
                counts[key] = 1;
                return;
            }

            counts[key] = current + 1;
        }

        private static void DecrementCount(Dictionary<string, int> counts, string key)
        {
            if (counts == null || string.IsNullOrWhiteSpace(key))
            {
                return;
            }

            int current;
            if (!counts.TryGetValue(key, out current))
            {
                return;
            }

            var next = current - 1;
            if (next <= 0)
            {
                counts.Remove(key);
                return;
            }

            counts[key] = next;
        }

        private static string NormalizeAgentId(string agentId)
        {
            return string.IsNullOrWhiteSpace(agentId) ? string.Empty : agentId.Trim();
        }

        private static string NormalizeZoneTypeKey(string rawZoneType)
        {
            var normalized = string.IsNullOrWhiteSpace(rawZoneType)
                ? string.Empty
                : rawZoneType.Trim().ToLowerInvariant();
            return normalized == "exitfinal" ? "exit_final" : normalized;
        }

        private void RefreshReportCacheStats()
        {
            cachedReportCount = reportedAgentCache.Count;
            cachedValidReportCount = 0;
            cachedPendingDecisionCount = 0;

            foreach (var pair in reportedAgentCache)
            {
                var report = pair.Value;
                if (report == null)
                {
                    continue;
                }

                if (report.is_valid)
                {
                    cachedValidReportCount++;
                }

                if (report.is_valid && report.needs_llm_decision)
                {
                    cachedPendingDecisionCount++;
                }
            }
        }

        private string BuildInvalidReasonSummary()
        {
            invalidReasonCountScratch.Clear();
            foreach (var pair in reportedAgentCache)
            {
                var report = pair.Value;
                if (report == null || report.is_valid)
                {
                    continue;
                }

                var reason = NormalizeInvalidReason(report.error);
                int count;
                if (!invalidReasonCountScratch.TryGetValue(reason, out count))
                {
                    invalidReasonCountScratch[reason] = 1;
                    continue;
                }

                invalidReasonCountScratch[reason] = count + 1;
            }

            if (invalidReasonCountScratch.Count <= 0)
            {
                return string.Empty;
            }

            sortedInvalidReasonCountScratch.Clear();
            foreach (var pair in invalidReasonCountScratch)
            {
                sortedInvalidReasonCountScratch.Add(pair);
            }

            var sortedReasonCounts = sortedInvalidReasonCountScratch;
            sortedReasonCounts.Sort((left, right) =>
            {
                var byCount = right.Value.CompareTo(left.Value);
                if (byCount != 0)
                {
                    return byCount;
                }

                return string.Compare(left.Key, right.Key, StringComparison.Ordinal);
            });

            var maxItems = Math.Min(3, sortedReasonCounts.Count);
            var segments = new string[maxItems];
            for (var i = 0; i < maxItems; i++)
            {
                segments[i] = sortedReasonCounts[i].Key + "(" + sortedReasonCounts[i].Value + ")";
            }

            var summary = string.Join(" | ", segments);
            sortedInvalidReasonCountScratch.Clear();
            invalidReasonCountScratch.Clear();
            return summary;
        }

        private static string NormalizeInvalidReason(string rawReason)
        {
            if (string.IsNullOrWhiteSpace(rawReason))
            {
                return "(unknown)";
            }

            var reason = rawReason.Trim();
            var markerIndex = reason.IndexOf(" agent_id=", StringComparison.OrdinalIgnoreCase);
            if (markerIndex > 0)
            {
                reason = reason.Substring(0, markerIndex).Trim();
            }

            return reason;
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

        private void LogDetailedDispatchState(
            string stage,
            string experimentId,
            bool runClientSimulation,
            bool requiresBackendDispatch,
            List<DecisionAgentRequestDto> requestAgents,
            string extra,
            bool force)
        {
            if (!enableDetailedDispatchLogs || !Application.isPlaying)
            {
                return;
            }

            if (enableBatchBundleLog && suppressLegacyLogsWhenBatchBundle)
            {
                return;
            }

            if (!force && Time.unscaledTime < nextDetailedDispatchLogAtTime)
            {
                return;
            }

            nextDetailedDispatchLogAtTime = Time.unscaledTime + Mathf.Max(0.2f, detailedDispatchLogIntervalSeconds);

            var pendingInCache = 0;
            var invalidInCache = 0;
            foreach (var pair in reportedAgentCache)
            {
                var report = pair.Value;
                if (report == null)
                {
                    continue;
                }

                if (!report.is_valid)
                {
                    invalidInCache++;
                }

                if (report.is_valid && report.needs_llm_decision)
                {
                    pendingInCache++;
                }
            }

            var requestCount = requestAgents != null ? requestAgents.Count : 0;
            var sample = BuildRequestAgentSample(requestAgents);

            var message = "[DecisionBatchScheduler][detail]"
                + " stage=" + NormalizeLogValue(stage)
                + " mode=" + dispatchMode.ToDisplayName()
                + " experiment_id=" + NormalizeLogValue(experimentId)
                + " run_client=" + runClientSimulation
                + " requires_backend=" + requiresBackendDispatch
                + " in_flight=" + inFlightRequestCount
                + " cache_total=" + reportedAgentCache.Count
                + " cache_pending=" + pendingInCache
                + " cache_invalid=" + invalidInCache
                + " request_count=" + requestCount
                + " request_sample=" + sample;

            if (!string.IsNullOrWhiteSpace(extra))
            {
                message += " extra=" + extra;
            }

            Debug.Log(message, this);
        }

        private string BuildRequestAgentSample(List<DecisionAgentRequestDto> requestAgents)
        {
            if (requestAgents == null || requestAgents.Count <= 0)
            {
                return "(none)";
            }

            var sampleCount = Math.Min(requestAgents.Count, Math.Max(1, detailedDispatchSampleAgentCount));
            var segments = new string[sampleCount];
            for (var i = 0; i < sampleCount; i++)
            {
                var request = requestAgents[i];
                if (request == null)
                {
                    segments[i] = "(null)";
                    continue;
                }

                var exitCount = request.available_exits != null ? request.available_exits.Length : 0;
                segments[i] = NormalizeLogValue(request.agent_id)
                    + "@" + NormalizeLogValue(request.current_zone_id)
                    + "->" + NormalizeLogValue(request.algorithm_recommendation)
                    + "(exits=" + exitCount + ")";
            }

            return string.Join(" | ", segments);
        }

        private static string NormalizeLogValue(string value)
        {
            return string.IsNullOrWhiteSpace(value) ? "(empty)" : value.Trim();
        }

        private void EmitBatchBundleLog(
            string batchId,
            string experimentId,
            string module,
            string moduleReason,
            List<DecisionAgentRequestDto> requestAgents,
            DecisionResultItemDto[] decisions,
            bool success,
            string error,
            int successCount,
            int fallbackCount)
        {
            if (!enableBatchBundleLog || !Application.isPlaying)
            {
                return;
            }

            var requestCount = requestAgents != null ? requestAgents.Count : 0;
            var decisionCount = decisions != null ? decisions.Length : 0;

            var requestTruncatedCount = 0;
            var resultTruncatedCount = 0;
            var requestJson = "(payload_disabled)";
            var resultJson = "(payload_disabled)";
            if (includeBatchBundlePayloadJson)
            {
                var requestPayload = BuildBatchBundleRequestPayload(requestAgents, out requestTruncatedCount);
                var resultPayload = BuildBatchBundleResultPayload(decisions, out resultTruncatedCount);

                requestJson = JsonUtility.ToJson(new BatchBundleRequestPayloadWrapper
                {
                    agents = requestPayload,
                });
                resultJson = JsonUtility.ToJson(new BatchBundleResultPayloadWrapper
                {
                    decisions = resultPayload,
                });
            }

            var message = "[DecisionBatchScheduler][batch_bundle]"
                + " mode=" + dispatchMode.ToDisplayName()
                + " module=" + NormalizeLogValue(module)
                + " module_reason=" + NormalizeLogValue(moduleReason)
                + " success=" + success
                + " experiment_id=" + NormalizeLogValue(experimentId)
                + " batch_id=" + NormalizeLogValue(batchId)
                + " in_flight=" + inFlightRequestCount
                + " request_count=" + requestCount
                + " decision_count=" + decisionCount
                + " success_count=" + Math.Max(0, successCount)
                + " fallback_count=" + Math.Max(0, fallbackCount)
                + " request_truncated=" + Math.Max(0, requestTruncatedCount)
                + " result_truncated=" + Math.Max(0, resultTruncatedCount)
                + " error=" + NormalizeLogValue(error)
                + " request_payload=" + requestJson
                + " result_payload=" + resultJson;

            if (success)
            {
                Debug.Log(message, this);
                return;
            }

            Debug.LogError(message, this);
        }

        private DecisionAgentRequestDto[] BuildBatchBundleRequestPayload(
            List<DecisionAgentRequestDto> requestAgents,
            out int truncatedCount)
        {
            truncatedCount = 0;
            if (requestAgents == null || requestAgents.Count <= 0)
            {
                return Array.Empty<DecisionAgentRequestDto>();
            }

            var maxCount = Math.Max(1, batchBundleMaxAgentPayloadCount);
            var count = Math.Min(requestAgents.Count, maxCount);
            truncatedCount = Math.Max(0, requestAgents.Count - count);

            var payload = new DecisionAgentRequestDto[count];
            for (var i = 0; i < count; i++)
            {
                payload[i] = requestAgents[i];
            }

            return payload;
        }

        private DecisionResultItemDto[] BuildBatchBundleResultPayload(
            DecisionResultItemDto[] decisions,
            out int truncatedCount)
        {
            truncatedCount = 0;
            if (decisions == null || decisions.Length <= 0)
            {
                return Array.Empty<DecisionResultItemDto>();
            }

            var maxCount = Math.Max(1, batchBundleMaxAgentPayloadCount);
            var count = Math.Min(decisions.Length, maxCount);
            truncatedCount = Math.Max(0, decisions.Length - count);

            if (count == decisions.Length)
            {
                return decisions;
            }

            var payload = new DecisionResultItemDto[count];
            Array.Copy(decisions, payload, count);
            return payload;
        }

        private void LogSummary(bool isError = false)
        {
            if (enableBatchBundleLog && suppressLegacyLogsWhenBatchBundle)
            {
                return;
            }

            if (!logToConsole)
            {
                return;
            }

            if (isError)
            {
                Debug.LogError("[DecisionBatchScheduler] " + lastDispatchSummary);
                return;
            }

            Debug.Log("[DecisionBatchScheduler] " + lastDispatchSummary);
        }

        [Serializable]
        private sealed class BatchBundleRequestPayloadWrapper
        {
            public DecisionAgentRequestDto[] agents;
        }

        [Serializable]
        private sealed class BatchBundleResultPayloadWrapper
        {
            public DecisionResultItemDto[] decisions;
        }
    }
}
