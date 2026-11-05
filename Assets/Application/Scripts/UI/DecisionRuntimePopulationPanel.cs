using System;
using System.Collections.Generic;
using System.Globalization;
using CrowdSim.Core;
using CrowdSim.Data.UI;
using TMPro;
using UnityEngine;
using CrowdSim.Simulation.Analytics;
using CrowdSim.Simulation.Decision;
using CrowdSim.Simulation.Trajectory;
using UnityEngine.SceneManagement;

namespace CrowdSim.UI
{
    public sealed class DecisionRuntimePopulationPanel : MonoBehaviour
    {
        private sealed class ItemView
        {
            public GameObject Root;
            public TMP_Text Label;
        }

        [Header("Dependencies")]
        [SerializeField] private ExperimentManager experimentManager;

        [Header("Refresh")]
        [SerializeField] private bool refreshOnEnable = true;
        [SerializeField] private bool autoRefresh = true;
        [SerializeField, Min(0.1f)] private float refreshIntervalSeconds = 0.3f;

        [Header("Summary")]
        [SerializeField] private TMP_Text visibleAgentCountTmpText;
        [SerializeField] private TMP_Text allAgentCountTmpText;
        [SerializeField] private string visibleAgentCountFormat = "Visible: {0}";
        [SerializeField] private string allAgentCountFormat = "All: {0}";

        [Header("Zone Population List")]
        [SerializeField] private RectTransform zoneListRoot;
        [SerializeField] private GameObject zoneItemPrefab;
        [SerializeField] private string zoneItemFormat = "{0} | total={1} | final_exit={2}";

        [Header("Crowd Type Population List")]
        [SerializeField] private RectTransform crowdTypeListRoot;
        [SerializeField] private GameObject crowdTypeItemPrefab;
        [SerializeField] private string crowdTypeItemFormat = "{0} / {1}: {2}";

        [Header("Total Crowd Type Population List")]
        [SerializeField] private RectTransform totalCrowdTypeListRoot;
        [SerializeField] private GameObject totalCrowdTypeItemPrefab;
        [SerializeField] private string totalCrowdTypeItemFormat = "{0} | total={1} | visible={2}";

        [Header("Experiment Control UI")]
        [SerializeField] private GameObject completeExperimentGameObject;
        [SerializeField] private GameObject exitGameObject;

        private readonly List<ItemView> zoneItemViews = new List<ItemView>(32);
        private readonly List<ItemView> crowdTypeItemViews = new List<ItemView>(64);
        private readonly List<ItemView> totalCrowdTypeItemViews = new List<ItemView>(32);
        private float nextRefreshAtTime;

        private HeatmapCompareOverlayRenderer heatmapOverlayRenderer;
        private bool wasExperimentRunning;
        private string _activeCrowdTypeFilter;

        private void Awake()
        {
            TryResolveExperimentManager();
            ShowCompleteExperimentUI(false);
        }

        private void OnEnable()
        {
            if (!refreshOnEnable)
            {
                return;
            }

            RefreshNow();
        }

        private void Update()
        {
            DetectExperimentRunningStateChange();

            if (!autoRefresh || Time.unscaledTime < nextRefreshAtTime)
            {
                return;
            }

            RefreshNow();
        }

        private void DetectExperimentRunningStateChange()
        {
            if (!TryResolveExperimentManager()) return;

            var isRunning = !string.IsNullOrWhiteSpace(experimentManager.RunningExperimentId);
            if (isRunning && !wasExperimentRunning)
            {
                // 实验刚开始运行，显示完成实验按钮
                ShowCompleteExperimentUI(true);

                // 启动轨迹上报
                var trajectoryReporter = FindObjectOfType<TrajectoryBatchReporter>();
                if (trajectoryReporter != null)
                    trajectoryReporter.StartReporting();

                // 初始化人群多选列表：只激活第一个 crowdType
                InitializeCrowdTypeSelection();
            }
            else if (!isRunning && wasExperimentRunning)
            {
                // 实验刚结束，显示退出按钮
                ShowCompleteExperimentUI(false);
            }

            wasExperimentRunning = isRunning;
        }

