using System;
using System.Collections.Generic;
using System.IO;
using Cysharp.Threading.Tasks;
using CrowdSim.Core;
using CrowdSim.Data.Analytics;
using CrowdSim.Data.Api;
using CrowdSim.Data.Common;
using UnityEngine;
using UnityEngine.Networking;

namespace CrowdSim.Simulation.Analytics
{
    public sealed class HeatmapCompareOverlayRenderer : MonoBehaviour
    {
        public sealed class LayerCaptureExportResult
        {
            public string outputDirectory = string.Empty;
            public string[] layerImagePaths = Array.Empty<string>();
            public string combinedImagePath = string.Empty;
            public int exportedLayerCount;
        }

        private enum HeatmapMetricOption
        {
            StayDuration,
            PassageCount,
            PeakDensity,
            QueueFrequency,
            AvgSpeed,
        }

        private enum HeatmapNormalizationOption
        {
            None,
            MinMax,
            Percentile99,
            Log,
        }

        private enum HeatmapRenderMode
        {
            PerCrowdType,
            AggregatedRedGreen,
            PathTraces,
        }

        private sealed class OverlayLayerVisual
        {
            public string CrowdType = string.Empty;
            public GameObject GameObject;
            public MeshRenderer Renderer;
            public Material Material;
            public Texture2D Texture;
        }

        [Header("Dependencies")]
        [SerializeField] private ExperimentManager experimentManager;

        [Header("Experiment")]
        [SerializeField] private bool onlyWhenExperimentRunning = true;
        [SerializeField] private string explicitExperimentId = string.Empty;

        [Header("Refresh")]
        [SerializeField] private bool autoRefresh = true;
        [SerializeField] private bool refreshOnEnable = true;
        [SerializeField, Min(0.5f)] private float refreshIntervalSeconds = 2f;

        [Header("Query Window")]
        [SerializeField, Min(1000)] private int trailingWindowMs = 30000;
        [SerializeField, Min(0f)] private float valueMulCoefficient = 30f;
        [SerializeField, Min(0f)] private float valuePlusCoefficient = 0.1f;

        [Header("Heatmap Request")]
        [SerializeField] private HeatmapMetricOption metric = HeatmapMetricOption.StayDuration;
        [SerializeField] private HeatmapNormalizationOption normalization = HeatmapNormalizationOption.MinMax;
        [SerializeField] private string[] crowdTypes = Array.Empty<string>();

        [Header("Grid")]
        [SerializeField] private float gridOriginX = -20f;
        [SerializeField] private float gridOriginZ = -20f;
        [SerializeField, Min(0.1f)] private float gridCellSize = 1f;
        [SerializeField, Min(1)] private int gridCols = 40;
        [SerializeField, Min(1)] private int gridRows = 40;

        [Header("Render Mode")]
        [SerializeField] private HeatmapRenderMode renderMode = HeatmapRenderMode.PerCrowdType;

        [Header("Overlay Render")]
        [SerializeField] private Transform overlayRoot;
        [SerializeField] private string overlayShaderName = "Unlit/Transparent";
        [SerializeField] private float baseOverlayY = 0.05f;
        [SerializeField] private float layerHeightStep = 0.002f;
        [SerializeField, Range(0.05f, 1f)] private float layerOpacity = 0.55f;
        [SerializeField] private FilterMode textureFilterMode = FilterMode.Bilinear;
        [SerializeField] private bool clearAbsentLayers = true;

        [Header("Smoothing")]
        [SerializeField] private bool enableGaussianSmoothing = true;
        [SerializeField, Range(1, 5)] private int blurKernelSize = 5;

        [Header("Path Traces")]
        [SerializeField] private string pathTracesCrowdType = "tourist";
        [SerializeField, Range(1, 10)] private int pathTracesSampleRate = 1;
        [SerializeField, Range(1, 500)] private int pathTracesMaxAgents = 100;
        [SerializeField, Min(1000)] private int pathTracesTimeWindowMs = 30000;
        [SerializeField, Range(0.1f, 1f)] private float pathTracesPointIntensity = 1f;
        [SerializeField, Range(0.05f, 0.8f)] private float pathTracesSegmentIntensity = 0.35f;
        [SerializeField, Range(0.1f, 1f)] private float pathTracesOverlayOpacity = 0.75f;
        [SerializeField] private float pathTracesBaseY = 0.1f;
        [SerializeField, Range(1, 4)] private int pathTracesSupersample = 2;
        [SerializeField, Range(0f, 0.5f)] private float pathTracesThreshold = 0.01f;
        [SerializeField, Range(0.1f, 3f)] private float pathTracesGamma = 3f;

        [Header("Manual Refresh")]
        // [SerializeField] private KeyCode manualRefreshKey = KeyCode.F5;
        [SerializeField] private bool manualRefresh = false;

        [Header("Fill Map")]
        [SerializeField] private GameObject filledMap;
        [SerializeField] private GameObject silhouetteMap;

        [Header("Complete Capture")]
        [SerializeField] private Vector3 captureCameraPosition = new Vector3(0f, 30f, 0f);
        [SerializeField] private Vector3 captureCameraEulerAngles = new Vector3(90f, 0f, 0f);
        [SerializeField] private float captureCameraOrthographicSize = 60f;

        [Header("Runtime")]
        [SerializeField] private bool requestInFlight;
        [SerializeField] private int activeLayerCount;
        [SerializeField] private bool activeOverlay = false;
        [SerializeField] private string lastRefreshSummary = "Not started";

        private struct PathTraceCachedRow
        {
            public int TimeMs;
            public string AgentId;
            public float XWorld;
            public float ZWorld;
        }

        private readonly Dictionary<string, OverlayLayerVisual> layerByCrowdType =
            new Dictionary<string, OverlayLayerVisual>(StringComparer.OrdinalIgnoreCase);
        private readonly List<string> staleLayerKeysScratch = new List<string>(16);
        private readonly Dictionary<string, PathTraceCachedRow> cachedPathTraceRows =
            new Dictionary<string, PathTraceCachedRow>(StringComparer.Ordinal);
        private BackendApiClient apiClient;
        private float nextRefreshAtTime;
        private bool allowCompletedExperimentRefresh;

        private void Awake()
        {
            apiClient = new BackendApiClient(ExperimentManager.SharedBackendOptions);
            ResolveExperimentManager();
            EnsureOverlayRoot();
        }

        private void OnEnable()
        {
            if (refreshOnEnable)
            {
                RefreshNow().Forget();
            }
        }

        private void Update()
        {
            if (manualRefresh)
            {
                RefreshNow().Forget();
                manualRefresh = false;
            }

            if (!autoRefresh || requestInFlight)
            {
                return;
            }

            if (Time.unscaledTime < nextRefreshAtTime)
            {
                return;
            }

            RefreshNow().Forget();
        }

        private void OnDestroy()
        {
            ClearAllLayerVisuals();
        }

        public bool IsAutoRefreshEnabled => autoRefresh;

        public Vector3 CaptureCameraPosition => captureCameraPosition;
        public Vector3 CaptureCameraEulerAngles => captureCameraEulerAngles;
        public float CaptureCameraOrthographicSize => captureCameraOrthographicSize;

        [ContextMenu("Enable Auto Refresh")]
        public void EnableAutoRefresh() => SetAutoRefreshEnabled(true, true);

        [ContextMenu("Disable Auto Refresh")]
        public void DisableAutoRefresh() => SetAutoRefreshEnabled(false, false);

        [ContextMenu("Toggle Auto Refresh")]
        public void ToggleAutoRefresh() => SetAutoRefreshEnabled(!autoRefresh, true);

        public void SetAutoRefreshEnabled(bool enabled, bool refreshImmediately)
        {
            autoRefresh = enabled;
            if (!enabled || !Application.isPlaying)
                return;

            nextRefreshAtTime = Time.unscaledTime;
            if (refreshImmediately)
                RefreshNow().Forget();
        }

        [ContextMenu("Toggle Overlay Visual")]
        public void ToggleOverlayVisual()
        {
            activeOverlay = !activeOverlay;
            foreach (var visualPair in layerByCrowdType)
                visualPair.Value.GameObject.SetActive(activeOverlay);
        }

