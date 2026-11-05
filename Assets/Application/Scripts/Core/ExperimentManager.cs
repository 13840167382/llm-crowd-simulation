using System;
using System.Collections.Generic;
using System.IO;
using Cysharp.Threading.Tasks;
using CrowdSim.Data.Allocation;
using CrowdSim.Data.Api;
using CrowdSim.Data.Common;
using CrowdSim.Data.Crowd;
using CrowdSim.Data.CrowdTypes;
using CrowdSim.Data.Decisions;
using CrowdSim.Data.Enums;
using CrowdSim.Data.Experiments;
using CrowdSim.Data.Spawning;
using CrowdSim.Data.UI;
using CrowdSim.Simulation.Analytics;
using CrowdSim.Simulation.Decision;
using CrowdSim.Data.Topology;
using CrowdSim.Simulation.Topology;
using CrowdSim.Utils;
using UnityEngine;
using UnityEngine.AI;
using UnityEngine.SceneManagement;

namespace CrowdSim.Core
{
    public sealed class ExperimentManager : MonoBehaviour
    {
        private struct CrowdAllocationPlanItem
        {
            public string TypeId;
            public int Count;
        }

        private static ExperimentManager activeInstance;
        private static bool hasPendingStartupSelection;
        private static string pendingStartupMapId = string.Empty;
        private static string pendingStartupSceneName = string.Empty;

        private readonly List<ExperimentCacheRecord> cacheRecords = new List<ExperimentCacheRecord>();
        private readonly Dictionary<string, int> cacheRecordIndex = new Dictionary<string, int>(StringComparer.Ordinal);

        private int callSequence;

        [Header("Backend")]
        [SerializeField] private BackendApiClientOptions backendOptions = new BackendApiClientOptions();

        public static BackendApiClientOptions SharedBackendOptions
        {
            get
            {
                if (activeInstance != null && activeInstance.backendOptions != null)
                {
                    return activeInstance.backendOptions;
                }

                return new BackendApiClientOptions();
            }
        }

        [Header("Startup")]
        [SerializeField] private bool requireStartupBeforeCreate = true;
        [SerializeField] private StartupMapConfig[] startupMapConfigs = Array.Empty<StartupMapConfig>();
        [SerializeField]
        private StartupMapOption[] startupMapOptions =
        {
            new StartupMapOption(),
        };
        [SerializeField, Min(0)] private int selectedStartupMapIndex;
        [SerializeField] private bool startupCompleted;
        [SerializeField] private string startupStatus = "Startup not completed.";

        [Header("Startup Crowd Templates")]
        [SerializeField] private bool requireCrowdTemplateSyncBeforeCreate = true;
        [SerializeField] private CrowdTypeTemplateConfig crowdTypeTemplateConfig;
        [SerializeField] private bool crowdTemplateStartupCompleted;
        [SerializeField] private string crowdTemplateStartupStatus = "Crowd templates not prepared.";
        [SerializeField] private CrowdTypeTemplateDto[] runtimeCrowdTypeTemplates = Array.Empty<CrowdTypeTemplateDto>();

        [Header("Create")]
        [SerializeField] private string createNamePrefix = "unity_m3";
        [SerializeField] private string createDescription = "Unity M3 experiment";
        [SerializeField] private string mapId = "unity_2d_map";
        [SerializeField] private string[] experimentTags = { "unity", "m3" };
        [SerializeField] private string llmModel = "gpt-4o-mini";
        [SerializeField] private float llmTemperature = 0.4f;
        [SerializeField] private int llmMaxTokens = 256;
        [SerializeField] private int llmTimeoutMs = 5000;
        [SerializeField] private int decisionBatchIntervalMs = 1000;
        [SerializeField] private int decisionBatchSize = 32;
        [SerializeField] private bool fallbackToAlgorithm = true;

        [Header("Configure")]
        [SerializeField] private string preferredCrowdTypeId = "tourist";
        [SerializeField, Min(1)] private int crowdCount = 20;
        [SerializeField] private CrowdTypeAllocationConfig[] crowdTypeAllocations = Array.Empty<CrowdTypeAllocationConfig>();
        [SerializeField] private EntryZoneAllocationMode entryZoneAllocationMode = EntryZoneAllocationMode.RandomEntry;
        [SerializeField] private bool allocationAvoidExits = true;
        [SerializeField] private string fixedEntryZoneId = "entry_zone_01";
        [SerializeField] private AllocationEntryZoneWeightConfig[] entryZoneWeights = Array.Empty<AllocationEntryZoneWeightConfig>();
        [SerializeField] private string zoneId = "entry_zone_01";
        [SerializeField] private string zoneName = "Entry Zone";
        [SerializeField] private float zoneMinX = -5f;
        [SerializeField] private float zoneMinZ = -5f;
        [SerializeField] private float zoneMaxX = 5f;
        [SerializeField] private float zoneMaxZ = 5f;
        [SerializeField, Min(1)] private int zoneCapacity = 100;
        [SerializeField] private bool preferSceneTopologyConfig = true;
        [SerializeField] private TopologySceneConfig topologySceneConfig;

        [Header("Default Crowd Profile")]
        [SerializeField] private CrowdDefaultProfile defaultCrowdProfile;
        [SerializeField] private bool applyDefaultCrowdProfileOnEnable = true;
        [SerializeField] private bool applyDecisionPreferenceFromDefaultProfile = true;

        [Header("Complete")]
        [SerializeField] private CompletionReason completionReason = CompletionReason.SimulationFinished;
        [SerializeField, Min(0)] private int completionDurationMs = 1000;
        [SerializeField, Min(0)] private int completionFinalAgentCount;

        [Header("Spawn Positions")]
        [SerializeField] private bool useFixedSpawnSeed = true;
        [SerializeField] private int spawnSeed = 42;
        [SerializeField] private bool generateSpawnPositionsBeforeStart = true;
        [SerializeField] private bool instantiatePrefabsOnSpawn = true;
        [SerializeField] private bool clearSpawnedPrefabsBeforeInstantiate = true;
        [SerializeField] private Transform spawnedPrefabsParent;
        [SerializeField] private float spawnedPrefabY;
        [SerializeField] private bool snapSpawnedAgentToNavMesh = true;
        [SerializeField, Min(0.1f)] private float spawnNavMeshSampleRadius = 2.5f;
        [SerializeField, Min(0.1f)] private float spawnNavMeshFallbackSampleRadius = 8f;
        [SerializeField] private SpawnPrefabDatabase spawnPrefabDatabase;

        [Header("Decision Flow")]
        [SerializeField] private DecisionBatchScheduler decisionBatchScheduler;
        [SerializeField] private bool startDecisionSchedulerAfterExperimentStarted = true;
        [SerializeField] private bool dispatchDecisionBatchImmediatelyAfterStart = false;
        [SerializeField] private bool stopDecisionSchedulerWhenExperimentCompleted = true;

        [Header("Complete Capture")]
        [SerializeField] private bool captureHeatmapLayersOnComplete = true;
        [SerializeField] private HeatmapCompareOverlayRenderer heatmapCompareOverlayRenderer;
        [SerializeField] private Camera completeCaptureCamera;
        [SerializeField, Min(128)] private int completeCaptureWidth = 1920;
        [SerializeField, Min(128)] private int completeCaptureHeight = 1080;
        [SerializeField] private string completeCaptureOutputRoot = "HeatmapCaptures～";
        [SerializeField] private bool completeCaptureIncludeCombined = true;

        [Header("Path Traces CSV Export")]
        [SerializeField] private bool exportPathTracesCsvOnComplete = true;
        [SerializeField] private string pathTracesCsvOutputRoot = "PathTracesCSV";

        [Header("Decision Transcript")]
        [SerializeField] private bool exportDecisionTranscriptOnComplete = true;
        [SerializeField] private string decisionTranscriptOutputRoot = "DecisionTranscripts";

        [Header("Runtime")]
        [SerializeField, Min(10)] private int maxCachedCallsPerExperiment = 40;
        [SerializeField] private bool logToConsole = true;
        [SerializeField] private string lastCreatedExperimentId = string.Empty;
        [SerializeField] private string currentMap = string.Empty;
        [SerializeField] private bool isBusy;

        private readonly List<GameObject> spawnedPrefabInstances = new List<GameObject>();

        private readonly Dictionary<string, int> overrideAssignedCountByType =
            new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);

        private string runningExperimentId = string.Empty;

        public static event Action CacheChanged;

        public static ExperimentManager ActiveInstance
        {
            get { return activeInstance; }
        }

        public bool IsBusy
        {
            get { return isBusy; }
        }

        public string LastCreatedExperimentId
        {
            get { return lastCreatedExperimentId; }
        }

        public string RunningExperimentId
        {
            get { return runningExperimentId; }
        }

        public string CurrentMap
        {
            get { return currentMap; }
        }

        public bool IsStartupCompleted
        {
            get { return !requireStartupBeforeCreate || startupCompleted; }
        }

        public string StartupStatus
        {
            get { return startupStatus; }
        }

        public bool IsCrowdTemplateStartupCompleted
        {
            get { return !requireCrowdTemplateSyncBeforeCreate || crowdTemplateStartupCompleted; }
        }

        public bool TryGetDecisionRuntimeUiPopulationSnapshot(out DecisionRuntimeUiPopulationSnapshot snapshot)
        {
            if (decisionBatchScheduler == null)
            {
                snapshot = DecisionRuntimeUiPopulationSnapshot.Empty();
                return false;
            }

            snapshot = decisionBatchScheduler.GetUiPopulationSnapshot();
            return true;
        }

        public string CrowdTemplateStartupStatus
        {
            get { return crowdTemplateStartupStatus; }
        }

        public CrowdTypeTemplateDto[] GetRuntimeCrowdTypeTemplatesSnapshot()
        {
            return CloneCrowdTypeTemplates(runtimeCrowdTypeTemplates);
        }

        private void OnEnable()
        {
            if (activeInstance != null && activeInstance != this)
            {
                Debug.LogWarning("Multiple ExperimentManager instances found. Latest enabled instance is used for debug panel.");
            }

            activeInstance = this;
            SceneManager.sceneLoaded += HandleSceneLoaded;
            TryApplyPendingStartupSelection();
            TryAutoResolveStartupSelectionFromActiveScene();
            TryAutoBindTopologySceneConfig();
            if (!requireStartupBeforeCreate && !startupCompleted)
            {
                startupCompleted = true;
                startupStatus = "Startup skipped because requirement is disabled.";
            }

            if (!requireCrowdTemplateSyncBeforeCreate && !crowdTemplateStartupCompleted)
            {
                crowdTemplateStartupCompleted = true;
                crowdTemplateStartupStatus = "Crowd template startup skipped because requirement is disabled.";
            }

            if (applyDefaultCrowdProfileOnEnable)
            {
                ApplyDefaultCrowdProfileInternal(false);
            }

            RaiseCacheChanged();
        }

        private void OnDisable()
        {
            if (activeInstance == this)
            {
                activeInstance = null;
                RaiseCacheChanged();
            }

            SceneManager.sceneLoaded -= HandleSceneLoaded;
        }

        private void HandleSceneLoaded(Scene scene, LoadSceneMode mode)
        {
            TryApplyPendingStartupSelection();
            TryAutoResolveStartupSelectionFromActiveScene();
            TryAutoBindTopologySceneConfig();
            RaiseCacheChanged();
        }

