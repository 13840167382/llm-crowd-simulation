#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using CrowdSim.Core;
using CrowdSim.Data.Debug;
using UnityEditor;
using UnityEngine;

namespace CrowdSim.EditorTools
{
    public sealed class M1FlowDebugPanelWindow : EditorWindow
    {
        private readonly Dictionary<string, bool> detailFoldouts = new Dictionary<string, bool>();

        private Vector2 scrollPosition;
        private bool autoRefresh = true;
        private bool latestRunOnly = true;
        private string selectedRunId = string.Empty;

        [MenuItem("CrowdSim/M1 Flow Debug Panel")]
        public static void OpenWindow()
        {
            var window = GetWindow<M1FlowDebugPanelWindow>();
            window.titleContent = new GUIContent("M1 Debug Panel");
            window.minSize = new Vector2(680f, 420f);
            window.Show();
        }

        private void OnEnable()
        {
            M1FlowDebugTraceStore.Changed += HandleTraceChanged;
            EditorApplication.update += HandleEditorUpdate;
        }

        private void OnDisable()
        {
            M1FlowDebugTraceStore.Changed -= HandleTraceChanged;
            EditorApplication.update -= HandleEditorUpdate;
        }

        private void HandleTraceChanged()
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
            DrawToolbar();

            var runs = M1FlowDebugTraceStore.GetRunsSnapshot();
            runs.Sort((left, right) => right.StartedAtUnixMs.CompareTo(left.StartedAtUnixMs));

            DrawRunSelector(runs);
            DrawRunSummary(runs);
            DrawStepList(runs);
        }

        private void DrawToolbar()
        {
            EditorGUILayout.BeginHorizontal(EditorStyles.toolbar);
            autoRefresh = GUILayout.Toggle(autoRefresh, "Auto Refresh", EditorStyles.toolbarButton, GUILayout.Width(96f));
            latestRunOnly = GUILayout.Toggle(latestRunOnly, "Latest Run", EditorStyles.toolbarButton, GUILayout.Width(88f));

            GUILayout.FlexibleSpace();

            using (new EditorGUI.DisabledScope(!Application.isPlaying))
            {
                if (GUILayout.Button("Run M1 Flow", EditorStyles.toolbarButton, GUILayout.Width(92f)))
                {
                    var app = FindObjectOfType<CrowdSim.App>();
                    if (app != null)
                    {
                        app.RunM1MinimalFlow();
                    }
                    else
                    {
                        ShowNotification(new GUIContent("No App component found in active scene."));
                    }
                }
            }

            if (GUILayout.Button("Clear", EditorStyles.toolbarButton, GUILayout.Width(64f)))
            {
                M1FlowDebugTraceStore.Clear();
                selectedRunId = string.Empty;
                detailFoldouts.Clear();
            }

            EditorGUILayout.EndHorizontal();

            if (!Application.isPlaying)
            {
                EditorGUILayout.HelpBox("Enter Play Mode to receive real-time M1 debug traces.", MessageType.Info);
            }
        }

        private void DrawRunSelector(List<M1FlowRunDebugInfo> runs)
        {
            if (runs.Count == 0)
            {
                EditorGUILayout.HelpBox("No M1 debug trace available yet.", MessageType.None);
                return;
            }

            if (latestRunOnly)
            {
                selectedRunId = runs[0].RunId;
            }

            if (string.IsNullOrEmpty(selectedRunId))
            {
                selectedRunId = runs[0].RunId;
            }

            var selectedIndex = FindRunIndex(runs, selectedRunId);
            if (selectedIndex < 0)
            {
                selectedIndex = 0;
                selectedRunId = runs[0].RunId;
            }

            if (!latestRunOnly)
            {
                var labels = new string[runs.Count];
                for (var i = 0; i < runs.Count; i++)
                {
                    labels[i] = BuildRunLabel(runs[i]);
                }

                selectedIndex = EditorGUILayout.Popup("Run", selectedIndex, labels);
                selectedRunId = runs[selectedIndex].RunId;
            }
        }

        private void DrawRunSummary(List<M1FlowRunDebugInfo> runs)
        {
            if (runs.Count == 0 || string.IsNullOrEmpty(selectedRunId))
            {
                return;
            }

            var run = GetRunById(runs, selectedRunId);
            if (run == null)
            {
                return;
            }

            EditorGUILayout.BeginVertical("box");
            EditorGUILayout.LabelField("Run Id", run.RunId);
            EditorGUILayout.LabelField("Status", run.Status.ToString());
            EditorGUILayout.LabelField("Summary", string.IsNullOrEmpty(run.Summary) ? "(empty)" : run.Summary);
            EditorGUILayout.LabelField("Started", FormatUnixMs(run.StartedAtUnixMs));
            EditorGUILayout.LabelField("Finished", run.FinishedAtUnixMs > 0 ? FormatUnixMs(run.FinishedAtUnixMs) : "running");
            EditorGUILayout.LabelField("Duration", run.DurationMs > 0 ? run.DurationMs + " ms" : "- ");
            EditorGUILayout.EndVertical();
        }