        public void SetOverlayActive(bool active)
        {
            if (activeOverlay == active) return;
            activeOverlay = active;
            if (active)
            {
                // 手动切换时强制刷新（不受 onlyWhenExperimentRunning 限制，完成后端仍可返回缓存数据）
                var experimentId = ResolveExperimentIdForManualRefresh();
                if (!string.IsNullOrWhiteSpace(experimentId))
                {
                    allowCompletedExperimentRefresh = true;
                    ClearAllLayerVisuals();
                    RefreshNow().Forget();
                }
                else if (layerByCrowdType.Count > 0)
                {
                    foreach (var visualPair in layerByCrowdType)
                        visualPair.Value.GameObject.SetActive(true);
                }
            }
            else
            {
                foreach (var visualPair in layerByCrowdType)
                    visualPair.Value.GameObject.SetActive(false);
            }
        }

        public void ResetRuntimeState()
        {
            ClearAllLayerVisuals();
            cachedPathTraceRows.Clear();
            renderMode = HeatmapRenderMode.PerCrowdType;
            activeOverlay = false;
            activeLayerCount = 0;
            requestInFlight = false;
            allowCompletedExperimentRefresh = false;
            lastRefreshSummary = "Not started";
            Debug.Log("[HeatmapCompareOverlayRenderer] Runtime state reset.", this);
        }

        public void SetMapFilled(bool filled)
        {
            if (filled)
            {
                filledMap.SetActive(true);
                if (silhouetteMap != null) silhouetteMap.SetActive(false);
            }
            else
            {
                if (filledMap != null) filledMap.SetActive(false);
                if (silhouetteMap != null) silhouetteMap.SetActive(true);
            }
        }

        [ContextMenu("Refresh Heatmap Overlay Now")]
        public void RefreshOverlayNow()
        {
            manualRefresh = true;
        }

        [ContextMenu("Clear Heatmap Overlay")]
        public void ClearOverlay()
        {
            ClearAllLayerVisuals();
            activeLayerCount = 0;
            lastRefreshSummary = "Overlay cleared.";
        }

        public void SetRenderMode(int mode)
        {
            var clamped = (HeatmapRenderMode)Mathf.Clamp(mode, 0, 2);
            if (renderMode == clamped) return;
            renderMode = clamped;
            ClearAllLayerVisuals();
            RefreshNow().Forget();
        }

        public void SetRenderModePerCrowdType() => SetRenderMode(0);
        public void SetRenderModeAggregatedRedGreen() => SetRenderMode(1);
        public void SetRenderModePathTraces() => SetRenderMode(2);

        // public void SetPathTracesCrowdType(string crowdType)
        // {
        //     if (string.IsNullOrWhiteSpace(crowdType)) return;
        //     var trimmed = crowdType.Trim();
        //     if (string.Equals(pathTracesCrowdType, trimmed, StringComparison.OrdinalIgnoreCase)) return;
        //     pathTracesCrowdType = trimmed;
        //     if (renderMode == HeatmapRenderMode.PathTraces)
        //     {
        //         ClearAllLayerVisuals();
        //         RefreshNow().Forget();
        //     }
        // }

        public void SetCrowdTypeActive(string crowdType, bool active)
        {
            if (string.IsNullOrWhiteSpace(crowdType)) return;
            var trimmed = crowdType.Trim();

            var list = crowdTypes != null
                ? new List<string>(crowdTypes)
                : new List<string>();

            if (active)
            {
                if (!list.Exists(t => string.Equals(t, trimmed, StringComparison.OrdinalIgnoreCase)))
                    list.Add(trimmed);
            }
            else
            {
                list.RemoveAll(t => string.Equals(t, trimmed, StringComparison.OrdinalIgnoreCase));
            }

            crowdTypes = list.ToArray();
        }

        public string[] GetCrowdTypesSnapshot()
        {
            return BuildNormalizedCrowdTypes();
        }

        public void ClearCrowdTypes()
        {
            crowdTypes = Array.Empty<string>();
        }

        public void SetCrowdTypes(string[] types)
        {
            crowdTypes = types ?? Array.Empty<string>();
        }

        public async UniTask RefreshNow()
        {
            if (requestInFlight) return;

            requestInFlight = true;
            try
            {
                EnsureOverlayRoot();
                var experimentId = allowCompletedExperimentRefresh
                    ? ResolveExperimentIdForManualRefresh()
                    : ResolveExperimentId();
                allowCompletedExperimentRefresh = false;
                if (string.IsNullOrWhiteSpace(experimentId))
                {
                    lastRefreshSummary = "Heatmap refresh skipped: experiment id is empty.";
                    if (clearAbsentLayers) ClearAllLayerVisuals();
                    activeLayerCount = 0;
                    return;
                }

                if (renderMode == HeatmapRenderMode.PathTraces)
                {
                    ClearAllLayerVisuals();
                    await FetchAndRenderPathTracesAsync(experimentId);
                    return;
                }

                var normalizedCrowdTypes = BuildNormalizedCrowdTypes();
                if (normalizedCrowdTypes.Length <= 0)
                {
                    lastRefreshSummary = "Heatmap refresh skipped: no crowd type configured.";
                    if (clearAbsentLayers) ClearAllLayerVisuals();
                    activeLayerCount = 0;
                    return;
                }

                var windowEndMs = Mathf.Max(0, Mathf.RoundToInt(Time.timeSinceLevelLoad * 1000f));
                var windowStartMs = Mathf.Max(0, windowEndMs - Math.Max(1000, trailingWindowMs));
                var gridConfig = BuildGridConfig();

                HeatmapCompareDataDto compareData;
                if (normalizedCrowdTypes.Length == 1)
                {
                    compareData = await FetchSingleLayerAsCompareData(
                        experimentId,
                        normalizedCrowdTypes[0],
                        windowStartMs,
                        windowEndMs,
                        gridConfig);
                }
                else
                {
                    compareData = await FetchCompareData(
                        experimentId,
                        normalizedCrowdTypes,
                        windowStartMs,
                        windowEndMs,
                        gridConfig);
                }

                var layers = compareData?.layers ?? Array.Empty<HeatmapCompareLayerDto>();
                ApplyLayerVisuals(layers, compareData?.grid_config ?? gridConfig);

                activeLayerCount = renderMode == HeatmapRenderMode.PerCrowdType ? layers.Length : 1;
                lastRefreshSummary = "Heatmap overlay refreshed. layers=" + activeLayerCount
                    + ", window_ms=" + (windowEndMs - windowStartMs);
            }
            catch (Exception exception)
            {
                lastRefreshSummary = "Heatmap refresh exception: " + exception.Message;
                Debug.LogWarning(lastRefreshSummary, this);
            }
            finally
            {
                requestInFlight = false;
                nextRefreshAtTime = Time.unscaledTime + Mathf.Max(0.5f, refreshIntervalSeconds);
            }
        }