        public static bool TryGetCacheSnapshot(out List<ExperimentCacheRecord> snapshot)
        {
            if (activeInstance == null)
            {
                snapshot = new List<ExperimentCacheRecord>();
                return false;
            }

            snapshot = activeInstance.GetCacheSnapshot();
            return true;
        }

        public List<ExperimentCacheRecord> GetCacheSnapshot()
        {
            var snapshot = new List<ExperimentCacheRecord>(cacheRecords.Count);
            for (var i = 0; i < cacheRecords.Count; i++)
            {
                snapshot.Add(cacheRecords[i].Clone());
            }

            return snapshot;
        }

        public void ClearCache()
        {
            cacheRecords.Clear();
            cacheRecordIndex.Clear();
            callSequence = 0;
            ClearSpawnedPrefabInstances();
            overrideAssignedCountByType.Clear();
            RaiseCacheChanged();
        }

        [ContextMenu("Reset Runtime State")]
        public void ResetRuntimeState()
        {
            TryStopDecisionScheduler();
            runningExperimentId = string.Empty;
            lastCreatedExperimentId = string.Empty;
            ClearCache();
            isBusy = false;
            overrideAssignedCountByType.Clear();
            Debug.Log("[ExperimentManager] Runtime state reset.", this);
        }

        [ContextMenu("Create Experiment")]
        public void ExecuteCreateExperiment()
        {
            CreateExperimentAsync().Forget();
        }

        [ContextMenu("Startup: Select Map And Load Scene")]
        public void ExecuteStartupSelectMapAndLoadScene()
        {
            StartupSelectMapAndLoadSceneAsync().Forget();
        }

        [ContextMenu("Startup: Sync Crowd Templates")]
        public void ExecuteStartupSyncCrowdTemplates()
        {
            StartupSyncCrowdTemplatesAsync().Forget();
        }

        [ContextMenu("Configure Last Created Experiment")]
        public void ExecuteConfigureLastCreatedExperiment()
        {
            ConfigureExperimentAsync(lastCreatedExperimentId).Forget();
        }

        [ContextMenu("Start Last Created Experiment")]
        public void ExecuteStartLastCreatedExperiment()
        {
            StartExperimentAsync(lastCreatedExperimentId).Forget();
        }

        [ContextMenu("Generate Spawn Positions For Last Created Experiment")]
        public void ExecuteGenerateSpawnPositionsForLastCreatedExperiment()
        {
            GenerateSpawnPositionsAsync(lastCreatedExperimentId).Forget();
        }

        [ContextMenu("Complete Last Created Experiment")]
        public void ExecuteCompleteLastCreatedExperiment()
        {
            CompleteExperimentAsync(lastCreatedExperimentId).Forget();
        }

        [ContextMenu("Apply Default Crowd Profile")]
        public void ExecuteApplyDefaultCrowdProfile()
        {
            ApplyDefaultCrowdProfileInternal(true);
        }

        [ContextMenu("Run Create Start Flow (Create -> Configure -> Spawn -> Start -> Decision)")]
        public void ExecuteCreateStartFlow()
        {
            RunCreateConfigureStartCoreAsync().Forget();
        }

        [ContextMenu("Run Full Flow (Create -> Configure -> Spawn -> Start -> Decision -> Complete)")]
        public void ExecuteFullFlow()
        {
            RunCreateConfigureStartCompleteAsync().Forget();
        }

        public UniTask<ExperimentOperationResult> CreateExperimentAsync()
        {
            return RunSingleOperationAsync(CreateExperimentCoreAsync, "Create Experiment");
        }

        public UniTask<ExperimentOperationResult> StartupSelectMapAndLoadSceneAsync()
        {
            return RunSingleOperationAsync(StartupSelectMapAndLoadSceneCoreAsync, "Startup Select Map");
        }

        public UniTask<ExperimentOperationResult> StartupSyncCrowdTemplatesAsync()
        {
            return RunSingleOperationAsync(StartupSyncCrowdTemplatesCoreAsync, "Startup Sync Crowd Templates");
        }

        public int GetStartupMapCount()
        {
            return GetStartupMapOptionCount();
        }

        public string[] GetStartupMapDisplayNames()
        {
            var optionCount = GetStartupMapOptionCount();
            if (optionCount <= 0)
            {
                return Array.Empty<string>();
            }

            var names = new string[optionCount];
            for (var i = 0; i < optionCount; i++)
            {
                names[i] = BuildStartupMapDisplayName(i);
            }

            return names;
        }

        public string GetStartupMapDisplayName(int index)
        {
            var optionCount = GetStartupMapOptionCount();
            if (optionCount <= 0)
            {
                return string.Empty;
            }

            var clampedIndex = Mathf.Clamp(index, 0, optionCount - 1);
            return BuildStartupMapDisplayName(clampedIndex);
        }

        public string GetSelectedStartupMapDisplayName()
        {
            var optionCount = GetStartupMapOptionCount();
            if (optionCount <= 0)
            {
                return string.Empty;
            }

            var clampedIndex = Mathf.Clamp(selectedStartupMapIndex, 0, optionCount - 1);
            return BuildStartupMapDisplayName(clampedIndex);
        }

        public Sprite GetStartupMapPreviewImage(int index)
        {
            if (startupMapConfigs == null || startupMapConfigs.Length <= 0)
            {
                return null;
            }

            if (index < 0 || index >= startupMapConfigs.Length)
            {
                return null;
            }

            var config = startupMapConfigs[index];
            return config != null ? config.PreviewImage : null;
        }

        public Sprite GetSelectedStartupMapPreviewImage()
        {
            var optionCount = GetStartupMapOptionCount();
            if (optionCount <= 0)
            {
                return null;
            }

            var clampedIndex = Mathf.Clamp(selectedStartupMapIndex, 0, optionCount - 1);
            return GetStartupMapPreviewImage(clampedIndex);
        }

        public string GetSelectedStartupMapDescription()
        {
            if (startupMapConfigs == null || startupMapConfigs.Length <= 0)
            {
                return string.Empty;
            }

            var clampedIndex = Mathf.Clamp(selectedStartupMapIndex, 0, startupMapConfigs.Length - 1);
            var config = startupMapConfigs[clampedIndex];
            return config != null && !string.IsNullOrWhiteSpace(config.Description)
                ? config.Description.Trim()
                : string.Empty;
        }

        public string[] GetStartupMapLabels()
        {
            var optionCount = GetStartupMapOptionCount();
            if (optionCount <= 0)
            {
                return Array.Empty<string>();
            }

            var labels = new string[optionCount];
            for (var i = 0; i < optionCount; i++)
            {
                labels[i] = BuildStartupMapLabel(i);
            }

            return labels;
        }

        public int GetSelectedStartupMapIndex()
        {
            var optionCount = GetStartupMapOptionCount();
            if (optionCount <= 0)
            {
                return 0;
            }

            selectedStartupMapIndex = Mathf.Clamp(selectedStartupMapIndex, 0, optionCount - 1);
            return selectedStartupMapIndex;
        }

        public void SetSelectedStartupMapIndex(int index)
        {
            var optionCount = GetStartupMapOptionCount();
            if (optionCount <= 0)
            {
                selectedStartupMapIndex = 0;
                return;
            }

            selectedStartupMapIndex = Mathf.Clamp(index, 0, optionCount - 1);
            startupCompleted = false;
            startupStatus = "Startup map selected. Run Startup to load the scene.";
            RaiseCacheChanged();
        }

        public CrowdTypeAllocationConfig[] GetCrowdTypeAllocationsSnapshot()
        {
            if (crowdTypeAllocations == null || crowdTypeAllocations.Length <= 0)
            {
                return Array.Empty<CrowdTypeAllocationConfig>();
            }

            var snapshot = new List<CrowdTypeAllocationConfig>(crowdTypeAllocations.Length);
            for (var i = 0; i < crowdTypeAllocations.Length; i++)
            {
                var source = crowdTypeAllocations[i];
                if (source == null)
                {
                    continue;
                }

                string normalizedTypeId;
                int normalizedCount;
                if (!source.TryGetNormalized(out normalizedTypeId, out normalizedCount))
                {
                    continue;
                }

                snapshot.Add(new CrowdTypeAllocationConfig
                {
                    typeId = normalizedTypeId,
                    count = Math.Max(1, normalizedCount),
                });
            }

            return snapshot.ToArray();
        }

        public void ApplyCrowdTypeAllocations(CrowdTypeAllocationConfig[] allocations)
        {
            if (allocations == null || allocations.Length <= 0)
            {
                crowdTypeAllocations = Array.Empty<CrowdTypeAllocationConfig>();
                RaiseCacheChanged();
                return;
            }

            var mergedCountByType = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            var orderedTypeIds = new List<string>();

            for (var i = 0; i < allocations.Length; i++)
            {
                var item = allocations[i];
                if (item == null)
                {
                    continue;
                }

                string normalizedTypeId;
                int normalizedCount;
                if (!item.TryGetNormalized(out normalizedTypeId, out normalizedCount))
                {
                    continue;
                }

                int current;
                if (mergedCountByType.TryGetValue(normalizedTypeId, out current))
                {
                    mergedCountByType[normalizedTypeId] = current + normalizedCount;
                }
                else
                {
                    mergedCountByType.Add(normalizedTypeId, normalizedCount);
                    orderedTypeIds.Add(normalizedTypeId);
                }
            }

            if (orderedTypeIds.Count <= 0)
            {
                crowdTypeAllocations = Array.Empty<CrowdTypeAllocationConfig>();
                RaiseCacheChanged();
                return;
            }

            var normalizedAllocations = new CrowdTypeAllocationConfig[orderedTypeIds.Count];
            for (var i = 0; i < orderedTypeIds.Count; i++)
            {
                var typeId = orderedTypeIds[i];
                normalizedAllocations[i] = new CrowdTypeAllocationConfig
                {
                    typeId = typeId,
                    count = Math.Max(1, mergedCountByType[typeId]),
                };
            }

            crowdTypeAllocations = normalizedAllocations;
            preferredCrowdTypeId = orderedTypeIds[0];
            RaiseCacheChanged();
        }

        public UniTask<ExperimentOperationResult> ConfigureExperimentAsync(string experimentId)
        {
            return RunSingleOperationAsync(() => ConfigureExperimentCoreAsync(experimentId), "Configure Experiment");
        }

        public UniTask<ExperimentOperationResult> StartExperimentAsync(string experimentId)
        {
            return RunSingleOperationAsync(() => StartExperimentCoreAsync(experimentId), "Start Experiment");
        }

        public UniTask<ExperimentOperationResult> GenerateSpawnPositionsAsync(string experimentId)
        {
            return RunSingleOperationAsync(() => GenerateSpawnPositionsCoreAsync(experimentId), "Generate Spawn Positions");
        }

        public UniTask<ExperimentOperationResult> CompleteExperimentAsync(string experimentId)
        {
            return RunSingleOperationAsync(() => CompleteExperimentCoreAsync(experimentId), "Complete Experiment");
        }

        public bool ApplyDefaultCrowdProfile()
        {
            return ApplyDefaultCrowdProfileInternal(true);
        }