        private void InitializeCrowdTypeSelection()
        {
            if (!TryResolveExperimentManager() || !TryResolveHeatmapOverlayRenderer())
                return;

            var templates = experimentManager.GetRuntimeCrowdTypeTemplatesSnapshot();
            if (templates == null || templates.Length <= 0)
                return;

            // 只保留第一个 crowdType，反选其余
            var firstTypeId = templates[0].type_id;
            if (!string.IsNullOrWhiteSpace(firstTypeId))
            {
                heatmapOverlayRenderer.SetCrowdTypes(new[] { firstTypeId.Trim() });
            }
        }

        [ContextMenu("Refresh Runtime Population UI")]
        public void RefreshNow()
        {
            nextRefreshAtTime = Time.unscaledTime + Mathf.Max(0.1f, refreshIntervalSeconds);

            if (!TryResolveExperimentManager())
            {
                ApplySummary(0, 0);
                ApplyZoneList(Array.Empty<ZonePopulationSnapshotItem>());
                ApplyCrowdTypeList(Array.Empty<ZonePopulationSnapshotItem>());
                ApplyTotalCrowdTypeList(Array.Empty<TotalCrowdTypePopulationSnapshotItem>());
                return;
            }

            DecisionRuntimeUiPopulationSnapshot snapshot;
            if (!experimentManager.TryGetDecisionRuntimeUiPopulationSnapshot(out snapshot)
                || snapshot == null)
            {
                ApplySummary(0, 0);
                ApplyZoneList(Array.Empty<ZonePopulationSnapshotItem>());
                ApplyCrowdTypeList(Array.Empty<ZonePopulationSnapshotItem>());
                ApplyTotalCrowdTypeList(Array.Empty<TotalCrowdTypePopulationSnapshotItem>());
                return;
            }

            var zones = snapshot.zone_populations ?? Array.Empty<ZonePopulationSnapshotItem>();
            var allAgentCount = 0;
            for (var i = 0; i < zones.Length; i++)
            {
                var zone = zones[i];
                if (zone == null)
                {
                    continue;
                }

                allAgentCount += Math.Max(0, zone.total_count);
            }

            ApplySummary(Math.Max(0, snapshot.visible_agent_count), Math.Max(0, allAgentCount));
            ApplyZoneList(zones);
            ApplyCrowdTypeList(zones);
            ApplyTotalCrowdTypeList(snapshot.total_crowd_type_populations ?? Array.Empty<TotalCrowdTypePopulationSnapshotItem>());
        }

        public void ToggleHeatmapOverlay(bool value)
        {
            if (!TryResolveHeatmapOverlayRenderer())
            {
                Debug.LogWarning("HeatmapCompareOverlayRenderer not found in the scene. Cannot toggle heatmap overlay.");
                return;
            }

            heatmapOverlayRenderer.SetOverlayActive(value);
        }

        public void SetHeatmapRenderMode(int mode)
        {
            if (!TryResolveHeatmapOverlayRenderer()) return;
            heatmapOverlayRenderer.SetRenderMode(mode);
        }

        public void SetHeatmapRenderModePerCrowdType()
        {
            if (!TryResolveHeatmapOverlayRenderer()) return;
            heatmapOverlayRenderer.SetRenderModePerCrowdType();
        }

        public void SetHeatmapRenderModeAggregatedRedGreen()
        {
            if (!TryResolveHeatmapOverlayRenderer()) return;
            heatmapOverlayRenderer.SetRenderModeAggregatedRedGreen();
        }

        public void SetHeatmapRenderModePathTraces()
        {
            if (!TryResolveHeatmapOverlayRenderer()) return;
            heatmapOverlayRenderer.SetRenderModePathTraces();
        }