        public async UniTask<LayerCaptureExportResult> CaptureLayerCompositedSnapshotsAsync(
            Camera captureCamera,
            Vector3 capturePosition,
            Vector3 captureEulerAngles,
            float captureOrthographicSize,
            int outputWidth,
            int outputHeight,
            string outputDirectory,
            string filePrefix,
            bool includeCombinedLayerSnapshot)
        {
            var result = new LayerCaptureExportResult();
            var resolvedCamera = captureCamera != null ? captureCamera : Camera.main;
            if (resolvedCamera == null || layerByCrowdType.Count <= 0)
                return result;

            int width = Math.Max(64, outputWidth);
            int height = Math.Max(64, outputHeight);
            var normalizedPrefix = string.IsNullOrWhiteSpace(filePrefix) ? "heatmap" : filePrefix.Trim();
            var normalizedOutputDirectory = string.IsNullOrWhiteSpace(outputDirectory)
                ? Path.Combine(Application.persistentDataPath, "heatmap_capture")
                : outputDirectory;

            Directory.CreateDirectory(normalizedOutputDirectory);
            result.outputDirectory = normalizedOutputDirectory;

            var originalCameraPosition = resolvedCamera.transform.position;
            var originalCameraRotation = resolvedCamera.transform.rotation;
            var originalCameraTargetTexture = resolvedCamera.targetTexture;
            var originalCameraEnabled = resolvedCamera.enabled;

            var layerOriginalActiveStates = new Dictionary<string, bool>(StringComparer.OrdinalIgnoreCase);
            var sortedKeys = new List<string>(layerByCrowdType.Count);
            foreach (var pair in layerByCrowdType)
            {
                if (pair.Value?.GameObject == null) continue;
                sortedKeys.Add(pair.Key);
                layerOriginalActiveStates[pair.Key] = pair.Value.GameObject.activeSelf;
            }
            sortedKeys.Sort((a, b) => string.Compare(a, b, StringComparison.OrdinalIgnoreCase));

            var exportedLayerPaths = new List<string>(sortedKeys.Count);
            try
            {
                resolvedCamera.transform.position = capturePosition;
                resolvedCamera.transform.rotation = Quaternion.Euler(captureEulerAngles);
                resolvedCamera.orthographicSize = captureOrthographicSize;
                if (!resolvedCamera.enabled) resolvedCamera.enabled = true;

                for (int i = 0; i < sortedKeys.Count; i++)
                {
                    var key = sortedKeys[i];
                    SetOnlyTargetLayerActive(key);
                    await UniTask.WaitForEndOfFrame(this);
                    var fileName = normalizedPrefix + "_" + SanitizeFileName(key) + ".png";
                    var filePath = Path.Combine(normalizedOutputDirectory, fileName);
                    CaptureCameraToPng(resolvedCamera, width, height, filePath);
                    exportedLayerPaths.Add(filePath);
                }

                if (includeCombinedLayerSnapshot)
                {
                    SetAllLayersActive(true);
                    await UniTask.WaitForEndOfFrame(this);
                    var combinedPath = Path.Combine(normalizedOutputDirectory, normalizedPrefix + "_combined.png");
                    CaptureCameraToPng(resolvedCamera, width, height, combinedPath);
                    result.combinedImagePath = combinedPath;
                }
            }
            finally
            {
                RestoreLayerActiveStates(layerOriginalActiveStates);
                resolvedCamera.targetTexture = originalCameraTargetTexture;
                resolvedCamera.transform.position = originalCameraPosition;
                resolvedCamera.transform.rotation = originalCameraRotation;
                resolvedCamera.enabled = originalCameraEnabled;
            }

            result.layerImagePaths = exportedLayerPaths.ToArray();
            result.exportedLayerCount = result.layerImagePaths.Length;
            return result;
        }

        private async UniTask<HeatmapCompareDataDto> FetchCompareData(
            string experimentId,
            string[] crowdTypeFilters,
            int startMs,
            int endMs,
            HeatmapGridConfigDto gridConfig)
        {
            var request = new HeatmapCompareRequestDto
            {
                time_start_ms = startMs,
                time_end_ms = endMs,
                grid_config = gridConfig,
                metric = ToApiMetric(metric),
                crowd_types = crowdTypeFilters,
                normalization = ToApiNormalization(normalization),
            };

            var raw = await apiClient.PostJson(
                "/api/v1/experiments/" + experimentId + "/heatmap/compare",
                JsonUtility.ToJson(request),
                experimentId);

            if (!TryEnsureApiSuccess(raw, "POST /api/v1/experiments/{id}/heatmap/compare", out _, out string error))
                throw new InvalidOperationException(error);

            var response = JsonUtility.FromJson<HeatmapCompareResponseDto>(raw.Body);
            return response?.data;
        }

        private async UniTask<HeatmapCompareDataDto> FetchSingleLayerAsCompareData(
            string experimentId,
            string crowdType,
            int startMs,
            int endMs,
            HeatmapGridConfigDto gridConfig)
        {
            var request = new HeatmapRequestDto
            {
                time_start_ms = startMs,
                time_end_ms = endMs,
                grid_config = gridConfig,
                metric = ToApiMetric(metric),
                crowd_type_filter = crowdType,
                zone_id_filter = null,
                normalization = ToApiNormalization(normalization),
            };

            var raw = await apiClient.PostJson(
                "/api/v1/experiments/" + experimentId + "/heatmap",
                JsonUtility.ToJson(request),
                experimentId);

            if (!TryEnsureApiSuccess(raw, "POST /api/v1/experiments/{id}/heatmap", out _, out string error))
                throw new InvalidOperationException(error);

            var response = JsonUtility.FromJson<HeatmapResponseDto>(raw.Body);
            var singleData = response?.data;
            if (singleData == null)
            {
                return new HeatmapCompareDataDto
                {
                    grid_config = gridConfig,
                    metric = ToApiMetric(metric),
                    layers = Array.Empty<HeatmapCompareLayerDto>(),
                };
            }

            return new HeatmapCompareDataDto
            {
                grid_config = singleData.grid_config ?? gridConfig,
                metric = singleData.metric,
                layers = new[]
                {
                    new HeatmapCompareLayerDto
                    {
                        crowd_type = crowdType,
                        display_name = crowdType,
                        color_hex = "#33A1FF",
                        statistics = singleData.statistics,
                        cells = singleData.cells ?? Array.Empty<float>(),
                    },
                },
            };
        }

        private void ApplyLayerVisuals(HeatmapCompareLayerDto[] layers, HeatmapGridConfigDto gridConfig)
        {
            var safeGrid = gridConfig ?? BuildGridConfig();

            if (renderMode == HeatmapRenderMode.PerCrowdType)
            {
                ApplyPerCrowdTypeLayers(layers, safeGrid);
            }
            else if (renderMode == HeatmapRenderMode.AggregatedRedGreen)
            {
                ApplyAggregatedRedGreenLayer(layers, safeGrid);
            }
            else // PathTraces
            {
                ClearAllLayerVisuals();
                _ = FetchAndRenderPathTracesAsync(ResolveExperimentId());
            }
        }

        private void ApplyPerCrowdTypeLayers(HeatmapCompareLayerDto[] layers, HeatmapGridConfigDto gridConfig)
        {
            staleLayerKeysScratch.Clear();
            foreach (var pair in layerByCrowdType)
                staleLayerKeysScratch.Add(pair.Key);

            for (int i = 0; i < layers.Length; i++)
            {
                var layer = layers[i];
                if (layer == null || string.IsNullOrWhiteSpace(layer.crowd_type))
                    continue;

                var key = layer.crowd_type.Trim();
                staleLayerKeysScratch.Remove(key);

                if (!layerByCrowdType.TryGetValue(key, out var visual) || visual == null)
                {
                    visual = CreateLayerVisual(key);
                    layerByCrowdType[key] = visual;
                }

                UpdateLayerVisual(visual, layer, gridConfig, i, false);
            }

            if (clearAbsentLayers)
            {
                foreach (var key in staleLayerKeysScratch)
                {
                    if (layerByCrowdType.TryGetValue(key, out var visual))
                    {
                        DestroyLayerVisual(visual);
                        layerByCrowdType.Remove(key);
                    }
                }
            }

            // Auto-activate layers if overlay is toggled on
            if (activeOverlay)
            {
                foreach (var pair in layerByCrowdType)
                    pair.Value.GameObject.SetActive(true);
            }
        }

        private void ApplyAggregatedRedGreenLayer(HeatmapCompareLayerDto[] layers, HeatmapGridConfigDto gridConfig)
        {
            if (layers == null || layers.Length == 0)
            {
                // 没有数据，清除所有图层
                if (clearAbsentLayers)
                    ClearAllLayerVisuals();
                return;
            }

            // 确定网格大小
            int cols = Math.Max(1, gridConfig.cols);
            int rows = Math.Max(1, gridConfig.rows);
            int totalCells = cols * rows;

            // 聚合：取每个格子的最大值（也可改为平均值）
            float[] aggregated = new float[totalCells];
            bool hasData = false;
            foreach (var layer in layers)
            {
                if (layer?.cells == null || layer.cells.Length == 0)
                    continue;
                int limit = Math.Min(layer.cells.Length, totalCells);
                for (int i = 0; i < limit; i++)
                {
                    float val = layer.cells[i];
                    if (val > aggregated[i])
                    {
                        aggregated[i] = val;
                        hasData = true;
                    }
                }
            }

            if (!hasData)
            {
                // 所有数据为零，清除图层
                if (clearAbsentLayers)
                    ClearAllLayerVisuals();
                return;
            }

            // 构建一个虚拟的 LayerDto
            var aggregatedLayer = new HeatmapCompareLayerDto
            {
                crowd_type = "Aggregated",
                display_name = "Aggregated",
                color_hex = "", // 不使用，由 UpdateLayerVisual 根据红绿模式决定
                cells = aggregated,
            };

            // 确保只有一个图层
            string key = "Aggregated";
            staleLayerKeysScratch.Clear();
            foreach (var pair in layerByCrowdType)
                staleLayerKeysScratch.Add(pair.Key);

            // 移除其他图层
            foreach (var k in staleLayerKeysScratch)
            {
                if (!string.Equals(k, key, StringComparison.OrdinalIgnoreCase))
                {
                    if (layerByCrowdType.TryGetValue(k, out var visual))
                    {
                        DestroyLayerVisual(visual);
                        layerByCrowdType.Remove(k);
                    }
                }
            }

            // 创建或获取聚合图层
            if (!layerByCrowdType.TryGetValue(key, out var aggVisual) || aggVisual == null)
            {
                aggVisual = CreateLayerVisual(key);
                layerByCrowdType[key] = aggVisual;
            }

            // 使用红绿模式更新（useRedGreen = true）
            UpdateLayerVisual(aggVisual, aggregatedLayer, gridConfig, 0, true);

            // Auto-activate if overlay is toggled on
            if (activeOverlay)
                aggVisual.GameObject.SetActive(true);
        }