        public UniTask<ExperimentOperationResult> RunCreateConfigureStartCompleteAsync()
        {
            return RunSingleOperationAsync(RunCreateConfigureStartCompleteCoreAsync, "Run Full Flow");
        }

        public UniTask<ExperimentOperationResult> RunCreateConfigureStartAsync()
        {
            return RunSingleOperationAsync(RunCreateConfigureStartCoreAsync, "Run Create-Configure-Start Flow");
        }

        private async UniTask<ExperimentOperationResult> RunSingleOperationAsync(
            Func<UniTask<ExperimentOperationResult>> operation,
            string operationName)
        {
            if (isBusy)
            {
                return BuildResult(false, operationName + " skipped: manager is busy.", string.Empty);
            }

            isBusy = true;
            RaiseCacheChanged();

            try
            {
                var result = await operation();
                LogOperationResult(operationName, result);
                return result;
            }
            catch (Exception exception)
            {
                Debug.LogException(exception);
                var message = operationName + " exception: " + exception.Message;
                return BuildResult(false, message, string.Empty);
            }
            finally
            {
                isBusy = false;
                RaiseCacheChanged();
            }
        }

        private async UniTask<ExperimentOperationResult> RunCreateConfigureStartCompleteCoreAsync()
        {
            var createResult = await CreateExperimentCoreAsync();
            if (!createResult.Success)
            {
                return createResult;
            }

            var experimentId = createResult.ExperimentId;

            var configureResult = await ConfigureExperimentCoreAsync(experimentId);
            if (!configureResult.Success)
            {
                return configureResult;
            }

            var startResult = await StartExperimentCoreAsync(experimentId);
            if (!startResult.Success)
            {
                return startResult;
            }
            // return startResult;

            return await CompleteExperimentCoreAsync(experimentId);
        }

        private async UniTask<ExperimentOperationResult> RunCreateConfigureStartCoreAsync()
        {
            var createResult = await CreateExperimentCoreAsync();
            if (!createResult.Success)
            {
                return createResult;
            }

            var experimentId = createResult.ExperimentId;

            var configureResult = await ConfigureExperimentCoreAsync(experimentId);
            if (!configureResult.Success)
            {
                return configureResult;
            }

            var startResult = await StartExperimentCoreAsync(experimentId);
            return startResult;
        }

        private UniTask<ExperimentOperationResult> StartupSelectMapAndLoadSceneCoreAsync()
        {
            string selectedMapId;
            string sceneName;
            string errorMessage;
            if (!TryResolveSelectedStartupMapSelection(out selectedMapId, out sceneName, out errorMessage))
            {
                return UniTask.FromResult(BuildResult(false, errorMessage, string.Empty));
            }

            if (!Application.CanStreamedLevelBeLoaded(sceneName))
            {
                var sceneMissingMessage = "Startup failed: scene " + sceneName + " is not in Build Settings.";
                return UniTask.FromResult(BuildResult(false, sceneMissingMessage, string.Empty));
            }

            mapId = selectedMapId;
            startupCompleted = false;
            startupStatus = "Loading scene " + sceneName + " for map " + mapId + "...";

            hasPendingStartupSelection = true;
            pendingStartupMapId = mapId;
            pendingStartupSceneName = sceneName;

            SceneManager.LoadSceneAsync(sceneName, LoadSceneMode.Single);

            var message = "Startup selected map " + mapId + ", loading scene " + sceneName + ".";
            return UniTask.FromResult(BuildResult(true, message, string.Empty));
        }

        private async UniTask<ExperimentOperationResult> StartupSyncCrowdTemplatesCoreAsync()
        {
            var startupService = new CrowdTypeTemplateStartupService(CreateApiClient());
            var syncResult = await startupService.FetchAndSyncAsync(crowdTypeTemplateConfig);

            crowdTemplateStartupCompleted = syncResult.Success;
            crowdTemplateStartupStatus = syncResult.Message;
            runtimeCrowdTypeTemplates = CloneCrowdTypeTemplates(syncResult.Templates);
            RaiseCacheChanged();

            return BuildResult(syncResult.Success, syncResult.Message, string.Empty);
        }

        private async UniTask<ExperimentOperationResult> CreateExperimentCoreAsync()
        {
            if (requireStartupBeforeCreate && !startupCompleted)
            {
                const string startupRequiredMessage = "Create experiment failed: startup is required. Select a map and load its scene first.";
                return BuildResult(false, startupRequiredMessage, string.Empty);
            }

            if (requireCrowdTemplateSyncBeforeCreate)
            {
                var crowdTemplateResult = await StartupSyncCrowdTemplatesCoreAsync();
                if (!crowdTemplateResult.Success)
                {
                    return crowdTemplateResult;
                }
            }

            var request = BuildCreateRequest();
            var requestJson = JsonUtility.ToJson(request);
            var path = "/api/v1/experiments";
            var raw = await CreateApiClient().PostJson(path, requestJson, null);

            ApiResponseMeta meta;
            string error;
            if (!TryEnsureApiSuccess(raw, "POST /api/v1/experiments", out meta, out error))
            {
                return BuildResult(false, error, string.Empty);
            }

            var response = JsonUtility.FromJson<ExperimentCreatedResponseDto>(raw.Body);
            if (response == null || response.data == null || string.IsNullOrWhiteSpace(response.data.experiment_id))
            {
                return BuildResult(false, "Create experiment response is invalid.", string.Empty);
            }

            ExperimentStatus createdStatus;
            if (!ExperimentStatusMapper.TryParse(response.data.status, out createdStatus))
            {
                var unknownMessage = "Create experiment status is invalid: " + response.data.status;
                var unknownEntry = EnsureRecord(response.data.experiment_id);
                unknownEntry.ExperimentName = request.name;
                RecordCall(unknownEntry, "Create Experiment", raw, meta, false, unknownMessage);
                return BuildResult(false, unknownMessage, response.data.experiment_id);
            }

            var record = EnsureRecord(response.data.experiment_id);
            record.ExperimentName = request.name;
            record.HasStatus = true;
            record.Status = createdStatus;
            if (record.CreatedAtUnixMs <= 0)
            {
                record.CreatedAtUnixMs = response.data.created_at_ms > 0
                    ? response.data.created_at_ms
                    : RequestIdUtility.GetUnixTimestampMs();
            }

            lastCreatedExperimentId = response.data.experiment_id;
            RecordCall(record, "Create Experiment", raw, meta, true, "Created experiment.");
            return BuildResult(true, "Created experiment " + response.data.experiment_id, response.data.experiment_id);
        }

        private async UniTask<ExperimentOperationResult> ConfigureExperimentCoreAsync(string experimentId)
        {
            var resolvedExperimentId = ResolveExperimentId(experimentId);
            if (string.IsNullOrWhiteSpace(resolvedExperimentId))
            {
                return BuildResult(false, "Configure experiment failed: experiment id is empty.", string.Empty);
            }

            var record = EnsureRecord(resolvedExperimentId);

            var crowdTypesPath = "/api/v1/crowd-types";
            var crowdTypesRaw = await CreateApiClient().Get(crowdTypesPath, null);

            ApiResponseMeta crowdTypesMeta;
            string crowdTypesError;
            if (!TryEnsureApiSuccess(crowdTypesRaw, "GET /api/v1/crowd-types", out crowdTypesMeta, out crowdTypesError))
            {
                RecordCall(record, "Fetch Crowd Types", crowdTypesRaw, crowdTypesMeta, false, crowdTypesError);
                return BuildResult(false, crowdTypesError, resolvedExperimentId);
            }

            var crowdTypesResponse = JsonUtility.FromJson<CrowdTypesListResponseDto>(crowdTypesRaw.Body);
            var templates = crowdTypesResponse != null && crowdTypesResponse.data != null
                ? crowdTypesResponse.data.GetTemplateList()
                : null;
            runtimeCrowdTypeTemplates = CloneCrowdTypeTemplates(templates);
            if (templates == null || templates.Length == 0)
            {
                const string message = "Configure experiment failed: crowd type templates are empty.";
                RecordCall(record, "Fetch Crowd Types", crowdTypesRaw, crowdTypesMeta, false, message);
                return BuildResult(false, message, resolvedExperimentId);
            }

            string planError;
            var crowdAllocationPlan = BuildCrowdAllocationPlan(templates, out planError);
            if (crowdAllocationPlan == null || crowdAllocationPlan.Length <= 0)
            {
                var message = string.IsNullOrWhiteSpace(planError)
                    ? "Configure experiment failed: cannot resolve valid crowd allocation groups."
                    : planError;
                RecordCall(record, "Fetch Crowd Types", crowdTypesRaw, crowdTypesMeta, false, message);
                return BuildResult(false, message, resolvedExperimentId);
            }

            record.SelectedCrowdTypeId = crowdAllocationPlan[0].TypeId;
            var crowdPlanSummary = BuildCrowdAllocationPlanSummary(crowdAllocationPlan);
            RecordCall(record, "Fetch Crowd Types", crowdTypesRaw, crowdTypesMeta, true, "Selected crowd allocation: " + crowdPlanSummary);

            var topologyPath = "/api/v1/experiments/" + resolvedExperimentId + "/topology";
            TopologyUpsertRequestDto topologyRequest;
            string topologyRequestError;
            if (!TryBuildTopologyRequest(out topologyRequest, out topologyRequestError))
            {
                RecordCall(record, "Configure Topology", null, null, false, topologyRequestError);
                return BuildResult(false, topologyRequestError, resolvedExperimentId);
            }

            var topologyRequestJson = JsonUtility.ToJson(topologyRequest);
            var topologyRaw = await CreateApiClient().PutJson(topologyPath, topologyRequestJson, resolvedExperimentId);

            ApiResponseMeta topologyMeta;
            string topologyError;
            if (!TryEnsureApiSuccess(topologyRaw, "PUT /api/v1/experiments/{id}/topology", out topologyMeta, out topologyError))
            {
                RecordCall(record, "Configure Topology", topologyRaw, topologyMeta, false, topologyError);
                return BuildResult(false, topologyError, resolvedExperimentId);
            }

            var topologyResponse = JsonUtility.FromJson<TopologyUpsertResponseDto>(topologyRaw.Body);
            if (topologyResponse == null || topologyResponse.data == null || topologyResponse.data.zone_count <= 0)
            {
                const string message = "Configure experiment failed: topology response is invalid.";
                RecordCall(record, "Configure Topology", topologyRaw, topologyMeta, false, message);
                return BuildResult(false, message, resolvedExperimentId);
            }

            RecordCall(
                record,
                "Configure Topology",
                topologyRaw,
                topologyMeta,
                true,
                "Configured topology. zone_count=" + topologyResponse.data.zone_count);

            var allocationPath = "/api/v1/experiments/" + resolvedExperimentId + "/crowd-allocation";
            string allocationRequestJson;
            string allocationBuildError;
            if (!TryBuildAllocationRequestJson(crowdAllocationPlan, resolvedExperimentId, out allocationRequestJson, out allocationBuildError))
            {
                RecordCall(record, "Configure Allocation", null, null, false, allocationBuildError);
                return BuildResult(false, allocationBuildError, resolvedExperimentId);
            }

            var allocationRaw = await CreateApiClient().PutJson(allocationPath, allocationRequestJson, resolvedExperimentId);

            ApiResponseMeta allocationMeta;
            string allocationError;
            if (!TryEnsureApiSuccess(allocationRaw, "PUT /api/v1/experiments/{id}/crowd-allocation", out allocationMeta, out allocationError))
            {
                RecordCall(record, "Configure Allocation", allocationRaw, allocationMeta, false, allocationError);
                return BuildResult(false, allocationError, resolvedExperimentId);
            }

            var allocationResponse = JsonUtility.FromJson<CrowdAllocationResponseDto>(allocationRaw.Body);
            if (allocationResponse == null || allocationResponse.data == null)
            {
                const string message = "Configure experiment failed: crowd allocation response is invalid.";
                RecordCall(record, "Configure Allocation", allocationRaw, allocationMeta, false, message);
                return BuildResult(false, message, resolvedExperimentId);
            }

            ExperimentStatus configuredStatus;
            if (!ExperimentStatusMapper.TryParse(allocationResponse.data.status, out configuredStatus)
                || configuredStatus != ExperimentStatus.Configured)
            {
                var message = "Configure experiment failed: unexpected status " + allocationResponse.data.status;
                RecordCall(record, "Configure Allocation", allocationRaw, allocationMeta, false, message);
                return BuildResult(false, message, resolvedExperimentId);
            }

            record.HasStatus = true;
            record.Status = configuredStatus;
            RecordCall(
                record,
                "Configure Allocation",
                allocationRaw,
                allocationMeta,
                true,
                "Configured allocation. total_count=" + allocationResponse.data.total_count + ", mode=" + entryZoneAllocationMode.ToApiValue());

            return BuildResult(true, "Configured experiment " + resolvedExperimentId, resolvedExperimentId);
        }

