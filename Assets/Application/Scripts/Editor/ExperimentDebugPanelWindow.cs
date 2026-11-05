#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using Cysharp.Threading.Tasks;
using CrowdSim.Core;
using CrowdSim.Data.Experiments;
using UnityEditor;
using UnityEngine;

namespace CrowdSim.EditorTools
{
    public sealed class ExperimentDebugPanelWindow : EditorWindow
    {
        private readonly Dictionary<string, bool> callFoldouts = new Dictionary<string, bool>();

        private Vector2 callScrollPosition;
        private bool autoRefresh = true;
        private bool latestExperimentOnly = true;
        private string selectedExperimentId = string.Empty;

        [MenuItem("CrowdSim/Experiment Debug Panel")]
        public static void OpenWindow()
        {
            var window = GetWindow<ExperimentDebugPanelWindow>();
            window.titleContent = new GUIContent("Experiment Debug");
            window.minSize = new Vector2(760f, 460f);
            window.Show();
        }

        private void OnEnable()
        {
            ExperimentManager.CacheChanged += HandleCacheChanged;
            EditorApplication.update += HandleEditorUpdate;
        }

        private void OnDisable()
        {
            ExperimentManager.CacheChanged -= HandleCacheChanged;
            EditorApplication.update -= HandleEditorUpdate;
        }

        private void HandleCacheChanged()
        {
            Repaint();
        }

        private void HandleEditorUpdate()
        {
            if (autoRefresh && Application.isPlaying)
            {
                Repaint();
            }
        }

        private void OnGUI()
        {
            var manager = ExperimentManager.ActiveInstance;
            DrawToolbar(manager);

            List<ExperimentCacheRecord> records;
            if (!ExperimentManager.TryGetCacheSnapshot(out records) || records.Count <= 0)
            {
                DrawEmptyState(manager);
                return;
            }

            records.Sort((left, right) => right.LastUpdatedAtUnixMs.CompareTo(left.LastUpdatedAtUnixMs));

            DrawRecordSelector(records);
            DrawRecordSummary(records);
            DrawCallHistory(records);
        }

        private void DrawToolbar(ExperimentManager manager)
        {
            EditorGUILayout.BeginHorizontal(EditorStyles.toolbar);

            autoRefresh = GUILayout.Toggle(autoRefresh, "Auto Refresh", EditorStyles.toolbarButton, GUILayout.Width(96f));
            latestExperimentOnly = GUILayout.Toggle(latestExperimentOnly, "Latest", EditorStyles.toolbarButton, GUILayout.Width(64f));

            GUILayout.Space(8f);
            GUILayout.Label(manager == null ? "Manager: none" : "Manager: active", EditorStyles.miniLabel, GUILayout.Width(110f));
            GUILayout.Label(manager != null && manager.IsBusy ? "Busy" : "Idle", EditorStyles.miniLabel, GUILayout.Width(36f));

            if (manager != null)
            {
                GUILayout.Space(6f);
                GUILayout.Label(manager.IsStartupCompleted ? "Startup: ready" : "Startup: pending", EditorStyles.miniLabel, GUILayout.Width(96f));
                GUILayout.Label(manager.IsCrowdTemplateStartupCompleted ? "Templates: ready" : "Templates: pending", EditorStyles.miniLabel, GUILayout.Width(112f));

                var startupLabels = manager.GetStartupMapLabels();
                if (startupLabels.Length > 0)
                {
                    var selectedIndex = manager.GetSelectedStartupMapIndex();
                    var newIndex = EditorGUILayout.Popup(selectedIndex, startupLabels, EditorStyles.toolbarPopup, GUILayout.Width(260f));
                    if (newIndex != selectedIndex)
                    {
                        manager.SetSelectedStartupMapIndex(newIndex);
                    }
                }
            }

            GUILayout.FlexibleSpace();

            var hasSelection = !string.IsNullOrWhiteSpace(selectedExperimentId);
            var startupCompleted = manager != null && manager.IsStartupCompleted;
            using (new EditorGUI.DisabledScope(!Application.isPlaying || manager == null || manager.IsBusy))
            {
                if (GUILayout.Button("Startup", EditorStyles.toolbarButton, GUILayout.Width(68f)))
                {
                    manager.StartupSelectMapAndLoadSceneAsync().Forget();
                }

                if (GUILayout.Button("Templates", EditorStyles.toolbarButton, GUILayout.Width(74f)))
                {
                    manager.StartupSyncCrowdTemplatesAsync().Forget();
                }

                using (new EditorGUI.DisabledScope(!startupCompleted))
                {
                    if (GUILayout.Button("Create", EditorStyles.toolbarButton, GUILayout.Width(60f)))
                    {
                        manager.CreateExperimentAsync().Forget();
                    }
                }

                using (new EditorGUI.DisabledScope(!hasSelection))
                {
                    if (GUILayout.Button("Configure", EditorStyles.toolbarButton, GUILayout.Width(72f)))
                    {
                        manager.ConfigureExperimentAsync(selectedExperimentId).Forget();
                    }

                    if (GUILayout.Button("Spawn", EditorStyles.toolbarButton, GUILayout.Width(58f)))
                    {
                        manager.GenerateSpawnPositionsAsync(selectedExperimentId).Forget();
                    }

                    if (GUILayout.Button("Start", EditorStyles.toolbarButton, GUILayout.Width(52f)))
                    {
                        manager.StartExperimentAsync(selectedExperimentId).Forget();
                    }

                    if (GUILayout.Button("Complete", EditorStyles.toolbarButton, GUILayout.Width(72f)))
                    {
                        manager.CompleteExperimentAsync(selectedExperimentId).Forget();
                    }
                }
            }

            using (new EditorGUI.DisabledScope(manager == null || manager.IsBusy))
            {
                if (GUILayout.Button("Clear Cache", EditorStyles.toolbarButton, GUILayout.Width(84f)))
                {
                    manager.ClearCache();
                    selectedExperimentId = string.Empty;
                    callFoldouts.Clear();
                }
            }

            EditorGUILayout.EndHorizontal();
        }