        // public void SetHeatmapPathTracesCrowdType(string crowdType)
        // {
        //     if (!TryResolveHeatmapOverlayRenderer()) return;
        //     heatmapOverlayRenderer.SetPathTracesCrowdType(crowdType);
        // }

        public void FillMap(bool filled)
        {
            if (!TryResolveHeatmapOverlayRenderer()) return;
            heatmapOverlayRenderer.SetMapFilled(filled);
        }

        public void SetHeatmapCrowdTypeActive(string crowdType, bool active)
        {
            if (!TryResolveHeatmapOverlayRenderer()) return;
            heatmapOverlayRenderer.SetCrowdTypeActive(crowdType, active);
        }

        public void SetActiveCrowdTypeFilter(string crowdType)
        {
            _activeCrowdTypeFilter = crowdType;
        }

        public void ToggleFilteredCrowdType(bool active)
        {
            if (!TryResolveHeatmapOverlayRenderer()) return;
            if (string.IsNullOrWhiteSpace(_activeCrowdTypeFilter)) return;
            heatmapOverlayRenderer.SetCrowdTypeActive(_activeCrowdTypeFilter, active);
        }

        public void ClearHeatmapCrowdTypes()
        {
            if (!TryResolveHeatmapOverlayRenderer()) return;
            heatmapOverlayRenderer.ClearCrowdTypes();
        }

        public void CompleteExperiment()
        {
            if (!TryResolveExperimentManager()) return;

            experimentManager.ExecuteCompleteLastCreatedExperiment();
            ShowCompleteExperimentUI(false);
        }

        public void ResetAndPrepareForNewExperiment()
        {
            // 1. 停止并重置核心组件
            if (TryResolveExperimentManager())
                experimentManager.ResetRuntimeState();

            var scheduler = FindObjectOfType<DecisionBatchScheduler>();
            if (scheduler != null)
                scheduler.ResetRuntimeState();

            var trajectoryReporter = FindObjectOfType<TrajectoryBatchReporter>();
            if (trajectoryReporter != null)
                trajectoryReporter.ResetRuntimeState();

            if (TryResolveHeatmapOverlayRenderer())
                heatmapOverlayRenderer.ResetRuntimeState();

            // 2. 重置面板 UI 状态
            ResetAllUIState();

            // 3. 加载 App 场景（自动卸载地图场景）
            SceneManager.LoadScene("App");

            Debug.Log("[DecisionRuntimePopulationPanel] Reset complete, loading App scene for new experiment.", this);
        }

        private void ResetAllUIState()
        {
            // 清除所有 ItemView 缓存（旧场景的 GameObject 引用已失效）
            zoneItemViews.Clear();
            crowdTypeItemViews.Clear();
            totalCrowdTypeItemViews.Clear();

            // 重置文本
            if (visibleAgentCountTmpText != null)
                visibleAgentCountTmpText.text = string.Format(
                    CultureInfo.InvariantCulture,
                    string.IsNullOrWhiteSpace(visibleAgentCountFormat) ? "Visible: {0}" : visibleAgentCountFormat,
                    0);
            if (allAgentCountTmpText != null)
                allAgentCountTmpText.text = string.Format(
                    CultureInfo.InvariantCulture,
                    string.IsNullOrWhiteSpace(allAgentCountFormat) ? "All: {0}" : allAgentCountFormat,
                    0);

            // 重置热力图渲染器引用
            heatmapOverlayRenderer = null;

            // 重置 UI 开关状态
            ShowCompleteExperimentUI(false);
            wasExperimentRunning = false;
            nextRefreshAtTime = 0f;
        }

        private void ShowCompleteExperimentUI(bool showComplete)
        {
            if (completeExperimentGameObject != null)
                completeExperimentGameObject.SetActive(showComplete);
            if (exitGameObject != null)
                exitGameObject.SetActive(!showComplete);
        }

