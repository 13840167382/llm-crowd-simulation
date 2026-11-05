using System;
using System.Collections.Generic;
using CrowdSim.Data.CrowdTypes;
using CrowdSim.Data.Decisions;
using CrowdSim.Data.Topology;
using UnityEngine;
using UnityEngine.AI;

namespace CrowdSim.Simulation.Decision
{
    public static class LocalCrowdDecisionEngine
    {
        /// <summary>
        /// 基于加权评分模型为每个 agent 的 available_exits 选出算法推荐出口。
        /// 用于在 LLM 请求中填充 algorithm_recommendation 字段。
        /// </summary>
        public static void EnrichAlgorithmRecommendations(
            List<DecisionAgentRequestDto> requestAgents,
            Dictionary<string, int> currentZonePopulation,
            Dictionary<string, int> predictedTargetInflow,
            Dictionary<string, int> zoneCapacityById,
            Dictionary<string, string> zoneTypeById,
            Dictionary<string, TopologyExitDto> topologyExitById,
            Dictionary<string, CrowdTypeAttractionWeightsDto> crowdTypeAttractionWeightsByTypeId,
            LocalDecisionScoringConfig scoringConfig = null)
        {
            if (requestAgents == null || requestAgents.Count <= 0)
            {
                return;
            }

            if (scoringConfig == null)
            {
                scoringConfig = new LocalDecisionScoringConfig();
            }

            var algorithmWeight = Mathf.Max(0f, scoringConfig.algorithmScoreWeight);
            var crowdWeight = Mathf.Max(0f, scoringConfig.crowdPressureWeight);
            var distanceWeight = Mathf.Max(0f, scoringConfig.distanceWeight);
            var exitCapacityWeight = Mathf.Max(0f, scoringConfig.exitCapacityWeight);
            var attractionWeight = Mathf.Max(0f, scoringConfig.crowdTypeAttractionWeight);
            var attractionContrast = Mathf.Max(1f, scoringConfig.crowdTypeAttractionContrast);
            var weightSum = algorithmWeight + crowdWeight + distanceWeight + exitCapacityWeight + attractionWeight;
            if (weightSum <= 0.0001f)
            {
                algorithmWeight = 1f;
                crowdWeight = 0f;
                distanceWeight = 0f;
                exitCapacityWeight = 0f;
                attractionWeight = 0f;
                weightSum = 1f;
            }

            for (var a = 0; a < requestAgents.Count; a++)
            {
                var agent = requestAgents[a];
                if (agent?.available_exits == null || agent.available_exits.Length <= 0)
                {
                    continue;
                }

                var normalizedCurrentZoneId = Normalize(agent.current_zone_id);
                var normalizedCrowdType = Normalize(agent.crowd_type);
                var bestScore = float.NegativeInfinity;
                var bestExitId = string.Empty;

                for (var e = 0; e < agent.available_exits.Length; e++)
                {
                    var option = agent.available_exits[e];
                    if (option == null)
                    {
                        continue;
                    }

                    var candidateExitId = Normalize(option.exit_id);
                    var candidateTargetZoneId = Normalize(option.target_zone_id);
                    if (string.IsNullOrWhiteSpace(candidateExitId) || string.IsNullOrWhiteSpace(candidateTargetZoneId))
                    {
                        continue;
                    }

                    if (!string.IsNullOrWhiteSpace(normalizedCurrentZoneId)
                        && string.Equals(candidateTargetZoneId, normalizedCurrentZoneId, StringComparison.OrdinalIgnoreCase))
                    {
                        continue;
                    }

                    var algorithmScore = Mathf.Clamp01(option.algorithm_score);
                    var targetLoad = ResolveCount(currentZonePopulation, candidateTargetZoneId);
                    var predictedInflow = ResolveCount(predictedTargetInflow, candidateTargetZoneId);
                    var targetCapacity = ResolveZoneCapacity(zoneCapacityById, candidateTargetZoneId, scoringConfig.defaultZoneCapacity);
                    var crowdScore = CalculateCrowdScore(targetLoad, predictedInflow, targetCapacity);
                    var targetZoneType = ResolveZoneType(zoneTypeById, candidateTargetZoneId);
                    var attractionScore = CalculateAttractionScore(normalizedCrowdType, targetZoneType, crowdTypeAttractionWeightsByTypeId, attractionContrast);

                    var weightedScore = algorithmWeight * algorithmScore
                        + crowdWeight * crowdScore
                        + distanceWeight * 0.5f  // 无 world position 时用中性距离分
                        + exitCapacityWeight * 0.5f
                        + attractionWeight * attractionScore;

                    if (weightedScore > bestScore)
                    {
                        bestScore = weightedScore;
                        bestExitId = candidateExitId;
                    }
                }

                if (!string.IsNullOrWhiteSpace(bestExitId))
                {
                    agent.algorithm_recommendation = bestExitId;
                }
            }
        }