        private async UniTask FetchAndRenderPathTracesAsync(string experimentId)
        {
            if (string.IsNullOrWhiteSpace(experimentId))
            {
                lastRefreshSummary = "Path traces skipped: experiment id is empty.";
                activeLayerCount = 0;
                return;
            }

            var windowEndMs = Mathf.Max(0, Mathf.RoundToInt(Time.timeSinceLevelLoad * 1000f));
            var windowStartMs = Mathf.Max(0, windowEndMs - Math.Max(1000, pathTracesTimeWindowMs));

            // 解析多个 crowdType（优先 crowdTypes 数组，否则使用单个 pathTracesCrowdType）
            var resolvedCrowdTypes = BuildNormalizedCrowdTypes();
            if (resolvedCrowdTypes.Length <= 0)
            {
                var fallback = string.IsNullOrWhiteSpace(pathTracesCrowdType)
                    ? "tourist"
                    : pathTracesCrowdType.Trim();
                resolvedCrowdTypes = new[] { fallback };
            }

            // 并发发起每个 crowdType 的请求
            var fetchTasks = new UniTask<PathTracesParsedData>[resolvedCrowdTypes.Length];
            for (var i = 0; i < resolvedCrowdTypes.Length; i++)
            {
                fetchTasks[i] = FetchPathTracesAsync(experimentId, windowStartMs, windowEndMs, resolvedCrowdTypes[i]);
            }

            var allParsed = await UniTask.WhenAll(fetchTasks);

            // 收集有效结果
            var validResults = new List<PathTracesParsedData>(allParsed.Length);
            var totalAgents = 0;
            for (var i = 0; i < allParsed.Length; i++)
            {
                var parsed = allParsed[i];
                if (parsed != null && parsed.Traces.Count > 0)
                {
                    validResults.Add(parsed);
                    totalAgents += parsed.Traces.Count;
                }
            }

            if (validResults.Count <= 0)
            {
                lastRefreshSummary = "Path traces fetched but no traces returned.";
                activeLayerCount = 0;
                return;
            }

            // 缓存路径轨迹数据供实验结束时导出 CSV
            CachePathTracesForExport(validResults);

            // Debug.Log("[PathTraces] Full parsed data dump:\n" + DumpParsedPathTraces(parsed));

            RenderPathTracesAsTexture(validResults);
            activeLayerCount = 1;
            lastRefreshSummary = "Path traces rendered. crowd_types=" + validResults.Count
                + ", agents=" + totalAgents
                + ", window_ms=" + (windowEndMs - windowStartMs);
        }

        private void CachePathTracesForExport(List<PathTracesParsedData> allParsed)
        {
            if (allParsed == null || allParsed.Count <= 0) return;

            for (var p = 0; p < allParsed.Count; p++)
            {
                var parsed = allParsed[p];
                if (parsed == null) continue;

                for (var t = 0; t < parsed.Traces.Count; t++)
                {
                    var trace = parsed.Traces[t];
                    if (trace == null || string.IsNullOrWhiteSpace(trace.AgentId)) continue;

                    for (var j = 0; j < trace.Points.Count; j++)
                    {
                        var pt = trace.Points[j];
                        // 以 agentId + timestampMs 去重
                        var key = trace.AgentId + "_" + pt.timestampMs;
                        if (!cachedPathTraceRows.ContainsKey(key))
                        {
                            cachedPathTraceRows[key] = new PathTraceCachedRow
                            {
                                TimeMs = pt.timestampMs,
                                AgentId = trace.AgentId,
                                XWorld = pt.x,
                                ZWorld = pt.z,
                            };
                        }
                    }
                }
            }
        }

        /// <summary>
        /// 将缓存的所有 PathTraces 数据导出为 CSV 文件。
        /// 列：time_ms, agent_id, x_world, y_world, decision_mode
        /// </summary>
        public string ExportCachedPathTracesToCsv(string experimentId, Dictionary<string, string> agentDecisionModes, string outputRoot)
        {
            if (cachedPathTraceRows == null || cachedPathTraceRows.Count <= 0)
            {
                return "path_traces_csv=skipped(no_cached_data)";
            }

            try
            {
                var root = string.IsNullOrWhiteSpace(outputRoot)
                    ? Path.Combine(Application.dataPath, "PathTracesCSV")
                    : outputRoot.Trim();
                if (!Path.IsPathRooted(root))
                {
                    root = Path.Combine(Application.dataPath, root);
                }

                var safeExperimentId = string.IsNullOrWhiteSpace(experimentId)
                    ? "unknown_experiment"
                    : experimentId.Trim();
                var fileName = "path_traces_" + safeExperimentId + "_" + DateTime.Now.ToString("yyyyMMdd_HHmmss") + ".csv";
                Directory.CreateDirectory(root);
                var filePath = Path.Combine(root, fileName);

                // 按时间排序
                var sortedRows = new List<PathTraceCachedRow>(cachedPathTraceRows.Values);
                sortedRows.Sort((a, b) => a.TimeMs.CompareTo(b.TimeMs));

                var decisionModes = agentDecisionModes ?? new Dictionary<string, string>(StringComparer.Ordinal);

                using (var writer = new StreamWriter(filePath, false, System.Text.Encoding.UTF8))
                {
                    writer.WriteLine("time_ms,agent_id,x_world,y_world,decision_mode");
                    for (var i = 0; i < sortedRows.Count; i++)
                    {
                        var row = sortedRows[i];
                        string decisionMode;
                        if (!decisionModes.TryGetValue(row.AgentId, out decisionMode) || string.IsNullOrWhiteSpace(decisionMode))
                        {
                            decisionMode = "normal";
                        }

                        writer.Write(row.TimeMs);
                        writer.Write(',');
                        writer.Write(EscapeCsvField(row.AgentId));
                        writer.Write(',');
                        writer.Write(row.XWorld.ToString("F3", System.Globalization.CultureInfo.InvariantCulture));
                        writer.Write(',');
                        writer.Write(row.ZWorld.ToString("F3", System.Globalization.CultureInfo.InvariantCulture));
                        writer.Write(',');
                        writer.WriteLine(EscapeCsvField(decisionMode));
                    }
                }

                // 导出后清空缓存
                cachedPathTraceRows.Clear();

                return "path_traces_csv=ok"
                    + ", rows=" + sortedRows.Count
                    + ", file=" + fileName;
            }
            catch (Exception exception)
            {
                Debug.LogWarning("Path traces CSV export failed: " + exception.Message, this);
                return "path_traces_csv=failed(" + exception.Message + ")";
            }
        }

        private static string EscapeCsvField(string field)
        {
            if (string.IsNullOrEmpty(field)) return string.Empty;
            if (field.Contains("\"") || field.Contains(",") || field.Contains("\n") || field.Contains("\r"))
            {
                return "\"" + field.Replace("\"", "\"\"") + "\"";
            }
            return field;
        }