        private void ApplySummary(int visibleCount, int allCount)
        {
            if (visibleAgentCountTmpText != null)
            {
                visibleAgentCountTmpText.text = string.Format(
                    CultureInfo.InvariantCulture,
                    string.IsNullOrWhiteSpace(visibleAgentCountFormat) ? "Visible: {0}" : visibleAgentCountFormat,
                    visibleCount);
            }

            if (allAgentCountTmpText != null)
            {
                allAgentCountTmpText.text = string.Format(
                    CultureInfo.InvariantCulture,
                    string.IsNullOrWhiteSpace(allAgentCountFormat) ? "All: {0}" : allAgentCountFormat,
                    allCount);
            }
        }

        private void ApplyZoneList(ZonePopulationSnapshotItem[] zones)
        {
            if (zoneListRoot == null || zoneItemPrefab == null)
            {
                return;
            }

            var safeZones = zones ?? Array.Empty<ZonePopulationSnapshotItem>();
            EnsureItemViewCount(zoneItemViews, safeZones.Length, zoneListRoot, zoneItemPrefab);

            var viewCount = Math.Min(zoneItemViews.Count, safeZones.Length);
            for (var i = 0; i < viewCount; i++)
            {
                var view = zoneItemViews[i];
                if (view == null || view.Root == null)
                {
                    continue;
                }
                var zone = safeZones[i];
                var isFinalExit = zone != null && zone.is_final_exit_zone;
                if (isFinalExit)
                {
                    continue; // Skip final exit zones in the zone list as they are less important and can be numerous, which may cause performance issues and UI clutter.
                }

                var shouldShow = i < safeZones.Length;
                view.Root.SetActive(shouldShow);
                if (!shouldShow || view.Label == null)
                {
                    continue;
                }

                var zoneId = zone != null && !string.IsNullOrWhiteSpace(zone.zone_id)
                    ? zone.zone_id
                    : "(unknown_zone)";
                var total = zone != null ? Math.Max(0, zone.total_count) : 0;

                view.Label.text = string.Format(
                    CultureInfo.InvariantCulture,
                    string.IsNullOrWhiteSpace(zoneItemFormat) ? "{0} | total={1} | final_exit={2}" : zoneItemFormat,
                    zoneId,
                    total,
                    isFinalExit ? "Y" : "N");
            }

            // 隐藏多余的 item view
            for (var i = viewCount; i < zoneItemViews.Count; i++)
            {
                var view = zoneItemViews[i];
                if (view?.Root != null)
                    view.Root.SetActive(false);
            }
        }

        private void ApplyCrowdTypeList(ZonePopulationSnapshotItem[] zones)
        {
            if (crowdTypeListRoot == null || crowdTypeItemPrefab == null)
            {
                return;
            }

            var safeZones = zones ?? Array.Empty<ZonePopulationSnapshotItem>();
            var totalCrowdTypeRows = 0;
            for (var i = 0; i < safeZones.Length; i++)
            {
                var zone = safeZones[i];
                if (zone == null || zone.crowd_type_populations == null)
                {
                    continue;
                }

                totalCrowdTypeRows += zone.crowd_type_populations.Length;
            }

            EnsureItemViewCount(crowdTypeItemViews, totalCrowdTypeRows, crowdTypeListRoot, crowdTypeItemPrefab);

            var rowIndex = 0;
            for (var i = 0; i < safeZones.Length; i++)
            {
                var zone = safeZones[i];
                if (zone == null || zone.crowd_type_populations == null)
                {
                    continue;
                }

                var zoneId = !string.IsNullOrWhiteSpace(zone.zone_id) ? zone.zone_id : "(unknown_zone)";
                var crowdRows = zone.crowd_type_populations;
                for (var j = 0; j < crowdRows.Length; j++)
                {
                    if (rowIndex >= crowdTypeItemViews.Count)
                    {
                        break;
                    }

                    var row = crowdRows[j];
                    var view = crowdTypeItemViews[rowIndex];
                    rowIndex++;
                    if (view == null || view.Root == null)
                    {
                        continue;
                    }

                    view.Root.SetActive(true);
                    if (view.Label == null)
                    {
                        continue;
                    }

                    var crowdType = row != null && !string.IsNullOrWhiteSpace(row.crowd_type)
                        ? row.crowd_type
                        : "(unknown)";
                    var count = row != null ? Math.Max(0, row.count) : 0;

                    view.Label.text = string.Format(
                        CultureInfo.InvariantCulture,
                        string.IsNullOrWhiteSpace(crowdTypeItemFormat) ? "{0} / {1}: {2}" : crowdTypeItemFormat,
                        zoneId,
                        crowdType,
                        count);
                }
            }

            for (var i = rowIndex; i < crowdTypeItemViews.Count; i++)
            {
                var view = crowdTypeItemViews[i];
                if (view == null || view.Root == null)
                {
                    continue;
                }

                view.Root.SetActive(false);
            }
        }