        public static bool TrySelectBestExit(
            DecisionAgentRequestDto request,
            Vector3? agentWorldPosition,
            Dictionary<string, int> currentZonePopulation,
            Dictionary<string, int> predictedTargetInflow,
            Dictionary<string, int> zoneCapacityById,
            Dictionary<string, string> zoneTypeById,
            Dictionary<string, TopologyExitDto> topologyExitById,
            Dictionary<string, CrowdTypeAttractionWeightsDto> crowdTypeAttractionWeightsByTypeId,
            LocalDecisionScoringConfig scoringConfig,
            out string chosenExitId,
            out string chosenTargetZoneId,
            out float confidence,
            out string reasoning)
        {
            chosenExitId = string.Empty;
            chosenTargetZoneId = string.Empty;
            confidence = 0f;
            reasoning = "client_simulation: no valid exit";

            if (request == null || request.available_exits == null || request.available_exits.Length <= 0)
            {
                return false;
            }

            var normalizedCurrentZoneId = Normalize(request.current_zone_id);

            if (scoringConfig == null)
            {
                scoringConfig = new LocalDecisionScoringConfig();
            }

            var algorithmWeight = Mathf.Max(0f, scoringConfig.algorithmScoreWeight);
            var crowdWeight = Mathf.Max(0f, scoringConfig.crowdPressureWeight);
            var distanceWeight = Mathf.Max(0f, scoringConfig.distanceWeight);
            var exitCapacityWeight = Mathf.Max(0f, scoringConfig.exitCapacityWeight);
            var attractionWeight = Mathf.Max(0f, scoringConfig.crowdTypeAttractionWeight);
            var attractionContrast = Mathf.Max(1f, scoringConfig.crowdTypeAttractionContrast);
            var weightSum = algorithmWeight + crowdWeight + distanceWeight + exitCapacityWeight + attractionWeight;
            if (weightSum <= 0.0001f)
            {
                algorithmWeight = 1f;
                crowdWeight = 0f;
                distanceWeight = 0f;
                exitCapacityWeight = 0f;
                attractionWeight = 0f;
                weightSum = 1f;
            }

            var bestScore = float.NegativeInfinity;
            var bestAlgorithmScore = float.NegativeInfinity;
            var bestCrowdScore = 0f;
            var bestDistanceScore = 0f;
            var bestExitCapacityScore = 0f;
            var bestAttractionScore = 0.5f;
            var bestTargetZoneType = "unknown";
            var bestTargetLoad = 0;
            var bestTargetCapacity = Math.Max(1, scoringConfig.defaultZoneCapacity);
            var bestPredictedInflow = 0;
            var normalizedCrowdType = Normalize(request.crowd_type);

            for (var i = 0; i < request.available_exits.Length; i++)
            {
                var option = request.available_exits[i];
                if (option == null)
                {
                    continue;
                }

                var candidateExitId = Normalize(option.exit_id);
                var candidateTargetZoneId = Normalize(option.target_zone_id);
                if (string.IsNullOrWhiteSpace(candidateExitId) || string.IsNullOrWhiteSpace(candidateTargetZoneId))
                {
                    continue;
                }

                if (!string.IsNullOrWhiteSpace(normalizedCurrentZoneId)
                    && string.Equals(candidateTargetZoneId, normalizedCurrentZoneId, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                var algorithmScore = Mathf.Clamp01(option.algorithm_score);

                var targetLoad = ResolveCount(currentZonePopulation, candidateTargetZoneId);
                var predictedInflow = ResolveCount(predictedTargetInflow, candidateTargetZoneId);
                var targetCapacity = ResolveZoneCapacity(zoneCapacityById, candidateTargetZoneId, scoringConfig.defaultZoneCapacity);
                var crowdScore = CalculateCrowdScore(targetLoad, predictedInflow, targetCapacity);
                var distanceScore = CalculateDistanceScore(
                    agentWorldPosition,
                    topologyExitById,
                    candidateExitId,
                    scoringConfig.distanceNormalizationMeters);
                var exitCapacityScore = CalculateExitCapacityScore(
                    topologyExitById,
                    candidateExitId,
                    scoringConfig.exitCapacityNormalization);
                var targetZoneType = ResolveZoneType(zoneTypeById, candidateTargetZoneId);
                var attractionScore = CalculateAttractionScore(
                    normalizedCrowdType,
                    targetZoneType,
                    crowdTypeAttractionWeightsByTypeId,
                    attractionContrast);

                var weightedScore = algorithmWeight * algorithmScore
                    + crowdWeight * crowdScore
                    + distanceWeight * distanceScore
                    + exitCapacityWeight * exitCapacityScore
                    + attractionWeight * attractionScore;

                if (weightedScore > bestScore
                    || (Mathf.Abs(weightedScore - bestScore) <= 0.0001f
                        && (attractionScore > bestAttractionScore
                            || (Mathf.Abs(attractionScore - bestAttractionScore) <= 0.0001f
                                && algorithmScore > bestAlgorithmScore)
                            || (Mathf.Abs(algorithmScore - bestAlgorithmScore) <= 0.0001f
                                && string.Compare(candidateExitId, chosenExitId, StringComparison.OrdinalIgnoreCase) < 0))))
                {
                    bestScore = weightedScore;
                    bestAlgorithmScore = algorithmScore;
                    bestCrowdScore = crowdScore;
                    bestDistanceScore = distanceScore;
                    bestExitCapacityScore = exitCapacityScore;
                    bestAttractionScore = attractionScore;
                    bestTargetZoneType = targetZoneType;
                    bestTargetLoad = targetLoad;
                    bestTargetCapacity = targetCapacity;
                    bestPredictedInflow = predictedInflow;
                    chosenExitId = candidateExitId;
                    chosenTargetZoneId = candidateTargetZoneId;
                }
            }

            if (string.IsNullOrWhiteSpace(chosenExitId))
            {
                return false;
            }

            confidence = Mathf.Clamp01(bestScore / weightSum);
            reasoning = "client_simulation"
                + " score=" + bestScore.ToString("F3")
                + " alg=" + bestAlgorithmScore.ToString("F2")
                + " crowd=" + bestCrowdScore.ToString("F2")
                + " dist=" + bestDistanceScore.ToString("F2")
                + " cap=" + bestExitCapacityScore.ToString("F2")
                + " attr=" + bestAttractionScore.ToString("F2")
                + " crowd_type=" + (string.IsNullOrWhiteSpace(normalizedCrowdType) ? "unknown" : normalizedCrowdType)
                + " zone_type=" + bestTargetZoneType
                + " target_load=" + bestTargetLoad
                + "/" + bestTargetCapacity
                + " predicted_inflow=" + bestPredictedInflow;
            return true;
        }

        private static string ResolveZoneType(Dictionary<string, string> zoneTypeById, string targetZoneId)
        {
            if (zoneTypeById == null || string.IsNullOrWhiteSpace(targetZoneId))
            {
                return "other";
            }

            if (!zoneTypeById.TryGetValue(targetZoneId, out var zoneType) || string.IsNullOrWhiteSpace(zoneType))
            {
                return "other";
            }

            return zoneType.Trim().ToLowerInvariant();
        }

        private static float CalculateAttractionScore(
            string normalizedCrowdType,
            string targetZoneType,
            Dictionary<string, CrowdTypeAttractionWeightsDto> crowdTypeAttractionWeightsByTypeId,
            float attractionContrast)
        {
            var weights = ResolveAttractionWeights(normalizedCrowdType, crowdTypeAttractionWeightsByTypeId);
            // Debug.LogFormat("Calculating attraction score for crowd_type='{0}', with weights, exhibit: {1}, commercial: {2}, rest: {3}, service: {4}, plaza: {5}, passage: {6}, exit_final: {7}",
            //     normalizedCrowdType,
            //     weights.exhibit,
            //     weights.commercial,
            //     weights.rest,
            //     weights.service,
            //     weights.plaza,
            //     weights.passage,
            //     weights.exit_final);
            var raw = ResolveAttractionWeightByZoneType(weights, targetZoneType);
            return ApplyAttractionContrast(raw, attractionContrast);
        }

        private static CrowdTypeAttractionWeightsDto ResolveAttractionWeights(
            string normalizedCrowdType,
            Dictionary<string, CrowdTypeAttractionWeightsDto> crowdTypeAttractionWeightsByTypeId)
        {
            if (crowdTypeAttractionWeightsByTypeId != null
                && !string.IsNullOrWhiteSpace(normalizedCrowdType)
                && crowdTypeAttractionWeightsByTypeId.TryGetValue(normalizedCrowdType, out var weights)
                && weights != null)
            {
                return weights;
            }

            return CrowdTypeAttractionWeightsDto.CreateDefault();
        }

        private static float ResolveAttractionWeightByZoneType(CrowdTypeAttractionWeightsDto weights, string targetZoneType)
        {
            var resolvedWeights = weights ?? CrowdTypeAttractionWeightsDto.CreateDefault();
            var normalizedZoneType = string.IsNullOrWhiteSpace(targetZoneType)
                ? "other"
                : targetZoneType.Trim().ToLowerInvariant();
            switch (normalizedZoneType)
            {
                case "exhibit":
                    return Mathf.Clamp01(resolvedWeights.exhibit);
                case "commercial":
                    return Mathf.Clamp01(resolvedWeights.commercial);
                case "rest":
                    return Mathf.Clamp01(resolvedWeights.rest);
                case "service":
                    return Mathf.Clamp01(resolvedWeights.service);
                case "plaza":
                    return Mathf.Clamp01(resolvedWeights.plaza);
                case "passage":
                    return Mathf.Clamp01(resolvedWeights.passage);
                case "exit_final":
                    return Mathf.Clamp01(resolvedWeights.exit_final);
                default:
                    return 0.5f;
            }
        }

        private static float ApplyAttractionContrast(float rawScore, float contrast)
        {
            var clamped = Mathf.Clamp01(rawScore);
            var normalizedContrast = Mathf.Max(1f, contrast);
            var centered = (clamped - 0.5f) * normalizedContrast;
            return Mathf.Clamp01(0.5f + centered);
        }

        private static int ResolveZoneCapacity(
            Dictionary<string, int> zoneCapacityById,
            string targetZoneId,
            int defaultCapacity)
        {
            var fallback = Math.Max(1, defaultCapacity);
            if (zoneCapacityById == null || string.IsNullOrWhiteSpace(targetZoneId))
            {
                return fallback;
            }

            int capacity;
            if (!zoneCapacityById.TryGetValue(targetZoneId, out capacity))
            {
                return fallback;
            }

            return Math.Max(1, capacity);
        }

        private static int ResolveCount(Dictionary<string, int> counts, string key)
        {
            if (counts == null || string.IsNullOrWhiteSpace(key))
            {
                return 0;
            }

            int value;
            return counts.TryGetValue(key, out value) ? Math.Max(0, value) : 0;
        }

        private static float CalculateCrowdScore(int targetLoad, int predictedInflow, int capacity)
        {
            var normalizedCapacity = Math.Max(1, capacity);
            var projectedLoad = Mathf.Max(0f, targetLoad + predictedInflow + 1f);
            var ratio = projectedLoad / normalizedCapacity;
            return Mathf.Clamp01(1f - ratio);
        }

        private static float CalculateDistanceScore(
            Vector3? agentWorldPosition,
            Dictionary<string, TopologyExitDto> topologyExitById,
            string exitId,
            float distanceNormalizationMeters)
        {
            if (!agentWorldPosition.HasValue
                || topologyExitById == null
                || string.IsNullOrWhiteSpace(exitId)
                || !topologyExitById.TryGetValue(exitId, out var exit)
                || exit == null
                || exit.position == null)
            {
                return 0.5f;
            }

            var normalization = Mathf.Max(0.1f, distanceNormalizationMeters);
            var agentPos = agentWorldPosition.Value;
            var exitPos = new Vector3(exit.position.x, agentPos.y, exit.position.z);

            var distance = CalculateNavMeshPathDistance(agentPos, exitPos, normalization * 4f);

            return 1f / (1f + distance / normalization);
        }

        private static float CalculateNavMeshPathDistance(Vector3 from, Vector3 to, float fallbackDistance)
        {
            var path = new NavMeshPath();
            if (NavMesh.CalculatePath(from, to, NavMesh.AllAreas, path)
                && path.status == NavMeshPathStatus.PathComplete
                && path.corners != null
                && path.corners.Length >= 2)
            {
                var totalDistance = 0f;
                for (var i = 1; i < path.corners.Length; i++)
                {
                    totalDistance += Vector3.Distance(path.corners[i - 1], path.corners[i]);
                }

                return Mathf.Max(0.01f, totalDistance);
            }

            // NavMesh 不可达时回退到直线距离
            return Mathf.Max(0.01f, Vector3.Distance(from, to));
        }

        private static float CalculateExitCapacityScore(
            Dictionary<string, TopologyExitDto> topologyExitById,
            string exitId,
            int normalization)
        {
            if (topologyExitById == null
                || string.IsNullOrWhiteSpace(exitId)
                || !topologyExitById.TryGetValue(exitId, out var exit)
                || exit == null)
            {
                return 0.5f;
            }

            var exitCapacity = Mathf.Max(1f, exit.pass_capacity_per_sec);
            var norm = Mathf.Max(1f, normalization);
            return exitCapacity / (exitCapacity + norm);
        }

        private static string Normalize(string value)
        {
            return string.IsNullOrWhiteSpace(value) ? string.Empty : value.Trim();
        }

        /// <summary>
        /// 根据地图配置（拓扑数据 + 人群类型吸引力权重）动态计算每个出口的 algorithm_score 和 nl_description。
        /// </summary>
        public static void EnrichExitOptionsWithMapScores(
            List<DecisionAvailableExitOptionDto> exits,
            Vector3 agentWorldPosition,
            string crowdType,
            Dictionary<string, int> zoneCapacityById,
            Dictionary<string, string> zoneTypeById,
            Dictionary<string, TopologyExitDto> topologyExitById,
            Dictionary<string, CrowdTypeAttractionWeightsDto> crowdTypeAttractionWeightsByTypeId = null,
            LocalDecisionScoringConfig scoringConfig = null,
            Dictionary<string, int> zoneArrivedCount = null)
        {
            if (exits == null || exits.Count <= 0)
            {
                return;
            }

            if (scoringConfig == null)
            {
                scoringConfig = new LocalDecisionScoringConfig();
            }

            var normalizedCrowdType = Normalize(crowdType);
            var weights = ResolveAttractionWeights(normalizedCrowdType, crowdTypeAttractionWeightsByTypeId);
            var defaultCapacity = Math.Max(1, scoringConfig.defaultZoneCapacity);

            for (var i = 0; i < exits.Count; i++)
            {
                var exit = exits[i];
                if (exit == null)
                {
                    continue;
                }

                var normalizedTargetZoneId = Normalize(exit.target_zone_id);
                var normalizedExitId = Normalize(exit.exit_id);

                // 计算目标区域类型吸引力
                var targetZoneType = ResolveZoneType(zoneTypeById, normalizedTargetZoneId);
                var rawAttraction = ResolveAttractionWeightByZoneType(weights, targetZoneType);
                var attractionScore = ApplyAttractionContrast(rawAttraction, scoringConfig.crowdTypeAttractionContrast);

                // 计算目标区域容量评分
                var targetCapacity = ResolveZoneCapacity(zoneCapacityById, normalizedTargetZoneId, defaultCapacity);
                var capacityScore = targetCapacity / (float)(targetCapacity + defaultCapacity);

                // 复合 algorithm_score：吸引力权重 0.7 + 容量 0.3
                exit.algorithm_score = Mathf.Clamp01(attractionScore * 0.7f + capacityScore * 0.3f);

                // 运行时数据
                var targetZonePop = ResolveZoneArrivedCount(zoneArrivedCount, normalizedTargetZoneId);
                var exitPassCapacity = ResolveExitPassCapacity(topologyExitById, normalizedExitId);

                // 动态构建 nl_description
                exit.nl_description = BuildExitNaturalLanguageDescription(
                    normalizedExitId,
                    targetZoneType,
                    rawAttraction,
                    targetZonePop,
                    targetCapacity,
                    exitPassCapacity);
            }
        }

        private static int ResolveZoneArrivedCount(Dictionary<string, int> zoneArrivedCount, string zoneId)
        {
            if (zoneArrivedCount == null || string.IsNullOrWhiteSpace(zoneId))
            {
                return 0;
            }

            int count;
            return zoneArrivedCount.TryGetValue(zoneId, out count) ? Math.Max(0, count) : 0;
        }

        private static float ResolveExitPassCapacity(Dictionary<string, TopologyExitDto> topologyExitById, string exitId)
        {
            if (topologyExitById == null || string.IsNullOrWhiteSpace(exitId))
            {
                return 0f;
            }

            TopologyExitDto exit;
            return topologyExitById.TryGetValue(exitId, out exit) && exit != null
                ? Math.Max(0f, exit.pass_capacity_per_sec)
                : 0f;
        }

        private static string BuildExitNaturalLanguageDescription(
            string exitId,
            string targetZoneType,
            float attractionWeight,
            int targetZonePopulation,
            int targetZoneCapacity,
            float exitPassCapacityPerSec)
        {
            var zoneTypeDisplay = ResolveZoneTypeDisplayName(targetZoneType);
            var strengthLabel = ResolveAttractionStrengthLabel(attractionWeight);

            // 基础吸引力描述
            var description = $"通往{exitId}({zoneTypeDisplay}). {zoneTypeDisplay}对该类人群吸引力{strengthLabel}(权重{attractionWeight:F1}).";

            // 仅在有人口数据时附加运行时人群描述
            if (targetZonePopulation > 0)
            {
                var crowdDescription = BuildZoneCrowdDescription(targetZonePopulation, targetZoneCapacity, exitPassCapacityPerSec);
                if (!string.IsNullOrWhiteSpace(crowdDescription))
                {
                    description += " 但" + crowdDescription;
                }
            }

            return description;
        }

        private static string BuildZoneCrowdDescription(int population, int capacity, float passCapacityPerSec)
        {
            var effectiveCapacity = Math.Max(1, capacity);
            var ratio = population / (float)effectiveCapacity;

            string crowdLabel;
            string waitInfo;
            if (population <= 0 || ratio <= 0.1f)
            {
                crowdLabel = "当前几乎无人排队，可即时通过";
                waitInfo = "内部空旷安静";
            }
            else if (ratio <= 0.35f)
            {
                crowdLabel = $"有少量排队约{population}人";
                var waitSeconds = passCapacityPerSec > 0f ? population / passCapacityPerSec : 3f;
                waitInfo = $"预计等待{waitSeconds:F0}秒, 区域内人流适中";
            }
            else if (ratio <= 0.7f)
            {
                crowdLabel = $"当前区域排队约{population}人";
                var waitSeconds = passCapacityPerSec > 0f ? population / passCapacityPerSec : 10f;
                waitInfo = $"预计等待{waitSeconds:F0}秒, 通过后达到的区域内部人也较多";
            }
            else
            {
                crowdLabel = $"当前区域拥挤约{population}人(容量{effectiveCapacity})";
                var waitSeconds = passCapacityPerSec > 0f ? population / passCapacityPerSec : 30f;
                waitInfo = $"预计等待{waitSeconds:F0}秒, 区域内非常拥挤";
            }

            return $"但{crowdLabel}, {waitInfo}。";
        }

        private static string ResolveZoneTypeDisplayName(string zoneTypeRaw)
        {
            if (string.IsNullOrWhiteSpace(zoneTypeRaw))
            {
                return "其他区域";
            }

            switch (zoneTypeRaw.Trim().ToLowerInvariant())
            {
                case "passage":
                    return "通道";
                case "plaza":
                    return "广场";
                case "exhibit":
                    return "展览区";
                case "rest":
                    return "休息区";
                case "commercial":
                    return "商业区";
                case "service":
                    return "服务区";
                case "exit_final":
                    return "出口";
                default:
                    return "其他区域";
            }
        }

        private static string ResolveAttractionStrengthLabel(float weight)
        {
            var clamped = Mathf.Clamp01(weight);
            if (clamped > 0.7f)
            {
                return "很强";
            }

            if (clamped > 0.4f)
            {
                return "中等";
            }

            if (clamped > 0.1f)
            {
                return "较弱";
            }

            return "极弱";
        }

        /// <summary>
        /// 用实时人群分布数据重新富化出口选项的 nl_description 和 algorithm_score。
        /// 在调度器收集完 agent 请求后、发 LLM 或本地模拟前调用。
        /// </summary>
        public static void EnrichExitOptionsWithRuntimePopulation(
            List<DecisionAgentRequestDto> requestAgents,
            Dictionary<string, int> currentZonePopulation,
            Dictionary<string, int> zoneCapacityById,
            Dictionary<string, string> zoneTypeById,
            Dictionary<string, TopologyExitDto> topologyExitById)
        {
            if (requestAgents == null || requestAgents.Count <= 0)
            {
                return;
            }

            for (var a = 0; a < requestAgents.Count; a++)
            {
                var agent = requestAgents[a];
                if (agent?.available_exits == null)
                {
                    continue;
                }

                for (var e = 0; e < agent.available_exits.Length; e++)
                {
                    var exit = agent.available_exits[e];
                    if (exit == null)
                    {
                        continue;
                    }

                    var normalizedTargetZoneId = Normalize(exit.target_zone_id);
                    var normalizedExitId = Normalize(exit.exit_id);

                    var targetCapacity = zoneCapacityById != null && zoneCapacityById.TryGetValue(normalizedTargetZoneId, out var cap)
                        ? Math.Max(1, cap)
                        : 120;

                    var targetPop = currentZonePopulation != null && currentZonePopulation.TryGetValue(normalizedTargetZoneId, out var pop)
                        ? Math.Max(0, pop)
                        : 0;

                    var exitPassCapacity = topologyExitById != null && topologyExitById.TryGetValue(normalizedExitId, out var exitDto) && exitDto != null
                        ? Math.Max(0f, exitDto.pass_capacity_per_sec)
                        : 0f;

                    var targetZoneType = zoneTypeById != null && zoneTypeById.TryGetValue(normalizedTargetZoneId, out var zt)
                        ? zt
                        : string.Empty;

                    // 更新 algorithm_score：纳入人群负载
                    var populationRatio = targetPop / (float)targetCapacity;
                    var loadPenalty = Mathf.Clamp01(1f - populationRatio);
                    var originalScore = exit.algorithm_score;
                    exit.algorithm_score = Mathf.Clamp01(originalScore * (0.4f + loadPenalty * 0.6f));

                    // 更新 nl_description：完全重建（覆盖 agent 侧的空人群描述）
                    var crowdDesc = BuildZoneCrowdDescription(targetPop, targetCapacity, exitPassCapacity);
                    var zoneTypeDisplay = ResolveZoneTypeDisplayName(targetZoneType);

                    // 从已有描述提取吸引力部分
                    var baseDesc = exit.nl_description ?? string.Empty;
                    var dotIndex = baseDesc.IndexOf('.');
                    var attractionPart = dotIndex > 0
                        ? baseDesc.Substring(0, dotIndex + 1).TrimEnd()
                        : $"通往{normalizedExitId}({zoneTypeDisplay})";

                    exit.nl_description = $"{attractionPart} 但{crowdDesc}";
                }
            }
        }
    }
}