        private void RenderPathTracesAsTexture(List<PathTracesParsedData> allParsed)
        {
            if (allParsed == null || allParsed.Count <= 0) return;

            var grid = BuildGridConfig();
            var ss = Mathf.Clamp(pathTracesSupersample, 1, 4);
            var cols = Math.Max(1, grid.cols);
            var rows = Math.Max(1, grid.rows);
            var ssCols = cols * ss;
            var ssRows = rows * ss;
            var ssTotalCells = ssCols * ssRows;
            var cellSize = Mathf.Max(0.1f, grid.cell_size);
            var ssCellSize = cellSize / ss;
            var originX = grid.origin != null ? grid.origin.x : 0f;
            var originZ = grid.origin != null ? grid.origin.z : 0f;

            var cells = new float[ssTotalCells];
            var pointIntensity = Mathf.Clamp01(pathTracesPointIntensity);
            var segmentIntensity = Mathf.Clamp01(pathTracesSegmentIntensity);

            // 将所有 crowdType 的轨迹光栅化到同一个网格中
            for (var p = 0; p < allParsed.Count; p++)
            {
                var parsed = allParsed[p];
                for (var t = 0; t < parsed.Traces.Count; t++)
                {
                    var trace = parsed.Traces[t];
                    var pointCount = trace.Points.Count;
                    if (pointCount < 1) continue;

                    for (var j = 0; j < pointCount; j++)
                    {
                        var pt = trace.Points[j];
                        var cellIndex = WorldToCellIndex(pt.x, pt.z, originX, originZ, ssCellSize, ssCols, ssRows);
                        if (cellIndex >= 0 && cellIndex < ssTotalCells)
                        {
                            cells[cellIndex] = Mathf.Min(pointIntensity, cells[cellIndex] + pointIntensity);
                        }

                        if (j + 1 < pointCount)
                        {
                            var next = trace.Points[j + 1];
                            RasterizeLineSegment(
                                cells, pt.x, pt.z, next.x, next.z,
                                originX, originZ, ssCellSize, ssCols, ssRows,
                                segmentIntensity, pointIntensity);
                        }
                    }
                }
            }

            // 使用第一个 crowdType 的名称作为显示名
            var primaryCrowdType = allParsed[0].CrowdType;
            var displayName = allParsed.Count == 1
                ? "PathTraces_" + primaryCrowdType
                : "PathTraces_" + allParsed.Count + "types";

            var ssGrid = new HeatmapGridConfigDto
            {
                origin = new HeatmapGridOriginDto { x = originX, z = originZ },
                cell_size = ssCellSize,
                cols = ssCols,
                rows = ssRows,
            };

            var layer = new HeatmapCompareLayerDto
            {
                crowd_type = primaryCrowdType,
                display_name = displayName,
                color_hex = allParsed[0].ColorHex,
                statistics = new HeatmapStatisticsDto
                {
                    min_value = 0f,
                    max_value = pointIntensity,
                    non_empty_cells = CountNonEmpty(cells),
                    total_cells = ssTotalCells,
                },
                cells = cells,
            };

            var layers = new[] { layer };
            ApplyPerCrowdTypeLayers(layers, ssGrid);

            // Fix quad to original grid's world-space extent and override texture
            if (layerByCrowdType.TryGetValue(primaryCrowdType, out var visual) && visual != null)
            {
                var worldWidth = cols * cellSize;
                var worldDepth = rows * cellSize;
                var centerX = originX + worldWidth * 0.5f;
                var centerZ = originZ + worldDepth * 0.5f;

                visual.GameObject.transform.position = new Vector3(centerX, pathTracesBaseY, centerZ);
                visual.GameObject.transform.localScale = new Vector3(worldWidth, worldDepth, 1f);

                EnsureLayerTexture(visual, ssCols, ssRows);
                RefreshPathTraceTextureOpacity(visual, cells, ssCols, ssRows);

                if (activeOverlay)
                    visual.GameObject.SetActive(true);
            }
        }

        private void RefreshPathTraceTextureOpacity(
            OverlayLayerVisual visual, float[] cells, int cols, int rows)
        {
            if (visual?.Texture == null) return;

            var opacity = Mathf.Clamp01(pathTracesOverlayOpacity);
            var threshold = Mathf.Clamp(pathTracesThreshold, 0f, 0.5f);
            var gamma = Mathf.Clamp(pathTracesGamma, 0.1f, 3f);

            // Path trace cells are already in [0, pointIntensity] range.
            // Find max for normalization baseline, avoiding division noise.
            var maxRaw = 0.0001f;
            var totalCells = cols * rows;
            for (var i = 0; i < totalCells; i++)
                if (cells[i] > maxRaw) maxRaw = cells[i];

            var invMax = 1f / maxRaw;
            var pixels = new Color[cols * rows];
            for (var i = 0; i < pixels.Length; i++)
            {
                var raw = cells[i];
                if (raw <= 0f)
                {
                    pixels[i] = Color.clear;
                    continue;
                }

                var t = raw * invMax;
                if (t <= threshold)
                {
                    pixels[i] = Color.clear;
                    continue;
                }

                // Remap above threshold, then apply gamma > 1 to suppress low-density paths
                t = (t - threshold) / Mathf.Max(0.0001f, 1f - threshold);
                t = Mathf.Pow(Mathf.Clamp01(t), gamma);

                var color = Color.Lerp(Color.green, Color.red, t);
                color.a = Mathf.Clamp01(t) * opacity;
                pixels[i] = color;
            }

            visual.Texture.SetPixels(pixels);
            visual.Texture.Apply(false, false);
            visual.Material.mainTexture = visual.Texture;
            visual.Material.color = Color.white;
        }

        private static int WorldToCellIndex(float worldX, float worldZ,
            float originX, float originZ, float cellSize, int cols, int rows)
        {
            var col = Mathf.FloorToInt((worldX - originX) / cellSize);
            var row = Mathf.FloorToInt((worldZ - originZ) / cellSize);

            if (col < 0 || col >= cols || row < 0 || row >= rows)
                return -1;

            return row * cols + col;
        }

        private static void RasterizeLineSegment(
            float[] cells,
            float x0, float z0, float x1, float z1,
            float originX, float originZ, float cellSize, int cols, int rows,
            float intensity, float cap)
        {
            var totalCells = cells.Length;
            // Bounding box in cell coordinates, expanded for anti-aliasing falloff
            var halfCell = cellSize * 0.5f;
            var falloffRadius = cellSize * 0.75f;
            var minX = Mathf.Min(x0, x1) - falloffRadius;
            var maxX = Mathf.Max(x0, x1) + falloffRadius;
            var minZ = Mathf.Min(z0, z1) - falloffRadius;
            var maxZ = Mathf.Max(z0, z1) + falloffRadius;

            var col0 = Mathf.Clamp(Mathf.FloorToInt((minX - originX) / cellSize), 0, cols - 1);
            var col1 = Mathf.Clamp(Mathf.FloorToInt((maxX - originX) / cellSize), 0, cols - 1);
            var row0 = Mathf.Clamp(Mathf.FloorToInt((minZ - originZ) / cellSize), 0, rows - 1);
            var row1 = Mathf.Clamp(Mathf.FloorToInt((maxZ - originZ) / cellSize), 0, rows - 1);

            var dx = x1 - x0;
            var dz = z1 - z0;
            var segLenSq = dx * dx + dz * dz;
            var invSegLen = segLenSq > 0.0001f ? 1f / Mathf.Sqrt(segLenSq) : 0f;

            for (var row = row0; row <= row1; row++)
            {
                for (var col = col0; col <= col1; col++)
                {
                    var cx = originX + (col + 0.5f) * cellSize;
                    var cz = originZ + (row + 0.5f) * cellSize;
                    var dist = PointToSegmentDistance(cx, cz, x0, z0, dx, dz, segLenSq, invSegLen);
                    var falloff = 1f - Mathf.Clamp01(dist / falloffRadius);
                    if (falloff <= 0f) continue;

                    var index = row * cols + col;
                    if (index < 0 || index >= totalCells) continue;
                    cells[index] = Mathf.Min(cap, cells[index] + intensity * falloff);
                }
            }
        }

        private static float PointToSegmentDistance(
            float px, float pz,
            float x0, float z0, float dx, float dz, float segLenSq, float invSegLen)
        {
            if (segLenSq < 0.0001f)
            {
                var d0 = px - x0;
                var d1 = pz - z0;
                return Mathf.Sqrt(d0 * d0 + d1 * d1);
            }

            // Project point onto line, clamp to segment
            var t = Mathf.Clamp01(((px - x0) * dx + (pz - z0) * dz) * invSegLen * invSegLen);
            var projX = x0 + t * dx;
            var projZ = z0 + t * dz;
            var ddx = px - projX;
            var ddz = pz - projZ;
            return Mathf.Sqrt(ddx * ddx + ddz * ddz);
        }

        private static int CountNonEmpty(float[] cells)
        {
            var count = 0;
            if (cells == null) return count;
            for (var i = 0; i < cells.Length; i++)
                if (cells[i] > 0.0001f) count++;
            return count;
        }