        private void DrawStepList(List<M1FlowRunDebugInfo> runs)
        {
            if (runs.Count == 0 || string.IsNullOrEmpty(selectedRunId))
            {
                return;
            }

            var steps = M1FlowDebugTraceStore.GetStepsForRun(selectedRunId);
            if (steps.Count == 0)
            {
                EditorGUILayout.HelpBox("No step trace for selected run.", MessageType.None);
                return;
            }

            scrollPosition = EditorGUILayout.BeginScrollView(scrollPosition);
            for (var i = 0; i < steps.Count; i++)
            {
                DrawSingleStep(steps[i]);
            }

            EditorGUILayout.EndScrollView();
        }

        private void DrawSingleStep(M1FlowStepDebugInfo step)
        {
            var previousColor = GUI.color;
            GUI.color = StatusColor(step.Status);

            EditorGUILayout.BeginVertical("box");
            GUI.color = previousColor;

            EditorGUILayout.LabelField("Step " + step.StepOrder + " - " + step.StepName, EditorStyles.boldLabel);
            EditorGUILayout.LabelField("Status", step.Status.ToString());
            EditorGUILayout.LabelField("Request", step.Method + " " + step.Path);
            EditorGUILayout.LabelField("HTTP", step.HttpStatusCode.ToString());
            EditorGUILayout.LabelField("API", step.ApiCode != int.MinValue ? step.ApiCode + " / " + step.ApiMessage : "(n/a)");
            EditorGUILayout.LabelField("Request Id", string.IsNullOrEmpty(step.RequestId) ? "(n/a)" : step.RequestId);
            EditorGUILayout.LabelField("Experiment Id", string.IsNullOrEmpty(step.ExperimentId) ? "(n/a)" : step.ExperimentId);
            EditorGUILayout.LabelField("Attempt", step.Attempt > 0 ? step.Attempt.ToString() : "- ");
            EditorGUILayout.LabelField("Duration", step.DurationMs > 0 ? step.DurationMs + " ms" : "- ");
            EditorGUILayout.LabelField("Note", string.IsNullOrEmpty(step.Note) ? "(none)" : step.Note);

            var key = step.RunId + "#" + step.StepOrder;
            var isFoldoutOpen = detailFoldouts.ContainsKey(key) && detailFoldouts[key];
            isFoldoutOpen = EditorGUILayout.Foldout(isFoldoutOpen, "Payload & Response", true);
            detailFoldouts[key] = isFoldoutOpen;

            if (isFoldoutOpen)
            {
                DrawMultiline("URL", step.Url);
                DrawMultiline("Request Body", step.RequestBody);
                DrawMultiline("Transport Error", step.TransportError);
                DrawMultiline("Response Body", step.ResponseBody);
            }

            EditorGUILayout.EndVertical();
        }

        private static void DrawMultiline(string label, string value)
        {
            EditorGUILayout.LabelField(label);
            EditorGUILayout.SelectableLabel(
                string.IsNullOrEmpty(value) ? "(empty)" : value,
                EditorStyles.textArea,
                GUILayout.MinHeight(42f));
        }

        private static string BuildRunLabel(M1FlowRunDebugInfo run)
        {
            return run.Status + " | " + FormatUnixMs(run.StartedAtUnixMs) + " | " + run.RunId;
        }

        private static M1FlowRunDebugInfo GetRunById(List<M1FlowRunDebugInfo> runs, string runId)
        {
            for (var i = 0; i < runs.Count; i++)
            {
                if (string.Equals(runs[i].RunId, runId, StringComparison.Ordinal))
                {
                    return runs[i];
                }
            }

            return null;
        }

        private static int FindRunIndex(List<M1FlowRunDebugInfo> runs, string runId)
        {
            for (var i = 0; i < runs.Count; i++)
            {
                if (string.Equals(runs[i].RunId, runId, StringComparison.Ordinal))
                {
                    return i;
                }
            }

            return -1;
        }

        private static string FormatUnixMs(long unixMs)
        {
            if (unixMs <= 0)
            {
                return "- ";
            }

            return DateTimeOffset.FromUnixTimeMilliseconds(unixMs).ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss.fff");
        }

        private static Color StatusColor(M1FlowStepStatus status)
        {
            switch (status)
            {
                case M1FlowStepStatus.Succeeded:
                    return new Color(0.55f, 0.95f, 0.55f, 1f);
                case M1FlowStepStatus.Failed:
                    return new Color(1f, 0.55f, 0.55f, 1f);
                default:
                    return new Color(1f, 0.95f, 0.5f, 1f);
            }
        }
    }
}
#endif