        private void DrawEmptyState(ExperimentManager manager)
        {
            if (manager == null)
            {
                EditorGUILayout.HelpBox(
                    "No active ExperimentManager found. Add ExperimentManager to a scene object and enter Play Mode.",
                    MessageType.Info);
                return;
            }

            EditorGUILayout.HelpBox(
                "Experiment cache is empty. Run Startup to select/load a map scene, then Create/Configure/Spawn/Start/Complete.",
                MessageType.None);

            EditorGUILayout.HelpBox("Startup status: " + manager.StartupStatus, MessageType.Info);
            EditorGUILayout.HelpBox("Crowd template startup: " + manager.CrowdTemplateStartupStatus, MessageType.Info);
        }

        private void DrawRecordSelector(List<ExperimentCacheRecord> records)
        {
            if (records.Count <= 0)
            {
                return;
            }

            if (latestExperimentOnly)
            {
                selectedExperimentId = records[0].ExperimentId;
            }

            if (string.IsNullOrWhiteSpace(selectedExperimentId))
            {
                selectedExperimentId = records[0].ExperimentId;
            }

            var selectedIndex = FindRecordIndex(records, selectedExperimentId);
            if (selectedIndex < 0)
            {
                selectedIndex = 0;
                selectedExperimentId = records[0].ExperimentId;
            }

            if (latestExperimentOnly)
            {
                return;
            }

            var labels = new string[records.Count];
            for (var i = 0; i < records.Count; i++)
            {
                labels[i] = BuildRecordLabel(records[i]);
            }

            selectedIndex = EditorGUILayout.Popup("Experiment", selectedIndex, labels);
            selectedExperimentId = records[selectedIndex].ExperimentId;
        }

        private void DrawRecordSummary(List<ExperimentCacheRecord> records)
        {
            var selected = GetSelectedRecord(records, selectedExperimentId);
            if (selected == null)
            {
                return;
            }

            EditorGUILayout.BeginVertical("box");
            EditorGUILayout.LabelField("Experiment Id", selected.ExperimentId);
            EditorGUILayout.LabelField("Name", string.IsNullOrWhiteSpace(selected.ExperimentName) ? "(empty)" : selected.ExperimentName);
            EditorGUILayout.LabelField(
                "Status",
                selected.HasStatus ? selected.Status.ToString() : "(unknown)");
            EditorGUILayout.LabelField(
                "Crowd Type",
                string.IsNullOrWhiteSpace(selected.SelectedCrowdTypeId) ? "(unknown)" : selected.SelectedCrowdTypeId);
            EditorGUILayout.LabelField("Last Spawn Seed", selected.LastSpawnAtUnixMs > 0 ? selected.LastSpawnSeed.ToString() : "-");
            EditorGUILayout.LabelField("Last Spawn Total", selected.LastSpawnAtUnixMs > 0 ? selected.LastSpawnTotalCount.ToString() : "-");
            EditorGUILayout.LabelField("Last Instantiated", selected.LastSpawnAtUnixMs > 0 ? selected.LastSpawnInstantiatedCount.ToString() : "-");
            EditorGUILayout.LabelField("Last Spawn At", FormatUnixMs(selected.LastSpawnAtUnixMs));
            EditorGUILayout.LabelField("Created", FormatUnixMs(selected.CreatedAtUnixMs));
            EditorGUILayout.LabelField("Updated", FormatUnixMs(selected.LastUpdatedAtUnixMs));
            EditorGUILayout.LabelField(
                "Last Operation",
                string.IsNullOrWhiteSpace(selected.LastOperationName) ? "(none)" : selected.LastOperationName);
            EditorGUILayout.LabelField("Last Success", selected.LastOperationSuccess ? "true" : "false");
            EditorGUILayout.LabelField("Last Message", string.IsNullOrWhiteSpace(selected.LastMessage) ? "(none)" : selected.LastMessage);
            EditorGUILayout.LabelField("Cached Calls", selected.Calls != null ? selected.Calls.Count.ToString() : "0");
            EditorGUILayout.EndVertical();
        }