        private static string TruncateBody(string body, int maxLength)
        {
            if (string.IsNullOrWhiteSpace(body))
                return "(empty)";
            if (body.Length <= maxLength)
                return body;
            return body.Substring(0, maxLength) + "...(truncated, total=" + body.Length + ")";
        }

        private static string DumpParsedPathTraces(PathTracesParsedData parsed)
        {
            if (parsed == null) return "(null)";
            var sb = new System.Text.StringBuilder();
            sb.AppendLine("--- PathTracesParsedData ---");
            sb.AppendLine("  CrowdType: " + parsed.CrowdType);
            sb.AppendLine("  DisplayName: " + parsed.DisplayName);
            sb.AppendLine("  ColorHex: " + parsed.ColorHex);
            sb.AppendLine("  AgentCountInResponse: " + parsed.AgentCountInResponse);
            sb.AppendLine("  TotalAgentsOfType: " + parsed.TotalAgentsOfType);
            sb.AppendLine("  SampleRate: " + parsed.SampleRate);
            sb.AppendLine("  Traces.Count: " + parsed.Traces.Count);

            for (var t = 0; t < parsed.Traces.Count; t++)
            {
                var trace = parsed.Traces[t];
                sb.AppendLine("  Trace[" + t + "]:");
                sb.AppendLine("    AgentId: " + trace.AgentId);
                sb.AppendLine("    PointCount: " + trace.PointCount);
                sb.AppendLine("    Points.Count: " + trace.Points.Count);

                for (var p = 0; p < trace.Points.Count; p++)
                {
                    var pt = trace.Points[p];
                    sb.AppendLine("      [" + p + "] ts=" + pt.timestampMs + " x=" + pt.x.ToString("F3") + " z=" + pt.z.ToString("F3"));
                }
            }

            sb.Append("--- End PathTracesParsedData ---");
            return sb.ToString();
        }

        private async UniTask<PathTracesParsedData> FetchPathTracesAsync(
            string experimentId, int startMs, int endMs, string crowdType)
        {
            var resolvedCrowdType = string.IsNullOrWhiteSpace(crowdType)
                ? "tourist"
                : crowdType.Trim();
            var queryParams = "?crowd_type=" + UnityWebRequest.EscapeURL(resolvedCrowdType)
                + "&sample_rate=" + Mathf.Clamp(pathTracesSampleRate, 1, 10)
                + "&max_agents=" + Mathf.Clamp(pathTracesMaxAgents, 1, 500)
                + "&time_start_ms=" + startMs
                + "&time_end_ms=" + endMs;

            var path = "/api/v1/experiments/" + experimentId + "/path-traces" + queryParams;
            var raw = await apiClient.Get(path, experimentId);

            if (!TryEnsureApiSuccess(raw, "GET /api/v1/experiments/{id}/path-traces", out _, out string error))
                throw new InvalidOperationException(error);

            var response = JsonUtility.FromJson<PathTracesResponseDto>(raw.Body);
            var data = response?.data;
            if (data == null || data.traces == null || data.traces.Length <= 0)
            {
                // Debug.Log("[PathTraces] " + path + " → no traces in response. body_preview="
                //     + TruncateBody(raw.Body, 500));
                return null;
            }

            // Debug.Log("[PathTraces] " + path
            //     + " → crowd_type=" + data.crowd_type
            //     + ", total_agents_of_type=" + data.total_agents_of_type
            //     + ", agent_count_in_response=" + data.agent_count_in_response
            //     + ", sample_rate=" + data.sample_rate
            //     + ", traces_count=" + data.traces.Length
            //     + ", body_preview=" + TruncateBody(raw.Body, 2000));

            var parsed = new PathTracesParsedData
            {
                CrowdType = data.crowd_type ?? string.Empty,
                DisplayName = data.display_name ?? string.Empty,
                ColorHex = data.color_hex ?? "#FFFFFF",
                AgentCountInResponse = data.agent_count_in_response,
                TotalAgentsOfType = data.total_agents_of_type,
                SampleRate = data.sample_rate,
            };

            // Manually parse points from raw JSON since JsonUtility can't handle float[][]
            var body = raw.Body;
            var tracesStart = body.IndexOf("\"traces\"");
            if (tracesStart < 0) return parsed;

            for (var i = 0; i < data.traces.Length; i++)
            {
                var traceDto = data.traces[i];
                if (traceDto == null) continue;
                var agentTrace = new PathTraceParsedAgent
                {
                    AgentId = traceDto.agent_id ?? string.Empty,
                    PointCount = traceDto.point_count,
                };
                ParsePointsForTrace(body, i, agentTrace.Points);
                if (agentTrace.Points.Count > 0)
                    parsed.Traces.Add(agentTrace);
            }

            return parsed;
        }

        private static void ParsePointsForTrace(
            string body, int traceIndex, List<PathTraceParsedPoint> points)
        {
            // Find N-th occurrence of \"points\":[ after \"traces\"
            var searchStart = body.IndexOf("\"traces\"", StringComparison.Ordinal);
            if (searchStart < 0) return;

            for (var n = 0; n <= traceIndex; n++)
            {
                searchStart = body.IndexOf("\"points\"", searchStart + 1, StringComparison.Ordinal);
                if (searchStart < 0) return;
            }

            // Skip past \"points\": to the outer [
            var outerBracket = body.IndexOf('[', searchStart);
            if (outerBracket < 0) return;

            // Find matching close bracket for the outer array by counting depth
            var closePos = outerBracket;
            var depth = 0;
            for (var i = outerBracket; i < body.Length; i++)
            {
                if (body[i] == '[') depth++;
                else if (body[i] == ']')
                {
                    depth--;
                    if (depth == 0)
                    {
                        closePos = i;
                        break;
                    }
                }
            }

            // Extract the inner content between outer [ and ]
            // Structure: [[a,b,c],[d,e,f],...] → inner is [a,b,c],[d,e,f],...
            var innerStart = outerBracket + 1;
            var innerLen = closePos - innerStart;
            if (innerLen <= 0) return;
            var inner = body.Substring(innerStart, innerLen);

            // Split by "],["
            var tupleStrings = inner.Split(new[] { "],[" }, StringSplitOptions.None);
            for (var t = 0; t < tupleStrings.Length; t++)
            {
                var tupleStr = tupleStrings[t];
                // Strip leading [ (first tuple) or trailing ] (last tuple)
                tupleStr = tupleStr.TrimStart('[').TrimEnd(']');
                var parts = tupleStr.Split(',');
                if (parts.Length >= 3)
                {
                    int.TryParse(parts[0].Trim(), out var ts);
                    float.TryParse(parts[1].Trim(),
                        System.Globalization.NumberStyles.Float,
                        System.Globalization.CultureInfo.InvariantCulture,
                        out var x);
                    float.TryParse(parts[2].Trim(),
                        System.Globalization.NumberStyles.Float,
                        System.Globalization.CultureInfo.InvariantCulture,
                        out var z);
                    points.Add(new PathTraceParsedPoint { timestampMs = ts, x = x, z = z });
                }
            }
        }

        private OverlayLayerVisual CreateLayerVisual(string crowdTypeKey)
        {
            EnsureOverlayRoot();
            var layerObject = GameObject.CreatePrimitive(PrimitiveType.Quad);
            layerObject.name = "HeatmapLayer_" + crowdTypeKey;
            layerObject.transform.SetParent(overlayRoot, false);

            var collider = layerObject.GetComponent<Collider>();
            if (collider != null) Destroy(collider);

            var renderer = layerObject.GetComponent<MeshRenderer>();
            var shader = Shader.Find(overlayShaderName);
            if (shader == null) shader = Shader.Find("Unlit/Transparent");
            if (shader == null) shader = Shader.Find("Sprites/Default");

            var material = new Material(shader);
            renderer.sharedMaterial = material;
            layerObject.SetActive(false);

            return new OverlayLayerVisual
            {
                CrowdType = crowdTypeKey,
                GameObject = layerObject,
                Renderer = renderer,
                Material = material,
                Texture = null,
            };
        }