        private async UniTask<ExperimentOperationResult> GenerateSpawnPositionsCoreAsync(string experimentId)
        {
            var resolvedExperimentId = ResolveExperimentId(experimentId);
            if (string.IsNullOrWhiteSpace(resolvedExperimentId))
            {
                return BuildResult(false, "Generate spawn positions failed: experiment id is empty.", string.Empty);
            }

            var record = EnsureRecord(resolvedExperimentId);
            var requestJson = BuildSpawnPositionsRequestJson();
            var path = "/api/v1/experiments/" + resolvedExperimentId + "/spawn-positions";
            var raw = await CreateApiClient().PostJson(path, requestJson, resolvedExperimentId);

            ApiResponseMeta meta;
            string error;
            if (!TryEnsureApiSuccess(raw, "POST /api/v1/experiments/{id}/spawn-positions", out meta, out error))
            {
                RecordCall(record, "Generate Spawn Positions", raw, meta, false, error);
                return BuildResult(false, error, resolvedExperimentId);
            }

            var response = JsonUtility.FromJson<SpawnPositionsResponseDto>(raw.Body);
            if (response == null || response.data == null)
            {
                const string message = "Generate spawn positions failed: response is invalid.";
                RecordCall(record, "Generate Spawn Positions", raw, meta, false, message);
                return BuildResult(false, message, resolvedExperimentId);
            }

            var agents = response.data.agents ?? Array.Empty<SpawnedAgentDto>();
            var skippedCount = 0;
            var instantiatedCount = 0;
            var missingPrefabCount = 0;
            var invalidAgentCount = 0;
            var prefabDatabaseMissing = false;
            var missingTypeIds = new List<string>();
            if (instantiatePrefabsOnSpawn)
            {
                instantiatedCount = InstantiateSpawnedPrefabs(
                    agents,
                    out skippedCount,
                    out missingPrefabCount,
                    out invalidAgentCount,
                    out prefabDatabaseMissing,
                    missingTypeIds);
            }

            record.LastSpawnSeed = response.data.seed;
            record.LastSpawnTotalCount = response.data.total_count;
            record.LastSpawnInstantiatedCount = instantiatedCount;
            record.LastSpawnAtUnixMs = RequestIdUtility.GetUnixTimestampMs();

            var note = "Generated spawn positions. seed=" + response.data.seed
                + ", total_count=" + response.data.total_count
                + ", instantiated=" + instantiatedCount;

            if (skippedCount > 0)
            {
                note += ", skipped=" + skippedCount;
            }

            var warningCount = response.data.warnings != null ? response.data.warnings.Length : 0;
            if (warningCount > 0)
            {
                note += ", warnings=" + warningCount;
            }

            if (prefabDatabaseMissing)
            {
                note += ", reason=prefab_database_not_set";
            }

            if (missingPrefabCount > 0)
            {
                note += ", missing_prefab=" + missingPrefabCount;
                if (missingTypeIds.Count > 0)
                {
                    note += ", missing_type_ids=" + string.Join("|", missingTypeIds.ToArray());
                }
            }

            if (invalidAgentCount > 0)
            {
                note += ", invalid_agents=" + invalidAgentCount;
            }

            RecordCall(record, "Generate Spawn Positions", raw, meta, true, note);
            return BuildResult(true, "Generated spawn positions for " + resolvedExperimentId, resolvedExperimentId);
        }

        private async UniTask<ExperimentOperationResult> StartExperimentCoreAsync(string experimentId)
        {
            var resolvedExperimentId = ResolveExperimentId(experimentId);
            if (string.IsNullOrWhiteSpace(resolvedExperimentId))
            {
                return BuildResult(false, "Start experiment failed: experiment id is empty.", string.Empty);
            }

            var record = EnsureRecord(resolvedExperimentId);
            if (!string.IsNullOrWhiteSpace(runningExperimentId))
            {
                if (string.Equals(runningExperimentId, resolvedExperimentId, StringComparison.Ordinal))
                {
                    const string alreadyRunningMessage = "Start experiment failed: target experiment is already running.";
                    RecordCall(record, "Start Experiment", null, null, false, alreadyRunningMessage);
                    return BuildResult(false, alreadyRunningMessage, resolvedExperimentId);
                }

                var lockMessage = "Start experiment failed: experiment " + runningExperimentId + " is already running. Complete it before starting another one.";
                RecordCall(record, "Start Experiment", null, null, false, lockMessage);
                return BuildResult(false, lockMessage, resolvedExperimentId);
            }

            if (generateSpawnPositionsBeforeStart)
            {
                var spawnResult = await GenerateSpawnPositionsCoreAsync(resolvedExperimentId);
                if (!spawnResult.Success)
                {
                    return spawnResult;
                }
            }

            var request = new ExperimentStartRequestDto
            {
                actual_start_time_ms = RequestIdUtility.GetUnixTimestampMs(),
            };
            var requestJson = JsonUtility.ToJson(request);
            var path = "/api/v1/experiments/" + resolvedExperimentId + "/start";
            var raw = await CreateApiClient().PostJson(path, requestJson, resolvedExperimentId);

            ApiResponseMeta meta;
            string error;
            if (!TryEnsureApiSuccess(raw, "POST /api/v1/experiments/{id}/start", out meta, out error))
            {
                RecordCall(record, "Start Experiment", raw, meta, false, error);
                return BuildResult(false, error, resolvedExperimentId);
            }

            var response = JsonUtility.FromJson<ExperimentStartedResponseDto>(raw.Body);
            if (response == null || response.data == null)
            {
                const string message = "Start experiment failed: response is invalid.";
                RecordCall(record, "Start Experiment", raw, meta, false, message);
                return BuildResult(false, message, resolvedExperimentId);
            }

            ExperimentStatus runningStatus;
            if (!ExperimentStatusMapper.TryParse(response.data.status, out runningStatus)
                || runningStatus != ExperimentStatus.Running)
            {
                var message = "Start experiment failed: unexpected status " + response.data.status;
                RecordCall(record, "Start Experiment", raw, meta, false, message);
                return BuildResult(false, message, resolvedExperimentId);
            }

            record.HasStatus = true;
            record.Status = runningStatus;
            runningExperimentId = resolvedExperimentId;

            TryStartDecisionScheduler();
            if (dispatchDecisionBatchImmediatelyAfterStart)
            {
                await TryDispatchDecisionBatchImmediatelyAfterStartAsync();
            }

            RecordCall(record, "Start Experiment", raw, meta, true, "Experiment running.");
            return BuildResult(true, "Started experiment " + resolvedExperimentId, resolvedExperimentId);
        }

        private async UniTask<ExperimentOperationResult> CompleteExperimentCoreAsync(string experimentId)
        {
            var resolvedExperimentId = ResolveExperimentId(experimentId);
            if (string.IsNullOrWhiteSpace(resolvedExperimentId))
            {
                return BuildResult(false, "Complete experiment failed: experiment id is empty.", string.Empty);
            }

            var record = EnsureRecord(resolvedExperimentId);
            if (!string.IsNullOrWhiteSpace(runningExperimentId)
                && !string.Equals(runningExperimentId, resolvedExperimentId, StringComparison.Ordinal))
            {
                var lockMessage = "Complete experiment failed: running experiment is " + runningExperimentId + ", not " + resolvedExperimentId + ".";
                RecordCall(record, "Complete Experiment", null, null, false, lockMessage);
                return BuildResult(false, lockMessage, resolvedExperimentId);
            }

            var request = new ExperimentCompleteRequestDto
            {
                reason = completionReason.ToApiValue(),
                actual_duration_ms = Math.Max(0, completionDurationMs),
                final_agent_count = completionFinalAgentCount > 0 ? completionFinalAgentCount : GetEffectiveConfiguredCrowdCount(),
            };
            var requestJson = JsonUtility.ToJson(request);
            var path = "/api/v1/experiments/" + resolvedExperimentId + "/complete";
            var raw = await CreateApiClient().PostJson(path, requestJson, resolvedExperimentId);

            ApiResponseMeta meta;
            string error;
            if (!TryEnsureApiSuccess(raw, "POST /api/v1/experiments/{id}/complete", out meta, out error))
            {
                RecordCall(record, "Complete Experiment", raw, meta, false, error);
                return BuildResult(false, error, resolvedExperimentId);
            }

            var response = JsonUtility.FromJson<ExperimentCompletedResponseDto>(raw.Body);
            if (response == null || response.data == null)
            {
                const string message = "Complete experiment failed: response is invalid.";
                RecordCall(record, "Complete Experiment", raw, meta, false, message);
                return BuildResult(false, message, resolvedExperimentId);
            }

            ExperimentStatus completedStatus;
            if (!ExperimentStatusMapper.TryParse(response.data.status, out completedStatus)
                || completedStatus != ExperimentStatus.Completed)
            {
                var message = "Complete experiment failed: unexpected status " + response.data.status;
                RecordCall(record, "Complete Experiment", raw, meta, false, message);
                return BuildResult(false, message, resolvedExperimentId);
            }

            record.HasStatus = true;
            record.Status = completedStatus;
            if (string.Equals(runningExperimentId, resolvedExperimentId, StringComparison.Ordinal))
            {
                runningExperimentId = string.Empty;
            }

            TryStopDecisionScheduler();

            var captureNote = await TryCaptureHeatmapLayersOnCompleteAsync(resolvedExperimentId);
            var transcriptNote = TryExportDecisionTranscriptOnComplete(resolvedExperimentId);
            var completeNoteParts = new List<string>();
            completeNoteParts.Add("Experiment completed.");
            if (!string.IsNullOrWhiteSpace(captureNote))
            {
                completeNoteParts.Add(captureNote);
            }

            if (!string.IsNullOrWhiteSpace(transcriptNote))
            {
                completeNoteParts.Add(transcriptNote);
            }

            var completeNote = string.Join(" ", completeNoteParts);

            RecordCall(record, "Complete Experiment", raw, meta, true, completeNote);
            return BuildResult(true, "Completed experiment " + resolvedExperimentId, resolvedExperimentId);
        }

