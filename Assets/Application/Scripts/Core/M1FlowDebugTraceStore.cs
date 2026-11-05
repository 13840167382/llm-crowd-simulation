using System;
using System.Collections.Generic;
using CrowdSim.Data.Debug;
using CrowdSim.Utils;

namespace CrowdSim.Core
{
    public static class M1FlowDebugTraceStore
    {
        private static readonly List<M1FlowRunDebugInfo> RunsInternal = new List<M1FlowRunDebugInfo>();
        private static readonly List<M1FlowStepDebugInfo> StepsInternal = new List<M1FlowStepDebugInfo>();

        public static event Action Changed;

        public static void StartRun(string runId, string title)
        {
            if (string.IsNullOrWhiteSpace(runId))
            {
                return;
            }

            var now = RequestIdUtility.GetUnixTimestampMs();
            UpsertRun(new M1FlowRunDebugInfo
            {
                RunId = runId,
                Title = string.IsNullOrWhiteSpace(title) ? "M1 Flow" : title,
                Status = M1FlowRunStatus.Running,
                Summary = "Running",
                StartedAtUnixMs = now,
                FinishedAtUnixMs = 0,
                DurationMs = 0,
            });
        }

        public static void CompleteRun(string runId, M1FlowRunStatus status, string summary)
        {
            if (string.IsNullOrWhiteSpace(runId))
            {
                return;
            }

            var index = FindRunIndex(runId);
            var now = RequestIdUtility.GetUnixTimestampMs();
            if (index < 0)
            {
                UpsertRun(new M1FlowRunDebugInfo
                {
                    RunId = runId,
                    Title = "M1 Flow",
                    Status = status,
                    Summary = summary,
                    StartedAtUnixMs = now,
                    FinishedAtUnixMs = now,
                    DurationMs = 0,
                });
                return;
            }

            var run = RunsInternal[index].Clone();
            run.Status = status;
            run.Summary = summary;
            run.FinishedAtUnixMs = now;
            run.DurationMs = Math.Max(0, now - run.StartedAtUnixMs);
            RunsInternal[index] = run;
            RaiseChanged();
        }

        public static void UpsertStep(M1FlowStepDebugInfo step)
        {
            if (step == null || string.IsNullOrWhiteSpace(step.RunId))
            {
                return;
            }

            var safeStep = step.Clone();
            if (safeStep.StartedAtUnixMs <= 0)
            {
                safeStep.StartedAtUnixMs = RequestIdUtility.GetUnixTimestampMs();
            }

            var index = FindStepIndex(safeStep.RunId, safeStep.StepOrder);
            if (index < 0)
            {
                StepsInternal.Add(safeStep);
            }
            else
            {
                StepsInternal[index] = safeStep;
            }

            RaiseChanged();
        }

        public static List<M1FlowRunDebugInfo> GetRunsSnapshot()
        {
            var output = new List<M1FlowRunDebugInfo>(RunsInternal.Count);
            for (var i = 0; i < RunsInternal.Count; i++)
            {
                output.Add(RunsInternal[i].Clone());
            }

            return output;
        }

        public static List<M1FlowStepDebugInfo> GetStepsForRun(string runId)
        {
            var output = new List<M1FlowStepDebugInfo>();
            if (string.IsNullOrWhiteSpace(runId))
            {
                return output;
            }

            for (var i = 0; i < StepsInternal.Count; i++)
            {
                if (string.Equals(StepsInternal[i].RunId, runId, StringComparison.Ordinal))
                {
                    output.Add(StepsInternal[i].Clone());
                }
            }

            output.Sort((left, right) => left.StepOrder.CompareTo(right.StepOrder));
            return output;
        }

        public static void Clear()
        {
            RunsInternal.Clear();
            StepsInternal.Clear();
            RaiseChanged();
        }

        private static void UpsertRun(M1FlowRunDebugInfo run)
        {
            var safeRun = run.Clone();
            var index = FindRunIndex(safeRun.RunId);
            if (index < 0)
            {
                RunsInternal.Add(safeRun);
            }
            else
            {
                RunsInternal[index] = safeRun;
            }

            RaiseChanged();
        }

        private static int FindRunIndex(string runId)
        {
            for (var i = 0; i < RunsInternal.Count; i++)
            {
                if (string.Equals(RunsInternal[i].RunId, runId, StringComparison.Ordinal))
                {
                    return i;
                }
            }

            return -1;
        }

        private static int FindStepIndex(string runId, int stepOrder)
        {
            for (var i = 0; i < StepsInternal.Count; i++)
            {
                if (string.Equals(StepsInternal[i].RunId, runId, StringComparison.Ordinal)
                    && StepsInternal[i].StepOrder == stepOrder)
                {
                    return i;
                }
            }

            return -1;
        }

        private static void RaiseChanged()
        {
            Changed?.Invoke();
        }
    }
}