        private void UpdateLayerVisual(
            OverlayLayerVisual visual,
            HeatmapCompareLayerDto layer,
            HeatmapGridConfigDto gridConfig,
            int layerIndex,
            bool useRedGreen)
        {
            if (visual == null || layer == null || gridConfig == null)
                return;

            var cols = Math.Max(1, gridConfig.cols);
            var rows = Math.Max(1, gridConfig.rows);
            EnsureLayerTexture(visual, cols, rows);

            // 归一化
            var normalizedCells = NormalizeCells(layer.cells, cols * rows);

            // 高斯平滑
            if (enableGaussianSmoothing && blurKernelSize > 1)
                normalizedCells = ApplyGaussianBlur(normalizedCells, cols, rows, blurKernelSize);

            var pixels = new Color[cols * rows];
            for (int i = 0; i < pixels.Length; i++)
            {
                float intensity = normalizedCells[i];
                float alpha = Mathf.Clamp01(intensity) * Mathf.Clamp01(layerOpacity);

                Color color;
                if (useRedGreen)
                {
                    // 红绿渐变：低→绿，高→红
                    color = Color.Lerp(Color.green, Color.red, intensity);
                    color.a = alpha;
                }
                else
                {
                    var baseColor = ResolveLayerColor(layer.color_hex, layerIndex);
                    color = new Color(baseColor.r, baseColor.g, baseColor.b, alpha);
                }
                pixels[i] = color;
            }

            visual.Texture.SetPixels(pixels);
            visual.Texture.Apply(false, false);
            visual.Material.mainTexture = visual.Texture;
            visual.Material.color = Color.white;

            // 设置位置和缩放
            float width = cols * Mathf.Max(0.1f, gridConfig.cell_size);
            float depth = rows * Mathf.Max(0.1f, gridConfig.cell_size);
            float centerX = gridConfig.origin != null ? gridConfig.origin.x + width * 0.5f : width * 0.5f;
            float centerZ = gridConfig.origin != null ? gridConfig.origin.z + depth * 0.5f : depth * 0.5f;

            var t = visual.GameObject.transform;
            t.position = new Vector3(centerX, baseOverlayY + layerIndex * layerHeightStep, centerZ);
            t.rotation = Quaternion.Euler(90f, 0f, 0f);
            t.localScale = new Vector3(width, depth, 1f);
            // 不在此处激活，由外部控制显示
        }

        private float[] NormalizeCells(float[] cells, int expectedCount)
        {
            var safeCount = Math.Max(1, expectedCount);
            var values = new float[safeCount];
            if (cells == null || cells.Length <= 0)
                return values;

            var maxValue = 0f;
            int limit = Math.Min(safeCount, cells.Length);
            for (int i = 0; i < limit; i++)
            {
                float value = 0f;
                if (cells[i] > 0 + Mathf.Epsilon)
                    value = cells[i] * valueMulCoefficient + valuePlusCoefficient;
                value = Mathf.Max(0f, value);
                values[i] = value;
                if (value > maxValue) maxValue = value;
            }

            if (maxValue <= 0f)
                return values;

            for (int i = 0; i < values.Length; i++)
                values[i] = Mathf.Clamp01(values[i] / maxValue);

            return values;
        }

        private float[] ApplyGaussianBlur(float[] data, int cols, int rows, int kernelSize)
        {
            if (kernelSize <= 1 || data == null || data.Length < 4)
                return data;

            if (kernelSize % 2 == 0) kernelSize += 1;
            int half = kernelSize / 2;
            float sigma = kernelSize / 3.0f;
            float[] kernel = new float[kernelSize];
            float sum = 0f;
            for (int i = 0; i < kernelSize; i++)
            {
                int x = i - half;
                kernel[i] = Mathf.Exp(-(x * x) / (2 * sigma * sigma));
                sum += kernel[i];
            }
            for (int i = 0; i < kernelSize; i++)
                kernel[i] /= sum;

            float[] temp = new float[data.Length];
            for (int r = 0; r < rows; r++)
            {
                for (int c = 0; c < cols; c++)
                {
                    float val = 0f;
                    for (int k = 0; k < kernelSize; k++)
                    {
                        int offset = k - half;
                        int colClamped = Mathf.Clamp(c + offset, 0, cols - 1);
                        val += data[r * cols + colClamped] * kernel[k];
                    }
                    temp[r * cols + c] = val;
                }
            }

            float[] result = new float[data.Length];
            for (int r = 0; r < rows; r++)
            {
                for (int c = 0; c < cols; c++)
                {
                    float val = 0f;
                    for (int k = 0; k < kernelSize; k++)
                    {
                        int offset = k - half;
                        int rowClamped = Mathf.Clamp(r + offset, 0, rows - 1);
                        val += temp[rowClamped * cols + c] * kernel[k];
                    }
                    result[r * cols + c] = val;
                }
            }
            return result;
        }

        private void EnsureLayerTexture(OverlayLayerVisual visual, int width, int height)
        {
            if (visual.Texture != null && visual.Texture.width == width && visual.Texture.height == height)
                return;

            if (visual.Texture != null)
                Destroy(visual.Texture);

            visual.Texture = new Texture2D(width, height, TextureFormat.RGBA32, false)
            {
                wrapMode = TextureWrapMode.Clamp,
                filterMode = textureFilterMode,
            };
        }

        private void ClearAllLayerVisuals()
        {
            foreach (var pair in layerByCrowdType)
                DestroyLayerVisual(pair.Value);
            layerByCrowdType.Clear();
            staleLayerKeysScratch.Clear();
        }

        private void SetOnlyTargetLayerActive(string targetCrowdType)
        {
            foreach (var pair in layerByCrowdType)
            {
                if (pair.Value?.GameObject == null) continue;
                bool isTarget = string.Equals(pair.Key, targetCrowdType, StringComparison.OrdinalIgnoreCase);
                pair.Value.GameObject.SetActive(isTarget);
            }
        }

        private void SetAllLayersActive(bool active)
        {
            foreach (var pair in layerByCrowdType)
            {
                if (pair.Value?.GameObject != null)
                    pair.Value.GameObject.SetActive(active);
            }
        }

        private void RestoreLayerActiveStates(Dictionary<string, bool> states)
        {
            if (states == null) return;
            foreach (var pair in layerByCrowdType)
            {
                if (pair.Value?.GameObject == null) continue;
                if (states.TryGetValue(pair.Key, out bool active))
                    pair.Value.GameObject.SetActive(active);
            }
        }

        private static void CaptureCameraToPng(Camera camera, int width, int height, string filePath)
        {
            var renderTexture = new RenderTexture(width, height, 24, RenderTextureFormat.ARGB32);
            var previousActive = RenderTexture.active;
            var previousTarget = camera.targetTexture;
            try
            {
                camera.targetTexture = renderTexture;
                RenderTexture.active = renderTexture;
                camera.Render();

                var texture = new Texture2D(width, height, TextureFormat.RGB24, false);
                texture.ReadPixels(new Rect(0, 0, width, height), 0, 0);
                texture.Apply(false, false);

                var pngBytes = texture.EncodeToPNG();
                File.WriteAllBytes(filePath, pngBytes);
                Destroy(texture);
            }
            finally
            {
                camera.targetTexture = previousTarget;
                RenderTexture.active = previousActive;
                renderTexture.Release();
                Destroy(renderTexture);
            }
        }

        private static string SanitizeFileName(string source)
        {
            var fallback = string.IsNullOrWhiteSpace(source) ? "layer" : source.Trim();
            var invalidChars = Path.GetInvalidFileNameChars();
            var buffer = new char[fallback.Length];
            for (int i = 0; i < fallback.Length; i++)
            {
                char ch = fallback[i];
                bool invalid = false;
                for (int j = 0; j < invalidChars.Length; j++)
                {
                    if (ch == invalidChars[j]) { invalid = true; break; }
                }
                buffer[i] = invalid ? '_' : ch;
            }
            return new string(buffer);
        }

        private static void DestroyLayerVisual(OverlayLayerVisual visual)
        {
            if (visual == null) return;
            if (visual.Texture != null) { Destroy(visual.Texture); visual.Texture = null; }
            if (visual.Material != null) { Destroy(visual.Material); visual.Material = null; }
            if (visual.GameObject != null) { Destroy(visual.GameObject); visual.GameObject = null; }
        }

        private HeatmapGridConfigDto BuildGridConfig()
        {
            return new HeatmapGridConfigDto
            {
                origin = new HeatmapGridOriginDto { x = gridOriginX, z = gridOriginZ },
                cell_size = Mathf.Max(0.1f, gridCellSize),
                cols = Math.Max(1, gridCols),
                rows = Math.Max(1, gridRows),
            };
        }