        private async UniTask<string> TryCaptureHeatmapLayersOnCompleteAsync(string experimentId)
        {
            if (!captureHeatmapLayersOnComplete)
            {
                return string.Empty;
            }

            var renderer = ResolveHeatmapCompareOverlayRenderer();
            if (renderer == null)
            {
                return "heatmap_capture=skipped(renderer_not_found)";
            }

            decisionBatchScheduler.SetAllAgentsVisibleInScene(false);

            var outputDirectory = BuildCompleteCaptureOutputDirectory(experimentId);
            var filePrefix = "exp_" + (string.IsNullOrWhiteSpace(experimentId) ? "unknown" : experimentId.Trim());

            var resultParts = new List<string>();

            try
            {
                var exportResult = await renderer.CaptureLayerCompositedSnapshotsAsync(
                    completeCaptureCamera,
                    renderer.CaptureCameraPosition,
                    renderer.CaptureCameraEulerAngles,
                    renderer.CaptureCameraOrthographicSize,
                    completeCaptureWidth,
                    completeCaptureHeight,
                    outputDirectory,
                    filePrefix,
                    completeCaptureIncludeCombined);

                if (exportResult == null)
                {
                    resultParts.Add("heatmap_capture=failed(null_result)");
                }
                else
                {
                    resultParts.Add("heatmap_capture=ok"
                        + ", layers=" + exportResult.exportedLayerCount
                        + ", output=" + exportResult.outputDirectory);
                }
            }
            catch (Exception exception)
            {
                Debug.LogWarning("Complete capture failed: " + exception.Message, this);
                resultParts.Add("heatmap_capture=failed(" + exception.Message + ")");
            }

            // 导出缓存的 PathTraces 为 CSV
            if (exportPathTracesCsvOnComplete)
            {
                var pathTracesNote = TryExportCachedPathTracesToCsv(renderer, experimentId);
                if (!string.IsNullOrWhiteSpace(pathTracesNote))
                {
                    resultParts.Add(pathTracesNote);
                }
            }

            return string.Join(" ", resultParts);
        }

        private static string TryExportCachedPathTracesToCsv(HeatmapCompareOverlayRenderer renderer, string experimentId)
        {
            var agentDecisionModes = new Dictionary<string, string>(StringComparer.Ordinal);

            // LLM Only 模式下所有 agent 的最终决策都来自 LLM
            var isLlmOnly = activeInstance != null
                && activeInstance.decisionBatchScheduler != null
                && activeInstance.decisionBatchScheduler.IsLlmOnly;

            var agents = FindObjectsOfType<DecisionAgentPoint>();
            for (var i = 0; i < agents.Length; i++)
            {
                var agent = agents[i];
                if (agent == null || string.IsNullOrWhiteSpace(agent.AgentId))
                {
                    continue;
                }

                string decisionMode;
                if (isLlmOnly)
                {
                    decisionMode = "llm";
                }
                else
                {
                    // 使用实际决策来源，与 transcript 中的 sourceLabel 保持一致
                    var source = agent.LastDecisionSource;
                    decisionMode = string.IsNullOrWhiteSpace(source) ? "algorithm" : source;
                }

                agentDecisionModes[agent.AgentId] = decisionMode;
            }

            var outputRoot = activeInstance != null ? activeInstance.pathTracesCsvOutputRoot : "PathTracesCSV";
            return renderer.ExportCachedPathTracesToCsv(experimentId, agentDecisionModes, outputRoot);
        }

        private HeatmapCompareOverlayRenderer ResolveHeatmapCompareOverlayRenderer()
        {
            if (heatmapCompareOverlayRenderer != null)
            {
                return heatmapCompareOverlayRenderer;
            }

            heatmapCompareOverlayRenderer = FindObjectOfType<HeatmapCompareOverlayRenderer>();
            return heatmapCompareOverlayRenderer;
        }

        private string BuildCompleteCaptureOutputDirectory(string experimentId)
        {
            var root = string.IsNullOrWhiteSpace(completeCaptureOutputRoot)
                ? Path.Combine(Application.dataPath, "HeatmapCaptures")
                : completeCaptureOutputRoot.Trim();
            if (!Path.IsPathRooted(root))
            {
                root = Path.Combine(Application.dataPath, root);
            }

            var safeExperimentId = string.IsNullOrWhiteSpace(experimentId)
                ? "unknown_experiment"
                : experimentId.Trim();
            var folderName = safeExperimentId + "_" + DateTime.Now.ToString("yyyyMMdd_HHmmss");
            var outputDirectory = Path.Combine(root, folderName);
            Directory.CreateDirectory(outputDirectory);
            return outputDirectory;
        }

        private string TryExportDecisionTranscriptOnComplete(string experimentId)
        {
            if (!exportDecisionTranscriptOnComplete)
            {
                return string.Empty;
            }

            var scheduler = ResolveDecisionBatchScheduler();
            if (scheduler == null)
            {
                return "decision_transcript=skipped(scheduler_not_found)";
            }

            var transcript = scheduler.GetDecisionTranscript();
            if (string.IsNullOrWhiteSpace(transcript))
            {
                return "decision_transcript=skipped(empty)";
            }

            try
            {
                var root = decisionTranscriptOutputRoot;
                if (string.IsNullOrWhiteSpace(root))
                {
                    root = Application.dataPath;
                }

                root = root.Trim();
                if (!Path.IsPathRooted(root))
                {
                    root = Path.Combine(Application.dataPath, root);
                }

                var safeExperimentId = string.IsNullOrWhiteSpace(experimentId)
                    ? "unknown_experiment"
                    : experimentId.Trim();
                var fileName = "transcript_" + safeExperimentId + "_" + DateTime.Now.ToString("yyyyMMdd_HHmmss") + ".txt";
                Directory.CreateDirectory(root);
                var filePath = Path.Combine(root, fileName);

                var header = "Decision Transcript\n"
                    + "Experiment: " + safeExperimentId + "\n"
                    + "Exported: " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "\n"
                    + new string('=', 60) + "\n\n";
                File.WriteAllText(filePath, header + transcript);

                return "decision_transcript=ok"
                    + ", file=" + fileName
                    + ", size=" + transcript.Length;
            }
            catch (Exception exception)
            {
                Debug.LogWarning("Decision transcript export failed: " + exception.Message, this);
                return "decision_transcript=failed(" + exception.Message + ")";
            }
        }