        private void DrawCallHistory(List<ExperimentCacheRecord> records)
        {
            var selected = GetSelectedRecord(records, selectedExperimentId);
            if (selected == null)
            {
                return;
            }

            if (selected.Calls == null || selected.Calls.Count <= 0)
            {
                EditorGUILayout.HelpBox("No API calls recorded for this experiment yet.", MessageType.None);
                return;
            }

            callScrollPosition = EditorGUILayout.BeginScrollView(callScrollPosition);
            for (var i = selected.Calls.Count - 1; i >= 0; i--)
            {
                DrawSingleCall(selected.Calls[i]);
            }

            EditorGUILayout.EndScrollView();
        }

        private void DrawSingleCall(ExperimentApiCallCacheRecord call)
        {
            if (call == null)
            {
                return;
            }

            var previousColor = GUI.color;
            GUI.color = call.Success
                ? new Color(0.55f, 0.95f, 0.55f, 1f)
                : new Color(1f, 0.55f, 0.55f, 1f);

            EditorGUILayout.BeginVertical("box");
            GUI.color = previousColor;

            EditorGUILayout.LabelField("#" + call.Sequence + " - " + call.OperationName, EditorStyles.boldLabel);
            EditorGUILayout.LabelField("Status", call.Success ? "Succeeded" : "Failed");
            EditorGUILayout.LabelField("Request", (call.Method ?? string.Empty) + " " + (call.Path ?? string.Empty));
            EditorGUILayout.LabelField("HTTP", call.HttpStatusCode.ToString());
            EditorGUILayout.LabelField("API", call.ApiCode != int.MinValue ? call.ApiCode + " / " + call.ApiMessage : "(n/a)");
            EditorGUILayout.LabelField("Request Id", string.IsNullOrWhiteSpace(call.RequestId) ? "(n/a)" : call.RequestId);
            EditorGUILayout.LabelField("Attempt", call.Attempt > 0 ? call.Attempt.ToString() : "-");
            EditorGUILayout.LabelField("Duration", call.DurationMs > 0 ? call.DurationMs + " ms" : "-");
            EditorGUILayout.LabelField("Note", string.IsNullOrWhiteSpace(call.Note) ? "(none)" : call.Note);

            var foldoutKey = (call.ExperimentId ?? string.Empty) + "#" + call.Sequence + "#" + (call.OperationName ?? string.Empty);
            var isOpen = callFoldouts.ContainsKey(foldoutKey) && callFoldouts[foldoutKey];
            isOpen = EditorGUILayout.Foldout(isOpen, "Payload & Response", true);
            callFoldouts[foldoutKey] = isOpen;

            if (isOpen)
            {
                DrawMultiline("URL", call.Url);
                DrawMultiline("Request Body", call.RequestBody);
                DrawMultiline("Transport Error", call.TransportError);
                DrawMultiline("Response Body", call.ResponseBody);
            }

            EditorGUILayout.EndVertical();
        }

        private static void DrawMultiline(string label, string value)
        {
            EditorGUILayout.LabelField(label);
            EditorGUILayout.SelectableLabel(
                string.IsNullOrWhiteSpace(value) ? "(empty)" : value,
                EditorStyles.textArea,
                GUILayout.MinHeight(42f));
        }

        private static int FindRecordIndex(List<ExperimentCacheRecord> records, string experimentId)
        {
            for (var i = 0; i < records.Count; i++)
            {
                if (string.Equals(records[i].ExperimentId, experimentId, StringComparison.Ordinal))
                {
                    return i;
                }
            }

            return -1;
        }

        private static ExperimentCacheRecord GetSelectedRecord(List<ExperimentCacheRecord> records, string experimentId)
        {
            var index = FindRecordIndex(records, experimentId);
            if (index < 0 || index >= records.Count)
            {
                return null;
            }

            return records[index];
        }

        private static string BuildRecordLabel(ExperimentCacheRecord record)
        {
            if (record == null)
            {
                return "(invalid)";
            }

            var status = record.HasStatus ? record.Status.ToString() : "Unknown";
            return status + " | " + record.ExperimentId + " | " + FormatUnixMs(record.LastUpdatedAtUnixMs);
        }

        private static string FormatUnixMs(long unixMs)
        {
            if (unixMs <= 0)
            {
                return "-";
            }

            return DateTimeOffset.FromUnixTimeMilliseconds(unixMs).ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss.fff");
        }
    }
}
#endif