        private void ApplyTotalCrowdTypeList(TotalCrowdTypePopulationSnapshotItem[] totalCrowdTypeRows)
        {
            if (totalCrowdTypeListRoot == null || totalCrowdTypeItemPrefab == null)
            {
                return;
            }

            var safeRows = totalCrowdTypeRows ?? Array.Empty<TotalCrowdTypePopulationSnapshotItem>();
            EnsureItemViewCount(totalCrowdTypeItemViews, safeRows.Length, totalCrowdTypeListRoot, totalCrowdTypeItemPrefab);

            for (var i = 0; i < totalCrowdTypeItemViews.Count; i++)
            {
                var view = totalCrowdTypeItemViews[i];
                if (view == null || view.Root == null)
                {
                    continue;
                }

                var shouldShow = i < safeRows.Length;
                view.Root.SetActive(shouldShow);
                if (!shouldShow || view.Label == null)
                {
                    continue;
                }

                var row = safeRows[i];
                var crowdType = row != null && !string.IsNullOrWhiteSpace(row.crowd_type)
                    ? row.crowd_type
                    : "(unknown)";
                var totalCount = row != null ? Math.Max(0, row.total_count) : 0;
                var visibleCount = row != null ? Math.Max(0, row.visible_agent_count) : 0;

                view.Label.text = string.Format(
                    CultureInfo.InvariantCulture,
                    string.IsNullOrWhiteSpace(totalCrowdTypeItemFormat)
                        ? "{0} | total={1} | visible={2}"
                        : totalCrowdTypeItemFormat,
                    crowdType,
                    totalCount,
                    visibleCount);
            }
        }

        private static void EnsureItemViewCount(
            List<ItemView> views,
            int requiredCount,
            RectTransform root,
            GameObject prefab)
        {
            requiredCount = Math.Max(0, requiredCount);
            while (views.Count < requiredCount)
            {
                var instance = Instantiate(prefab, root);
                var label = instance.GetComponent<TMP_Text>();
                if (label == null)
                {
                    label = instance.GetComponentInChildren<TMP_Text>(true);
                }

                views.Add(new ItemView
                {
                    Root = instance,
                    Label = label,
                });
            }
        }

        private bool TryResolveExperimentManager()
        {
            if (experimentManager != null)
            {
                return true;
            }

            experimentManager = ExperimentManager.ActiveInstance;
            if (experimentManager != null)
            {
                return true;
            }

            experimentManager = FindObjectOfType<ExperimentManager>();
            return experimentManager != null;
        }

        private bool TryResolveHeatmapOverlayRenderer()
        {
            if (heatmapOverlayRenderer != null)
            {
                return true;
            }

            heatmapOverlayRenderer = FindObjectOfType<HeatmapCompareOverlayRenderer>();
            return heatmapOverlayRenderer != null;
        }
    }
}