        private void TryAssignOverrideToAgent(DecisionAgentPoint agentPoint, string crowdType)
        {
            if (agentPoint == null || string.IsNullOrWhiteSpace(crowdType))
            {
                return;
            }

            // 读取当前地图的覆写配置
            var configs = topologySceneConfig != null
                ? topologySceneConfig.FixedPathConfigs
                : Array.Empty<FixedPathConfigSO>();

            if (configs.Length <= 0)
            {
                return;
            }

            var normalizedType = crowdType.Trim();

            // 按配置优先级分配：FixedPath / Random 优先于 Normal
            for (var i = 0; i < configs.Length; i++)
            {
                var config = configs[i];
                if (config == null || !config.IsValid())
                {
                    continue;
                }

                if (config.mode == DecisionOverrideMode.Normal)
                {
                    continue;
                }

                if (!string.Equals(config.crowdType.Trim(), normalizedType, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                int assigned;
                overrideAssignedCountByType.TryGetValue(normalizedType, out assigned);

                if (assigned >= config.count)
                {
                    continue;
                }

                overrideAssignedCountByType[normalizedType] = assigned + 1;
                agentPoint.ApplyOverrideConfig(config.mode, config.zoneIds);

                // FixedPath：随机选取起始区域，agent 从该区域出生并走向下一个区域
                if (config.mode == DecisionOverrideMode.FixedPath
                    && config.zoneIds.Length > 0
                    && topologySceneConfig != null)
                {
                    var startIdx = UnityEngine.Random.Range(0, config.zoneIds.Length);
                    var startZoneId = config.zoneIds[startIdx];

                    // 随机起始位置
                    Vector3 zonePosition;
                    if (topologySceneConfig.TryGetRandomPointInZone(
                            startZoneId,
                            spawnedPrefabY,
                            0.4f,
                            out zonePosition))
                    {
                        agentPoint.transform.position = zonePosition;
                    }

                    // 更新 agent 当前区域为起始区域
                    agentPoint.SetCurrentZone(startZoneId);

                    // 下一目标为起始区域的下一个（循环）
                    agentPoint.SetOverrideZoneStartIndex(startIdx);
                }

                return;
            }
        }

        private DecisionBatchScheduler ResolveDecisionBatchScheduler()
        {
            if (decisionBatchScheduler != null)
            {
                return decisionBatchScheduler;
            }

            decisionBatchScheduler = FindObjectOfType<DecisionBatchScheduler>();
            return decisionBatchScheduler;
        }

        private void TryStartDecisionScheduler()
        {
            if (!startDecisionSchedulerAfterExperimentStarted)
            {
                return;
            }

            var scheduler = ResolveDecisionBatchScheduler();
            if (scheduler == null)
            {
                return;
            }

            scheduler.StartDispatching();
        }

        private async UniTask TryDispatchDecisionBatchImmediatelyAfterStartAsync()
        {
            var scheduler = ResolveDecisionBatchScheduler();
            if (scheduler == null)
            {
                return;
            }

            await scheduler.DispatchNowAsync();
        }

        private void TryStopDecisionScheduler()
        {
            if (!stopDecisionSchedulerWhenExperimentCompleted)
            {
                return;
            }

            var scheduler = ResolveDecisionBatchScheduler();
            if (scheduler == null)
            {
                return;
            }

            scheduler.StopDispatching();
        }

        private string BuildSpawnPositionsRequestJson()
        {
            if (!useFixedSpawnSeed)
            {
                return "{}";
            }

            var request = new SpawnPositionsRequestDto
            {
                seed = spawnSeed,
            };
            return JsonUtility.ToJson(request);
        }

        private int InstantiateSpawnedPrefabs(
            SpawnedAgentDto[] agents,
            out int skippedCount,
            out int missingPrefabCount,
            out int invalidAgentCount,
            out bool prefabDatabaseMissing,
            List<string> missingTypeIds)
        {
            skippedCount = 0;
            missingPrefabCount = 0;
            invalidAgentCount = 0;
            prefabDatabaseMissing = false;

            if (clearSpawnedPrefabsBeforeInstantiate)
            {
                ClearSpawnedPrefabInstances();
            }

            if (agents == null || agents.Length <= 0)
            {
                return 0;
            }

            if (spawnPrefabDatabase == null)
            {
                prefabDatabaseMissing = true;
                skippedCount = agents.Length;
                missingPrefabCount = agents.Length;
                for (var i = 0; i < agents.Length; i++)
                {
                    AddMissingTypeId(missingTypeIds, agents[i] != null ? agents[i].type_id : null);
                }
                return 0;
            }

            var instantiatedCount = 0;
            for (var i = 0; i < agents.Length; i++)
            {
                var agent = agents[i];
                if (agent == null || agent.position == null)
                {
                    skippedCount++;
                    invalidAgentCount++;
                    continue;
                }

                GameObject prefab;
                if (!spawnPrefabDatabase.TryGetPrefab(agent.type_id, out prefab) || prefab == null)
                {
                    skippedCount++;
                    missingPrefabCount++;
                    AddMissingTypeId(missingTypeIds, agent.type_id);
                    continue;
                }

                var position = new Vector3(agent.position.x, spawnedPrefabY, agent.position.z);
                position = ResolveSpawnPosition(position);
                var rotation = Quaternion.Euler(0f, agent.rotation_y, 0f);
                var spawned = Instantiate(prefab, position, rotation, spawnedPrefabsParent);
                if (spawned == null)
                {
                    skippedCount++;
                    continue;
                }

                spawned.name = string.IsNullOrWhiteSpace(agent.agent_id)
                    ? "spawn_agent_" + instantiatedCount
                    : agent.agent_id;

                var decisionAgentPoint = spawned.GetComponent<DecisionAgentPoint>();
                if (decisionAgentPoint != null)
                {
                    decisionAgentPoint.ApplySpawnContext(
                        agent.agent_id,
                        agent.type_id,
                        agent.zone_id,
                        true);

                    TryAssignOverrideToAgent(decisionAgentPoint, agent.type_id);
                }

                spawnedPrefabInstances.Add(spawned);
                instantiatedCount++;
            }

            return instantiatedCount;
        }

        private Vector3 ResolveSpawnPosition(Vector3 originalPosition)
        {
            if (!snapSpawnedAgentToNavMesh)
            {
                return originalPosition;
            }

            NavMeshHit navMeshHit;
            var primaryRadius = Mathf.Max(0.1f, spawnNavMeshSampleRadius);
            if (NavMesh.SamplePosition(originalPosition, out navMeshHit, primaryRadius, NavMesh.AllAreas))
            {
                return navMeshHit.position;
            }

            var fallbackRadius = Mathf.Max(primaryRadius, spawnNavMeshFallbackSampleRadius);
            if (fallbackRadius > primaryRadius + 0.01f
                && NavMesh.SamplePosition(originalPosition, out navMeshHit, fallbackRadius, NavMesh.AllAreas))
            {
                return navMeshHit.position;
            }

            return originalPosition;
        }

        private static void AddMissingTypeId(List<string> missingTypeIds, string typeId)
        {
            if (missingTypeIds == null || missingTypeIds.Count >= 6)
            {
                return;
            }

            var normalized = string.IsNullOrWhiteSpace(typeId) ? "(empty)" : typeId.Trim();
            for (var i = 0; i < missingTypeIds.Count; i++)
            {
                if (string.Equals(missingTypeIds[i], normalized, StringComparison.OrdinalIgnoreCase))
                {
                    return;
                }
            }

            missingTypeIds.Add(normalized);
        }

        private void ClearSpawnedPrefabInstances()
        {
            for (var i = 0; i < spawnedPrefabInstances.Count; i++)
            {
                var instance = spawnedPrefabInstances[i];
                if (instance == null)
                {
                    continue;
                }

                if (Application.isPlaying)
                {
                    Destroy(instance);
                }
                else
                {
                    DestroyImmediate(instance);
                }
            }

            spawnedPrefabInstances.Clear();
        }

        private BackendApiClient CreateApiClient()
        {
            return new BackendApiClient(backendOptions);
        }

        private ExperimentCreateRequestDto BuildCreateRequest()
        {
            var ticks = DateTime.UtcNow.ToString("yyyyMMdd_HHmmss");
            return new ExperimentCreateRequestDto
            {
                name = createNamePrefix + "_" + ticks,
                description = createDescription,
                map_id = mapId,
                llm_config = new LlmConfigDto
                {
                    model = llmModel,
                    temperature = llmTemperature,
                    max_tokens = Math.Max(1, llmMaxTokens),
                    timeout_ms = Math.Max(1, llmTimeoutMs),
                },
                decision_config = new DecisionConfigDto
                {
                    batch_interval_ms = Math.Max(1, decisionBatchIntervalMs),
                    batch_size = Math.Max(1, decisionBatchSize),
                    fallback_to_algorithm = fallbackToAlgorithm,
                },
                tags = experimentTags ?? Array.Empty<string>(),
            };
        }

        private int GetStartupMapOptionCount()
        {
            if (startupMapConfigs != null && startupMapConfigs.Length > 0)
            {
                return startupMapConfigs.Length;
            }

            return startupMapOptions != null ? startupMapOptions.Length : 0;
        }

        private string BuildStartupMapLabel(int index)
        {
            if (startupMapConfigs != null && startupMapConfigs.Length > 0)
            {
                if (index < 0 || index >= startupMapConfigs.Length)
                {
                    return "(invalid map config index)";
                }

                var config = startupMapConfigs[index];
                return config != null
                    ? config.BuildLabel()
                    : "(invalid map config)";
            }

            if (startupMapOptions == null || index < 0 || index >= startupMapOptions.Length)
            {
                return "(invalid map option index)";
            }

            var option = startupMapOptions[index];
            return option != null
                ? option.BuildLabel()
                : "(invalid map option)";
        }

        private string BuildStartupMapDisplayName(int index)
        {
            if (startupMapConfigs != null && startupMapConfigs.Length > 0)
            {
                if (index < 0 || index >= startupMapConfigs.Length)
                {
                    return "(invalid map config index)";
                }

                var config = startupMapConfigs[index];
                if (config == null)
                {
                    return "(invalid map config)";
                }

                if (!string.IsNullOrWhiteSpace(config.DisplayName))
                {
                    return config.DisplayName.Trim();
                }

                if (!string.IsNullOrWhiteSpace(config.MapId))
                {
                    return config.MapId.Trim();
                }

                return "(unnamed map)";
            }

            if (startupMapOptions == null || index < 0 || index >= startupMapOptions.Length)
            {
                return "(invalid map option index)";
            }

            var option = startupMapOptions[index];
            if (option == null)
            {
                return "(invalid map option)";
            }

            if (!string.IsNullOrWhiteSpace(option.displayName))
            {
                return option.displayName.Trim();
            }

            if (!string.IsNullOrWhiteSpace(option.mapId))
            {
                return option.mapId.Trim();
            }

            return "(unnamed map)";
        }

        private bool TryResolveSelectedStartupMapSelection(out string resolvedMapId, out string resolvedSceneName, out string errorMessage)
        {
            resolvedMapId = string.Empty;
            resolvedSceneName = string.Empty;

            var optionCount = GetStartupMapOptionCount();
            if (optionCount <= 0)
            {
                errorMessage = "Startup failed: no startup map options configured.";
                return false;
            }

            selectedStartupMapIndex = Mathf.Clamp(selectedStartupMapIndex, 0, optionCount - 1);

            if (startupMapConfigs != null && startupMapConfigs.Length > 0)
            {
                var config = startupMapConfigs[selectedStartupMapIndex];
                if (config == null)
                {
                    errorMessage = "Startup failed: selected map config is null.";
                    return false;
                }

                if (!config.IsConfigured())
                {
                    errorMessage = "Startup failed: selected map config is not fully configured (map_id or scene path missing).";
                    return false;
                }

                string sceneName;
                if (!config.TryGetSceneName(out sceneName))
                {
                    errorMessage = "Startup failed: selected map scene path is invalid.";
                    return false;
                }

                resolvedMapId = config.MapId.Trim();
                resolvedSceneName = sceneName;
                errorMessage = string.Empty;
                return true;
            }

            var option = startupMapOptions[selectedStartupMapIndex];
            if (option == null)
            {
                errorMessage = "Startup failed: selected map option is null.";
                return false;
            }

            if (!option.IsConfigured())
            {
                errorMessage = "Startup failed: selected map option is not fully configured (map_id or scene path missing).";
                return false;
            }

            string fallbackSceneName;
            if (!option.TryGetSceneName(out fallbackSceneName))
            {
                errorMessage = "Startup failed: selected map scene path is invalid.";
                return false;
            }

            resolvedMapId = option.mapId.Trim();
            resolvedSceneName = fallbackSceneName;

            errorMessage = string.Empty;
            return true;
        }

        private void TryApplyPendingStartupSelection()
        {
            if (!hasPendingStartupSelection)
            {
                return;
            }

            if (!string.IsNullOrWhiteSpace(pendingStartupMapId))
            {
                mapId = pendingStartupMapId.Trim();
            }

            startupCompleted = true;
            var activeSceneName = SceneManager.GetActiveScene().name;
            currentMap = activeSceneName;
            var expectedScene = pendingStartupSceneName;
            if (!string.IsNullOrWhiteSpace(expectedScene)
                && !string.Equals(expectedScene, activeSceneName, StringComparison.OrdinalIgnoreCase))
            {
                startupStatus = "Startup completed. map_id=" + mapId + ", active_scene=" + activeSceneName + " (expected " + expectedScene + ").";
            }
            else
            {
                startupStatus = "Startup completed. map_id=" + mapId + ", active_scene=" + activeSceneName + ".";
            }

            hasPendingStartupSelection = false;
            pendingStartupMapId = string.Empty;
            pendingStartupSceneName = string.Empty;
        }

        private void TryAutoResolveStartupSelectionFromActiveScene()
        {
            if (!requireStartupBeforeCreate || startupCompleted)
            {
                return;
            }

            var activeSceneName = SceneManager.GetActiveScene().name;
            if (string.IsNullOrWhiteSpace(activeSceneName))
            {
                return;
            }

            if (startupMapConfigs != null && startupMapConfigs.Length > 0)
            {
                for (var i = 0; i < startupMapConfigs.Length; i++)
                {
                    var config = startupMapConfigs[i];
                    if (config == null)
                    {
                        continue;
                    }

                    string sceneName;
                    if (!config.TryGetSceneName(out sceneName))
                    {
                        continue;
                    }

                    if (!string.Equals(sceneName, activeSceneName, StringComparison.OrdinalIgnoreCase))
                    {
                        continue;
                    }

                    selectedStartupMapIndex = i;
                    if (!string.IsNullOrWhiteSpace(config.MapId))
                    {
                        mapId = config.MapId.Trim();
                    }

                    startupCompleted = true;
                    startupStatus = "Startup auto-resolved from active scene. map_id=" + mapId + ", active_scene=" + activeSceneName + ".";
                    return;
                }

                return;
            }

            if (startupMapOptions == null || startupMapOptions.Length <= 0)
            {
                return;
            }

            for (var i = 0; i < startupMapOptions.Length; i++)
            {
                var option = startupMapOptions[i];
                if (option == null)
                {
                    continue;
                }

                string sceneName;
                if (!option.TryGetSceneName(out sceneName))
                {
                    continue;
                }

                if (!string.Equals(sceneName, activeSceneName, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                selectedStartupMapIndex = i;
                if (!string.IsNullOrWhiteSpace(option.mapId))
                {
                    mapId = option.mapId.Trim();
                }

                startupCompleted = true;
                startupStatus = "Startup auto-resolved from active scene. map_id=" + mapId + ", active_scene=" + activeSceneName + ".";
                return;
            }
        }

        private void TryAutoBindTopologySceneConfig()
        {
            if (!startupCompleted)
            {
                return;
            }

            var baseStatus = startupStatus;
            var markerIndex = baseStatus.IndexOf(" TopologySceneConfig ", StringComparison.Ordinal);
            if (markerIndex >= 0)
            {
                baseStatus = baseStatus.Substring(0, markerIndex);
            }

            var activeScene = SceneManager.GetActiveScene();
            var sceneConfigs = FindObjectsOfType<TopologySceneConfig>(true);
            if (sceneConfigs == null || sceneConfigs.Length <= 0)
            {
                startupStatus = baseStatus + " TopologySceneConfig not found in active scene.";
                return;
            }

            TopologySceneConfig selectedConfig = null;
            for (var i = 0; i < sceneConfigs.Length; i++)
            {
                var candidate = sceneConfigs[i];
                if (candidate == null)
                {
                    continue;
                }

                if (candidate.gameObject.scene != activeScene)
                {
                    continue;
                }

                if (selectedConfig == null)
                {
                    selectedConfig = candidate;
                    continue;
                }

                if (candidate.gameObject.activeInHierarchy && !selectedConfig.gameObject.activeInHierarchy)
                {
                    selectedConfig = candidate;
                }
            }

            if (selectedConfig == null)
            {
                selectedConfig = sceneConfigs[0];
            }

            topologySceneConfig = selectedConfig;
            preferSceneTopologyConfig = true;

            string preferredZoneId;
            if (selectedConfig.TryGetPreferredSpawnZoneId(out preferredZoneId)
                && !string.IsNullOrWhiteSpace(preferredZoneId))
            {
                var normalizedZoneId = preferredZoneId.Trim();
                zoneId = normalizedZoneId;
                fixedEntryZoneId = normalizedZoneId;

                TopologyZoneDto preferredZone;
                if (selectedConfig.TryGetZoneById(normalizedZoneId, out preferredZone)
                    && preferredZone != null)
                {
                    if (!string.IsNullOrWhiteSpace(preferredZone.zone_name))
                    {
                        zoneName = preferredZone.zone_name;
                    }

                    if (preferredZone.bounds != null)
                    {
                        zoneMinX = preferredZone.bounds.min_x;
                        zoneMinZ = preferredZone.bounds.min_z;
                        zoneMaxX = preferredZone.bounds.max_x;
                        zoneMaxZ = preferredZone.bounds.max_z;
                    }

                    zoneCapacity = Math.Max(1, preferredZone.capacity);
                }
            }

            var topologyName = selectedConfig.name;
            if (string.IsNullOrWhiteSpace(topologyName))
            {
                topologyName = selectedConfig.gameObject.name;
            }

            startupStatus = baseStatus + " TopologySceneConfig bound: " + topologyName + ".";
        }

        private bool TryBuildTopologyRequest(out TopologyUpsertRequestDto request, out string error)
        {
            if (preferSceneTopologyConfig && topologySceneConfig != null)
            {
                if (topologySceneConfig.TryBuildTopologyRequest(out request, out error))
                {
                    return true;
                }

                if (!string.IsNullOrWhiteSpace(error))
                {
                    error = "Configure topology failed: " + error;
                    return false;
                }
            }

            request = BuildLegacyTopologyRequest();
            if (request == null || request.zones == null || request.zones.Length <= 0)
            {
                error = "Configure topology failed: no valid zones are available.";
                return false;
            }

            error = string.Empty;
            return true;
        }

        private TopologyUpsertRequestDto BuildLegacyTopologyRequest()
        {
            var minX = Mathf.Min(zoneMinX, zoneMaxX);
            var maxX = Mathf.Max(zoneMinX, zoneMaxX);
            var minZ = Mathf.Min(zoneMinZ, zoneMaxZ);
            var maxZ = Mathf.Max(zoneMinZ, zoneMaxZ);

            return new TopologyUpsertRequestDto
            {
                zones = new[]
                {
                    new TopologyZoneDto
                    {
                        zone_id = zoneId,
                        zone_name = zoneName,
                        zone_type = ZoneType.Plaza.ToApiValue(),
                        bounds = new Bounds2DDto
                        {
                            min_x = minX,
                            min_z = minZ,
                            max_x = maxX,
                            max_z = maxZ,
                        },
                        capacity = Math.Max(Math.Max(1, zoneCapacity), GetEffectiveConfiguredCrowdCount()),
                        is_entry_zone = true,
                    },
                },
                exits = Array.Empty<TopologyExitDto>(),
            };
        }

        private bool TryBuildAllocationRequestJson(CrowdAllocationPlanItem[] planItems, string experimentId, out string requestJson, out string error)
        {
            if (planItems == null || planItems.Length <= 0)
            {
                requestJson = string.Empty;
                error = "Configure allocation failed: crowd allocation plan is empty.";
                return false;
            }

            switch (entryZoneAllocationMode)
            {
                case EntryZoneAllocationMode.RandomEntry:
                    {
                        var groups = new CrowdAllocationGroupDto[planItems.Length];
                        for (var i = 0; i < planItems.Length; i++)
                        {
                            groups[i] = new CrowdAllocationGroupDto
                            {
                                type_id = planItems[i].TypeId,
                                count = Math.Max(1, planItems[i].Count),
                                distribution = new RandomEntryDistributionDto
                                {
                                    avoid_exits = allocationAvoidExits,
                                },
                            };
                        }

                        var request = new CrowdAllocationRequestDto
                        {
                            groups = groups,
                            agent_id_prefix = BuildAgentPrefix(experimentId),
                        };
                        requestJson = JsonUtility.ToJson(request);
                        error = string.Empty;
                        return true;
                    }

                case EntryZoneAllocationMode.ZoneWeighted:
                    {
                        var zoneWeights = BuildNormalizedEntryZoneWeights();
                        if (zoneWeights.Length <= 0)
                        {
                            requestJson = string.Empty;
                            error = "Configure allocation failed: zone_weighted mode requires at least one valid entry zone weight.";
                            return false;
                        }

                        var groups = new CrowdAllocationGroupZoneWeightedDto[planItems.Length];
                        for (var i = 0; i < planItems.Length; i++)
                        {
                            groups[i] = new CrowdAllocationGroupZoneWeightedDto
                            {
                                type_id = planItems[i].TypeId,
                                count = Math.Max(1, planItems[i].Count),
                                distribution = new ZoneWeightedDistributionDto
                                {
                                    zone_weights = zoneWeights,
                                    avoid_exits = allocationAvoidExits,
                                },
                            };
                        }

                        var request = new CrowdAllocationRequestZoneWeightedDto
                        {
                            groups = groups,
                            agent_id_prefix = BuildAgentPrefix(experimentId),
                        };
                        requestJson = JsonUtility.ToJson(request);
                        error = string.Empty;
                        return true;
                    }

                case EntryZoneAllocationMode.ZoneFixed:
                    {
                        var resolvedFixedZoneId = ResolveFixedEntryZoneId();
                        if (string.IsNullOrWhiteSpace(resolvedFixedZoneId))
                        {
                            requestJson = string.Empty;
                            error = "Configure allocation failed: zone_fixed mode requires a valid fixed entry zone id.";
                            return false;
                        }

                        var groups = new CrowdAllocationGroupZoneFixedDto[planItems.Length];
                        for (var i = 0; i < planItems.Length; i++)
                        {
                            groups[i] = new CrowdAllocationGroupZoneFixedDto
                            {
                                type_id = planItems[i].TypeId,
                                count = Math.Max(1, planItems[i].Count),
                                distribution = new ZoneFixedDistributionDto
                                {
                                    fixed_zone_id = resolvedFixedZoneId,
                                    avoid_exits = allocationAvoidExits,
                                },
                            };
                        }

                        var request = new CrowdAllocationRequestZoneFixedDto
                        {
                            groups = groups,
                            agent_id_prefix = BuildAgentPrefix(experimentId),
                        };
                        requestJson = JsonUtility.ToJson(request);
                        error = string.Empty;
                        return true;
                    }

                default:
                    requestJson = string.Empty;
                    error = "Configure allocation failed: unsupported allocation mode " + entryZoneAllocationMode;
                    return false;
            }
        }

        private CrowdAllocationPlanItem[] BuildCrowdAllocationPlan(CrowdTypeTemplateDto[] templates, out string error)
        {
            var hasConfiguredEntries = false;
            var mergedCountByType = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            var orderedTypeIds = new List<string>();

            if (crowdTypeAllocations != null && crowdTypeAllocations.Length > 0)
            {
                for (var i = 0; i < crowdTypeAllocations.Length; i++)
                {
                    var config = crowdTypeAllocations[i];
                    if (config == null)
                    {
                        continue;
                    }

                    string normalizedTypeId;
                    int normalizedCount;
                    if (!config.TryGetNormalized(out normalizedTypeId, out normalizedCount))
                    {
                        continue;
                    }

                    hasConfiguredEntries = true;

                    string resolvedTypeId;
                    if (!TryResolveCrowdTypeId(templates, normalizedTypeId, out resolvedTypeId))
                    {
                        continue;
                    }

                    int currentCount;
                    if (mergedCountByType.TryGetValue(resolvedTypeId, out currentCount))
                    {
                        mergedCountByType[resolvedTypeId] = currentCount + normalizedCount;
                    }
                    else
                    {
                        mergedCountByType.Add(resolvedTypeId, normalizedCount);
                        orderedTypeIds.Add(resolvedTypeId);
                    }
                }
            }

            if (hasConfiguredEntries)
            {
                if (orderedTypeIds.Count <= 0)
                {
                    error = "Configure experiment failed: configured crowdTypeAllocations has no valid type/count entries.";
                    return Array.Empty<CrowdAllocationPlanItem>();
                }

                var configuredPlans = new CrowdAllocationPlanItem[orderedTypeIds.Count];
                for (var i = 0; i < orderedTypeIds.Count; i++)
                {
                    var typeId = orderedTypeIds[i];
                    configuredPlans[i] = new CrowdAllocationPlanItem
                    {
                        TypeId = typeId,
                        Count = Math.Max(1, mergedCountByType[typeId]),
                    };
                }

                error = string.Empty;
                return configuredPlans;
            }

            var fallbackTypeId = SelectCrowdTypeId(templates, preferredCrowdTypeId);
            if (string.IsNullOrWhiteSpace(fallbackTypeId))
            {
                error = "Configure experiment failed: cannot resolve a valid crowd type id.";
                return Array.Empty<CrowdAllocationPlanItem>();
            }

            error = string.Empty;
            return new[]
            {
                new CrowdAllocationPlanItem
                {
                    TypeId = fallbackTypeId,
                    Count = Math.Max(1, crowdCount),
                },
            };
        }

        private static bool TryResolveCrowdTypeId(CrowdTypeTemplateDto[] templates, string candidateTypeId, out string resolvedTypeId)
        {
            resolvedTypeId = string.Empty;
            if (templates == null || templates.Length <= 0)
            {
                return false;
            }

            if (string.IsNullOrWhiteSpace(candidateTypeId))
            {
                return false;
            }

            for (var i = 0; i < templates.Length; i++)
            {
                var template = templates[i];
                if (template == null || string.IsNullOrWhiteSpace(template.type_id))
                {
                    continue;
                }

                if (!string.Equals(template.type_id, candidateTypeId, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                resolvedTypeId = template.type_id;
                return true;
            }

            return false;
        }

        private static string BuildCrowdAllocationPlanSummary(CrowdAllocationPlanItem[] planItems)
        {
            if (planItems == null || planItems.Length <= 0)
            {
                return "(empty)";
            }

            var parts = new string[planItems.Length];
            for (var i = 0; i < planItems.Length; i++)
            {
                parts[i] = planItems[i].TypeId + " x" + Math.Max(1, planItems[i].Count);
            }

            return string.Join(", ", parts);
        }

        private int GetEffectiveConfiguredCrowdCount()
        {
            if (crowdTypeAllocations == null || crowdTypeAllocations.Length <= 0)
            {
                return Math.Max(1, crowdCount);
            }

            var totalCount = 0;
            for (var i = 0; i < crowdTypeAllocations.Length; i++)
            {
                var config = crowdTypeAllocations[i];
                if (config == null)
                {
                    continue;
                }

                string typeId;
                int count;
                if (!config.TryGetNormalized(out typeId, out count))
                {
                    continue;
                }

                totalCount += Math.Max(0, count);
            }

            return totalCount > 0 ? totalCount : Math.Max(1, crowdCount);
        }

        private string ResolveFixedEntryZoneId()
        {
            if (!string.IsNullOrWhiteSpace(fixedEntryZoneId))
            {
                return fixedEntryZoneId.Trim();
            }

            if (preferSceneTopologyConfig && topologySceneConfig != null)
            {
                string scenePreferredZoneId;
                if (topologySceneConfig.TryGetPreferredSpawnZoneId(out scenePreferredZoneId)
                    && !string.IsNullOrWhiteSpace(scenePreferredZoneId))
                {
                    return scenePreferredZoneId.Trim();
                }
            }

            return string.IsNullOrWhiteSpace(zoneId) ? string.Empty : zoneId.Trim();
        }

        private bool ApplyDefaultCrowdProfileInternal(bool logResult)
        {
            if (defaultCrowdProfile == null)
            {
                if (logResult)
                {
                    Debug.LogWarning("[ExperimentManager] Apply default crowd profile skipped: profile is null.");
                }

                return false;
            }

            var preferredTypeId = defaultCrowdProfile.PreferredCrowdTypeId;
            if (!string.IsNullOrWhiteSpace(preferredTypeId))
            {
                preferredCrowdTypeId = preferredTypeId;
            }

            crowdCount = defaultCrowdProfile.InitialCrowdCount;
            crowdTypeAllocations = defaultCrowdProfile.CloneCrowdTypeAllocations();
            var crowdTypeDecisionPreferences = defaultCrowdProfile.CloneCrowdTypeDecisionPreferences();

            if (applyDecisionPreferenceFromDefaultProfile
                && decisionBatchScheduler != null)
            {
                decisionBatchScheduler.ApplyLocalDecisionScoringConfig(defaultCrowdProfile.CloneDecisionPreference());
                decisionBatchScheduler.ApplyCrowdTypeDecisionScoringOverrides(crowdTypeDecisionPreferences);
            }

            if (logResult)
            {
                var allocationCount = crowdTypeAllocations != null ? crowdTypeAllocations.Length : 0;
                var crowdTypePreferenceCount = crowdTypeDecisionPreferences != null
                    ? crowdTypeDecisionPreferences.Length
                    : 0;
                Debug.Log(
                    "[ExperimentManager] Applied default crowd profile: " + defaultCrowdProfile.DisplayName
                    + ", preferred_type=" + preferredCrowdTypeId
                    + ", crowd_count=" + crowdCount
                    + ", allocation_entries=" + allocationCount
                    + ", crowd_type_preference_entries=" + crowdTypePreferenceCount
                    + ", apply_preference=" + (applyDecisionPreferenceFromDefaultProfile && decisionBatchScheduler != null));
            }

            RaiseCacheChanged();
            return true;
        }

        private ZoneWeightDto[] BuildNormalizedEntryZoneWeights()
        {
            if (entryZoneWeights == null || entryZoneWeights.Length <= 0)
            {
                return Array.Empty<ZoneWeightDto>();
            }

            var mergedWeights = new Dictionary<string, float>(StringComparer.OrdinalIgnoreCase);
            for (var i = 0; i < entryZoneWeights.Length; i++)
            {
                var config = entryZoneWeights[i];
                if (config == null)
                {
                    continue;
                }

                ZoneWeightDto dto;
                if (!config.TryBuildDto(out dto) || dto == null)
                {
                    continue;
                }

                float current;
                if (mergedWeights.TryGetValue(dto.zone_id, out current))
                {
                    mergedWeights[dto.zone_id] = current + dto.weight;
                }
                else
                {
                    mergedWeights.Add(dto.zone_id, dto.weight);
                }
            }

            if (mergedWeights.Count <= 0)
            {
                return Array.Empty<ZoneWeightDto>();
            }

            var totalWeight = 0f;
            foreach (var pair in mergedWeights)
            {
                totalWeight += Math.Max(0f, pair.Value);
            }

            if (totalWeight <= 0f)
            {
                return Array.Empty<ZoneWeightDto>();
            }

            var normalizedWeights = new List<ZoneWeightDto>(mergedWeights.Count);
            foreach (var pair in mergedWeights)
            {
                var normalizedWeight = Math.Max(0f, pair.Value) / totalWeight;
                if (normalizedWeight <= 0f)
                {
                    continue;
                }

                normalizedWeights.Add(new ZoneWeightDto
                {
                    zone_id = pair.Key,
                    weight = normalizedWeight,
                });
            }

            return normalizedWeights.ToArray();
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

        private static CrowdTypeTemplateDto[] CloneCrowdTypeTemplates(CrowdTypeTemplateDto[] templates)
        {
            if (templates == null || templates.Length <= 0)
            {
                return Array.Empty<CrowdTypeTemplateDto>();
            }

            var cloned = new List<CrowdTypeTemplateDto>(templates.Length);
            for (var i = 0; i < templates.Length; i++)
            {
                var source = templates[i];
                if (source == null || string.IsNullOrWhiteSpace(source.type_id))
                {
                    continue;
                }

                cloned.Add(new CrowdTypeTemplateDto
                {
                    type_id = source.type_id,
                    display_name = source.display_name,
                    color_hex = source.color_hex,
                    base_speed = source.base_speed,
                    social_distance = source.social_distance,
                    tolerance_duration_ms = source.tolerance_duration_ms,
                    attraction_weights = source.attraction_weights != null
                        ? source.attraction_weights.Clone()
                        : CrowdTypeAttractionWeightsDto.CreateDefault(),
                    is_builtin = source.is_builtin,
                    created_at_ms = source.created_at_ms,
                    updated_at_ms = source.updated_at_ms,
                });
            }

            return cloned.ToArray();
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

        private string ResolveExperimentId(string experimentId)
        {
            if (!string.IsNullOrWhiteSpace(experimentId))
            {
                return experimentId.Trim();
            }

            return string.IsNullOrWhiteSpace(lastCreatedExperimentId)
                ? string.Empty
                : lastCreatedExperimentId.Trim();
        }

        private ExperimentCacheRecord EnsureRecord(string experimentId)
        {
            var id = string.IsNullOrWhiteSpace(experimentId) ? string.Empty : experimentId.Trim();
            if (string.IsNullOrEmpty(id))
            {
                id = "(unknown)";
            }

            int index;
            if (!cacheRecordIndex.TryGetValue(id, out index))
            {
                var record = new ExperimentCacheRecord
                {
                    ExperimentId = id,
                    CreatedAtUnixMs = RequestIdUtility.GetUnixTimestampMs(),
                    LastUpdatedAtUnixMs = RequestIdUtility.GetUnixTimestampMs(),
                };
                cacheRecords.Add(record);
                index = cacheRecords.Count - 1;
                cacheRecordIndex[id] = index;
            }

            return cacheRecords[index];
        }

        private void RecordCall(
            ExperimentCacheRecord record,
            string operationName,
            BackendApiRawResponse raw,
            ApiResponseMeta meta,
            bool success,
            string note)
        {
            if (record == null)
            {
                return;
            }

            if (record.Calls == null)
            {
                record.Calls = new List<ExperimentApiCallCacheRecord>();
            }

            var call = new ExperimentApiCallCacheRecord
            {
                Sequence = ++callSequence,
                OperationName = operationName,
                Method = raw != null ? raw.Method : string.Empty,
                Path = raw != null ? raw.Path : string.Empty,
                Url = raw != null ? raw.Url : string.Empty,
                RequestId = raw != null ? raw.RequestId : string.Empty,
                ExperimentId = raw != null && !string.IsNullOrWhiteSpace(raw.ExperimentId) ? raw.ExperimentId : record.ExperimentId,
                RequestBody = raw != null ? raw.RequestBody : string.Empty,
                ResponseBody = raw != null ? raw.Body : string.Empty,
                TransportError = raw != null ? raw.Error : string.Empty,
                HttpStatusCode = raw != null ? raw.StatusCode : 0,
                TransportSuccess = raw != null && raw.TransportSuccess,
                Attempt = raw != null ? raw.Attempt : 0,
                ApiCode = meta != null ? meta.code : int.MinValue,
                ApiMessage = meta != null ? meta.message : string.Empty,
                Success = success,
                Note = note,
                StartedAtUnixMs = raw != null ? raw.SentAtUnixMs : 0,
                FinishedAtUnixMs = raw != null ? raw.ReceivedAtUnixMs : 0,
                DurationMs = raw != null ? raw.DurationMs : 0,
            };

            var now = RequestIdUtility.GetUnixTimestampMs();
            if (call.StartedAtUnixMs <= 0)
            {
                call.StartedAtUnixMs = now;
            }

            if (call.FinishedAtUnixMs <= 0)
            {
                call.FinishedAtUnixMs = now;
            }

            if (call.DurationMs <= 0)
            {
                call.DurationMs = Math.Max(0, call.FinishedAtUnixMs - call.StartedAtUnixMs);
            }

            record.Calls.Add(call);

            var limit = Math.Max(10, maxCachedCallsPerExperiment);
            while (record.Calls.Count > limit)
            {
                record.Calls.RemoveAt(0);
            }

            record.LastOperationName = operationName;
            record.LastOperationSuccess = success;
            record.LastMessage = note;
            record.LastRequestId = call.RequestId;
            record.LastUpdatedAtUnixMs = now;

            RaiseCacheChanged();
        }

        private static ExperimentOperationResult BuildResult(bool success, string message, string experimentId)
        {
            return new ExperimentOperationResult
            {
                Success = success,
                Message = message ?? string.Empty,
                ExperimentId = experimentId ?? string.Empty,
            };
        }

        private void LogOperationResult(string operationName, ExperimentOperationResult result)
        {
            if (!logToConsole || result == null)
            {
                return;
            }

            if (result.Success)
            {
                Debug.Log("[ExperimentManager] " + operationName + " succeeded. " + result.Message);
                return;
            }

            Debug.LogError("[ExperimentManager] " + operationName + " failed. " + result.Message);
        }

        private static void RaiseCacheChanged()
        {
            CacheChanged?.Invoke();
        }
    }
}