        private string[] BuildNormalizedCrowdTypes()
        {
            if (crowdTypes == null || crowdTypes.Length == 0)
                return Array.Empty<string>();

            var result = new List<string>(crowdTypes.Length);
            var set = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var t in crowdTypes)
            {
                var trimmed = t?.Trim();
                if (string.IsNullOrWhiteSpace(trimmed) || !set.Add(trimmed))
                    continue;
                result.Add(trimmed);
                if (result.Count >= 6) break;
            }
            return result.ToArray();
        }

        private string ResolveExperimentId()
        {
            if (!string.IsNullOrWhiteSpace(explicitExperimentId))
                return explicitExperimentId.Trim();

            var manager = ResolveExperimentManager();
            if (manager == null) return string.Empty;

            if (!string.IsNullOrWhiteSpace(manager.RunningExperimentId))
                return manager.RunningExperimentId.Trim();

            if (onlyWhenExperimentRunning)
                return string.Empty;

            return manager.LastCreatedExperimentId?.Trim() ?? string.Empty;
        }

        /// <summary>
        /// 用于 UI 手动切换等场景，不受 onlyWhenExperimentRunning 限制。
        /// 实验完成后热力图后端仍可返回缓存数据。
        /// </summary>
        private string ResolveExperimentIdForManualRefresh()
        {
            if (!string.IsNullOrWhiteSpace(explicitExperimentId))
                return explicitExperimentId.Trim();

            var manager = ResolveExperimentManager();
            if (manager == null) return string.Empty;

            if (!string.IsNullOrWhiteSpace(manager.RunningExperimentId))
                return manager.RunningExperimentId.Trim();

            // 即使实验已完成（RunningExperimentId 为空），也允许通过 LastCreatedExperimentId 查询
            return manager.LastCreatedExperimentId?.Trim() ?? string.Empty;
        }

        private ExperimentManager ResolveExperimentManager()
        {
            if (experimentManager != null) return experimentManager;
            experimentManager = ExperimentManager.ActiveInstance;
            if (experimentManager != null) return experimentManager;
            experimentManager = FindObjectOfType<ExperimentManager>();
            return experimentManager;
        }

        private void EnsureOverlayRoot()
        {
            if (overlayRoot != null) return;
            var root = new GameObject("HeatmapOverlayRoot");
            root.transform.SetParent(transform, false);
            overlayRoot = root.transform;
        }

        private static string ToApiMetric(HeatmapMetricOption option)
        {
            return option switch
            {
                HeatmapMetricOption.StayDuration => "stay_duration",
                HeatmapMetricOption.PassageCount => "passage_count",
                HeatmapMetricOption.PeakDensity => "peak_density",
                HeatmapMetricOption.QueueFrequency => "queue_frequency",
                HeatmapMetricOption.AvgSpeed => "avg_speed",
                _ => "stay_duration",
            };
        }

        private static string ToApiNormalization(HeatmapNormalizationOption option)
        {
            return option switch
            {
                HeatmapNormalizationOption.None => "none",
                HeatmapNormalizationOption.MinMax => "min_max",
                HeatmapNormalizationOption.Percentile99 => "percentile_99",
                HeatmapNormalizationOption.Log => "log",
                _ => "min_max",
            };
        }

        private static Color ResolveLayerColor(string colorHex, int fallbackIndex)
        {
            if (!string.IsNullOrWhiteSpace(colorHex) && ColorUtility.TryParseHtmlString(colorHex.Trim(), out Color color))
                return color;

            var palette = new[]
            {
                new Color(0.20f, 0.63f, 1.0f, 1f),
                new Color(1.0f, 0.35f, 0.35f, 1f),
                new Color(0.25f, 0.85f, 0.45f, 1f),
                new Color(1.0f, 0.75f, 0.15f, 1f),
                new Color(0.95f, 0.40f, 0.85f, 1f),
                new Color(0.35f, 0.85f, 0.90f, 1f),
            };
            int index = Math.Abs(fallbackIndex) % palette.Length;
            return palette[index];
        }

        private static bool TryEnsureApiSuccess(BackendApiRawResponse raw, string operation, out ApiResponseMeta meta, out string error)
        {
            meta = null;
            if (raw == null) { error = operation + " failed: empty response."; return false; }
            if (!raw.TransportSuccess) { error = operation + " transport error: " + raw.Error; return false; }
            if (raw.StatusCode < 200 || raw.StatusCode >= 300)
            {
                var detail = string.IsNullOrWhiteSpace(raw.Body) ? raw.Error : raw.Body;
                error = operation + " failed with HTTP " + raw.StatusCode + ". " + detail;
                return false;
            }
            if (string.IsNullOrWhiteSpace(raw.Body)) { error = operation + " failed: response body is empty."; return false; }

            meta = JsonUtility.FromJson<ApiResponseMeta>(raw.Body);
            if (meta == null) { error = operation + " failed: cannot parse response meta."; return false; }
            if (meta.code != 0) { error = operation + " failed: code=" + meta.code + ", message=" + meta.message; return false; }

            error = string.Empty;
            return true;
        }

        private void ClientProvided()
        {
            /*
                  setStatus("正在着色热力图 ...");
      await sleepFrame();

      const imageData = dctx.getImageData(0, 0, densityCanvas.width, densityCanvas.height);
      const src = imageData.data;
      let maxAlpha = 0;
      for (let i = 3; i < src.length; i += 4) {
        if (src[i] > maxAlpha) maxAlpha = src[i];
      }

      const colorCanvas = document.createElement("canvas");
      colorCanvas.width = densityCanvas.width;
      colorCanvas.height = densityCanvas.height;
      const colorCtx = colorCanvas.getContext("2d");
      const out = colorCtx.createImageData(colorCanvas.width, colorCanvas.height);
      const dst = out.data;

      for (let i = 0; i < src.length; i += 4) {
        let t = maxAlpha > 0 ? src[i + 3] / maxAlpha : 0;
        if (t <= threshold) {
          dst[i + 3] = 0;
          continue;
        }
        t = (t - threshold) / Math.max(0.0001, 1 - threshold);
        t = Math.pow(clamp01(t), 0.62);
        const [r, g, b] = heatColor(t);
        dst[i] = r;
        dst[i + 1] = g;
        dst[i + 2] = b;
        dst[i + 3] = Math.round(255 * opacity * clamp01(t));
      }
      colorCtx.putImageData(out, 0, 0);

      const heatCtx = els.heatCanvas.getContext("2d");
      heatCtx.clearRect(0, 0, state.imageWidth, state.imageHeight);
      heatCtx.imageSmoothingEnabled = true;
      heatCtx.drawImage(colorCanvas, 0, 0, state.imageWidth, state.imageHeight);

      drawTrackOverlay(tracks, maxFrameGap);

      els.exportBtn.disabled = false;
      els.renderBtn.disabled = false;
      state.lastRender = { rows: rows.length, tracks: tracks.size, stampCount, maxAlpha };
      updateMeta(`已渲染 ${formatInt(rows.length)} 条记录`);
      setStatus([
        `<span class="ok">渲染完成</span>`,
        `记录：<strong>${formatInt(rows.length)}</strong>`,
        `轨迹 ID：<strong>${formatInt(tracks.size)}</strong>`,
        `采样点：<strong>${formatInt(stampCount)}</strong>`,
        `质量：<strong>${Math.round(quality * 100)}%</strong>`
      ].join("\n"));
    }

    function drawTrackOverlay(tracks, maxFrameGap) {
      const ctx = els.trackCanvas.getContext("2d");
      ctx.clearRect(0, 0, state.imageWidth, state.imageHeight);
      if (!els.showTrackLine.checked) return;

      const lineWidth = Math.max(0.1, Number(els.lineWidth.value) || 1.5);
      ctx.save();
      ctx.lineWidth = lineWidth;
      ctx.strokeStyle = "rgba(255,255,255,0.55)";
      ctx.shadowColor = "rgba(0,0,0,0.45)";
      ctx.shadowBlur = 2;

      for (const points of tracks.values()) {
        if (points.length < 2) continue;
        ctx.beginPath();
        let penDown = false;
        for (let i = 0; i < points.length; i++) {
          const p = points[i];
          if (i === 0 || p.frame - points[i - 1].frame > maxFrameGap || p.frame - points[i - 1].frame <= 0) {
            if (penDown) ctx.stroke();
            ctx.beginPath();
            ctx.moveTo(p.cx, p.cy);
            penDown = true;
          } else {
            ctx.lineTo(p.cx, p.cy);
          }
        }
        if (penDown) ctx.stroke();
      }
      ctx.restore();
    }
            */
        }
    }
}