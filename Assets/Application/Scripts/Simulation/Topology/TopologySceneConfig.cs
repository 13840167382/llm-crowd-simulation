using System;
using System.Collections.Generic;
using CrowdSim.Data.Decisions;
using CrowdSim.Data.Topology;
using UnityEngine;

namespace CrowdSim.Simulation.Topology
{
    public sealed class TopologySceneConfig : MonoBehaviour
    {
        [SerializeField] private bool autoCollectFromChildren = true;
        [SerializeField] private TopologyZonePoint[] zonePoints = Array.Empty<TopologyZonePoint>();
        [SerializeField] private TopologyExitPoint[] exitPoints = Array.Empty<TopologyExitPoint>();
        [SerializeField] private string preferredSpawnZoneId = string.Empty;
        [SerializeField] private ZoneRuntimeArrivalRecord[] zoneRuntimeArrivals = Array.Empty<ZoneRuntimeArrivalRecord>();
        [SerializeField] private FixedPathConfigSO[] fixedPathConfigs = Array.Empty<FixedPathConfigSO>();
        [SerializeField] private bool enableFixedPathConfigs = true;

        private bool topologyPointsCacheDirty = true;
        private TopologyZonePoint[] cachedResolvedZonePoints = Array.Empty<TopologyZonePoint>();
        private TopologyExitPoint[] cachedResolvedExitPoints = Array.Empty<TopologyExitPoint>();
        private bool topologySnapshotCacheDirty = true;
        private TopologyUpsertRequestDto cachedTopologyRequest;
        private string cachedTopologyError = string.Empty;
        private bool cachedTopologyValid;
        private int topologyVersion = 1;

        private readonly Dictionary<string, int> zoneArrivalCountMap =
            new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);

        public int TopologyVersion
        {
            get { return topologyVersion; }
        }

        public FixedPathConfigSO[] FixedPathConfigs
        {
            get
            {
                if (!enableFixedPathConfigs)
                {
                    return Array.Empty<FixedPathConfigSO>();
                }

                return fixedPathConfigs ?? Array.Empty<FixedPathConfigSO>();
            }
        }

        private void OnEnable()
        {
            MarkTopologyDirty();
        }

#if UNITY_EDITOR
        private void OnValidate()
        {
            MarkTopologyDirty();
        }
#endif

        public bool TryBuildTopologyRequest(out TopologyUpsertRequestDto request, out string error)
        {
            if (!topologySnapshotCacheDirty)
            {
                request = cachedTopologyRequest;
                error = cachedTopologyError;
                return cachedTopologyValid;
            }

            var zonesSource = ResolveZonePoints();
            if (zonesSource.Length <= 0)
            {
                request = null;
                error = "TopologySceneConfig has no zone points.";
                CacheTopologySnapshot(request, error, false);
                return false;
            }

            var zones = new List<TopologyZoneDto>(zonesSource.Length);
            var zoneIdSet = new HashSet<string>(StringComparer.Ordinal);
            for (var i = 0; i < zonesSource.Length; i++)
            {
                var point = zonesSource[i];
                if (point == null)
                {
                    continue;
                }

                TopologyZoneDto zone;
                if (!point.TryBuildDto(out zone) || zone == null)
                {
                    continue;
                }

                if (!zoneIdSet.Add(zone.zone_id))
                {
                    request = null;
                    error = "TopologySceneConfig has duplicated zone_id: " + zone.zone_id;
                    CacheTopologySnapshot(request, error, false);
                    return false;
                }

                zones.Add(zone);
            }

            if (zones.Count <= 0)
            {
                request = null;
                error = "TopologySceneConfig has no valid zone points.";
                CacheTopologySnapshot(request, error, false);
                return false;
            }

            var exitsSource = ResolveExitPoints();
            var exits = new List<TopologyExitDto>(exitsSource.Length);
            for (var i = 0; i < exitsSource.Length; i++)
            {
                var point = exitsSource[i];
                if (point == null)
                {
                    continue;
                }

                TopologyExitDto exit;
                if (!point.TryBuildDto(out exit) || exit == null)
                {
                    continue;
                }

                if (!zoneIdSet.Contains(exit.source_zone_id) || !zoneIdSet.Contains(exit.target_zone_id))
                {
                    request = null;
                    error = "Exit references unknown zone. exit_id=" + exit.exit_id;
                    CacheTopologySnapshot(request, error, false);
                    return false;
                }

                exits.Add(exit);
            }

            request = new TopologyUpsertRequestDto
            {
                zones = zones.ToArray(),
                exits = exits.ToArray(),
            };
            error = string.Empty;
            CacheTopologySnapshot(request, error, true);
            return true;
        }

        private void CacheTopologySnapshot(TopologyUpsertRequestDto request, string error, bool isValid)
        {
            cachedTopologyRequest = request;
            cachedTopologyError = string.IsNullOrWhiteSpace(error) ? string.Empty : error.Trim();
            cachedTopologyValid = isValid;
            topologySnapshotCacheDirty = false;
        }

        private void MarkTopologyDirty()
        {
            topologyPointsCacheDirty = true;
            topologySnapshotCacheDirty = true;
            cachedTopologyRequest = null;
            cachedTopologyError = string.Empty;
            cachedTopologyValid = false;
            unchecked
            {
                topologyVersion++;
                if (topologyVersion <= 0)
                {
                    topologyVersion = 1;
                }
            }
        }

        private void EnsureTopologyPointsCache()
        {
            if (!topologyPointsCacheDirty)
            {
                return;
            }

            if (autoCollectFromChildren)
            {
                cachedResolvedZonePoints = GetComponentsInChildren<TopologyZonePoint>(true) ?? Array.Empty<TopologyZonePoint>();
                cachedResolvedExitPoints = GetComponentsInChildren<TopologyExitPoint>(true) ?? Array.Empty<TopologyExitPoint>();
            }
            else
            {
                cachedResolvedZonePoints = zonePoints ?? Array.Empty<TopologyZonePoint>();
                cachedResolvedExitPoints = exitPoints ?? Array.Empty<TopologyExitPoint>();
            }

            topologyPointsCacheDirty = false;
        }

        public bool TryGetPreferredSpawnZoneId(out string zoneId)
        {
            if (!string.IsNullOrWhiteSpace(preferredSpawnZoneId))
            {
                zoneId = preferredSpawnZoneId.Trim();
                return true;
            }

            var zonesSource = ResolveZonePoints();
            for (var i = 0; i < zonesSource.Length; i++)
            {
                var point = zonesSource[i];
                if (point == null)
                {
                    continue;
                }

                TopologyZoneDto zone;
                if (!point.TryBuildDto(out zone) || zone == null)
                {
                    continue;
                }

                if (zone.is_entry_zone)
                {
                    zoneId = zone.zone_id;
                    return true;
                }
            }

            for (var i = 0; i < zonesSource.Length; i++)
            {
                var point = zonesSource[i];
                if (point == null)
                {
                    continue;
                }

                TopologyZoneDto zone;
                if (!point.TryBuildDto(out zone) || zone == null)
                {
                    continue;
                }

                zoneId = zone.zone_id;
                return true;
            }

            zoneId = string.Empty;
            return false;
        }

        public bool TryGetExitById(string exitId, out TopologyExitDto exit)
        {
            var normalizedExitId = string.IsNullOrWhiteSpace(exitId) ? string.Empty : exitId.Trim();
            if (string.IsNullOrWhiteSpace(normalizedExitId))
            {
                exit = null;
                return false;
            }

            var exitsSource = ResolveExitPoints();
            for (var i = 0; i < exitsSource.Length; i++)
            {
                var point = exitsSource[i];
                if (point == null)
                {
                    continue;
                }

                TopologyExitDto candidate;
                if (!point.TryBuildDto(out candidate)
                    || candidate == null
                    || string.IsNullOrWhiteSpace(candidate.exit_id))
                {
                    continue;
                }

                if (!string.Equals(candidate.exit_id, normalizedExitId, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                exit = candidate;
                return true;
            }

            exit = null;
            return false;
        }

        public bool TryGetNearestExitBySourceZone(
            string sourceZoneId,
            Vector3 referencePosition,
            out TopologyExitDto nearestExit,
            out float planarDistance)
        {
            var normalizedSourceZoneId = string.IsNullOrWhiteSpace(sourceZoneId) ? string.Empty : sourceZoneId.Trim();
            if (string.IsNullOrWhiteSpace(normalizedSourceZoneId))
            {
                nearestExit = null;
                planarDistance = float.PositiveInfinity;
                return false;
            }

            nearestExit = null;
            planarDistance = float.PositiveInfinity;

            var exitsSource = ResolveExitPoints();
            var reference = new Vector2(referencePosition.x, referencePosition.z);
            for (var i = 0; i < exitsSource.Length; i++)
            {
                var point = exitsSource[i];
                if (point == null)
                {
                    continue;
                }

                TopologyExitDto candidate;
                if (!point.TryBuildDto(out candidate)
                    || candidate == null
                    || string.IsNullOrWhiteSpace(candidate.exit_id)
                    || string.IsNullOrWhiteSpace(candidate.source_zone_id)
                    || candidate.position == null)
                {
                    continue;
                }

                if (!string.Equals(candidate.source_zone_id, normalizedSourceZoneId, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                var candidatePosition = new Vector2(candidate.position.x, candidate.position.z);
                var distance = Vector2.Distance(reference, candidatePosition);
                if (distance >= planarDistance)
                {
                    continue;
                }

                nearestExit = candidate;
                planarDistance = distance;
            }

            return nearestExit != null;
        }

        public bool TryGetZoneById(string zoneId, out TopologyZoneDto zone)
        {
            var normalizedZoneId = string.IsNullOrWhiteSpace(zoneId) ? string.Empty : zoneId.Trim();
            if (string.IsNullOrWhiteSpace(normalizedZoneId))
            {
                zone = null;
                return false;
            }

            var zonesSource = ResolveZonePoints();
            for (var i = 0; i < zonesSource.Length; i++)
            {
                var point = zonesSource[i];
                if (point == null)
                {
                    continue;
                }

                TopologyZoneDto candidate;
                if (!point.TryBuildDto(out candidate)
                    || candidate == null
                    || string.IsNullOrWhiteSpace(candidate.zone_id))
                {
                    continue;
                }

                if (!string.Equals(candidate.zone_id, normalizedZoneId, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                zone = candidate;
                return true;
            }

            zone = null;
            return false;
        }

        public bool TryGetRandomPointInZone(string zoneId, float y, float edgePadding, out Vector3 position)
        {
            TopologyZoneDto zone;
            if (!TryGetZoneById(zoneId, out zone)
                || zone == null
                || zone.bounds == null)
            {
                position = default;
                return false;
            }

            var minX = Mathf.Min(zone.bounds.min_x, zone.bounds.max_x);
            var maxX = Mathf.Max(zone.bounds.min_x, zone.bounds.max_x);
            var minZ = Mathf.Min(zone.bounds.min_z, zone.bounds.max_z);
            var maxZ = Mathf.Max(zone.bounds.min_z, zone.bounds.max_z);

            var width = Mathf.Max(0.01f, maxX - minX);
            var depth = Mathf.Max(0.01f, maxZ - minZ);
            var padding = Mathf.Max(0f, edgePadding);
            var maxPaddingX = width * 0.49f;
            var maxPaddingZ = depth * 0.49f;
            var paddingX = Mathf.Min(padding, maxPaddingX);
            var paddingZ = Mathf.Min(padding, maxPaddingZ);

            var sampleMinX = minX + paddingX;
            var sampleMaxX = maxX - paddingX;
            var sampleMinZ = minZ + paddingZ;
            var sampleMaxZ = maxZ - paddingZ;

            var x = sampleMinX >= sampleMaxX
                ? (minX + maxX) * 0.5f
                : UnityEngine.Random.Range(sampleMinX, sampleMaxX);
            var z = sampleMinZ >= sampleMaxZ
                ? (minZ + maxZ) * 0.5f
                : UnityEngine.Random.Range(sampleMinZ, sampleMaxZ);

            position = new Vector3(x, y, z);
            return true;
        }

        public void NotifyAgentArrivedToZone(string zoneId)
        {
            var normalizedZoneId = string.IsNullOrWhiteSpace(zoneId) ? string.Empty : zoneId.Trim();
            if (string.IsNullOrWhiteSpace(normalizedZoneId))
            {
                return;
            }

            EnsureRuntimeArrivalMapInitialized();

            int current;
            if (!zoneArrivalCountMap.TryGetValue(normalizedZoneId, out current))
            {
                zoneArrivalCountMap[normalizedZoneId] = 1;
            }
            else
            {
                zoneArrivalCountMap[normalizedZoneId] = current + 1;
            }

            SyncRuntimeArrivalSnapshot();
        }

        public int GetZoneArrivedCount(string zoneId)
        {
            var normalizedZoneId = string.IsNullOrWhiteSpace(zoneId) ? string.Empty : zoneId.Trim();
            if (string.IsNullOrWhiteSpace(normalizedZoneId))
            {
                return 0;
            }

            EnsureRuntimeArrivalMapInitialized();

            int count;
            return zoneArrivalCountMap.TryGetValue(normalizedZoneId, out count)
                ? Mathf.Max(0, count)
                : 0;
        }

        [ContextMenu("Reset Runtime Zone Arrival Counters")]
        public void ResetRuntimeZoneArrivalCounters()
        {
            zoneArrivalCountMap.Clear();
            zoneRuntimeArrivals = Array.Empty<ZoneRuntimeArrivalRecord>();
        }

        [ContextMenu("Refresh Topology Points From Children")]
        public void RefreshFromChildren()
        {
            zonePoints = GetComponentsInChildren<TopologyZonePoint>(true);
            exitPoints = GetComponentsInChildren<TopologyExitPoint>(true);
            MarkTopologyDirty();
        }

        private void EnsureRuntimeArrivalMapInitialized()
        {
            if (zoneArrivalCountMap.Count > 0 || zoneRuntimeArrivals == null || zoneRuntimeArrivals.Length <= 0)
            {
                return;
            }

            for (var i = 0; i < zoneRuntimeArrivals.Length; i++)
            {
                var record = zoneRuntimeArrivals[i];
                if (record == null || string.IsNullOrWhiteSpace(record.zone_id))
                {
                    continue;
                }

                var zoneId = record.zone_id.Trim();
                var count = Mathf.Max(0, record.arrived_count);
                int existing;
                if (zoneArrivalCountMap.TryGetValue(zoneId, out existing))
                {
                    zoneArrivalCountMap[zoneId] = existing + count;
                }
                else
                {
                    zoneArrivalCountMap[zoneId] = count;
                }
            }
        }

        private void SyncRuntimeArrivalSnapshot()
        {
            if (zoneArrivalCountMap.Count <= 0)
            {
                zoneRuntimeArrivals = Array.Empty<ZoneRuntimeArrivalRecord>();
                return;
            }

            var snapshot = new List<ZoneRuntimeArrivalRecord>(zoneArrivalCountMap.Count);
            foreach (var pair in zoneArrivalCountMap)
            {
                snapshot.Add(new ZoneRuntimeArrivalRecord
                {
                    zone_id = pair.Key,
                    arrived_count = Mathf.Max(0, pair.Value),
                });
            }

            snapshot.Sort((left, right) => string.Compare(left.zone_id, right.zone_id, StringComparison.OrdinalIgnoreCase));
            zoneRuntimeArrivals = snapshot.ToArray();
        }

        private TopologyZonePoint[] ResolveZonePoints()
        {
            EnsureTopologyPointsCache();
            return cachedResolvedZonePoints ?? Array.Empty<TopologyZonePoint>();
        }

        private TopologyExitPoint[] ResolveExitPoints()
        {
            EnsureTopologyPointsCache();
            return cachedResolvedExitPoints ?? Array.Empty<TopologyExitPoint>();
        }

        public void BuildLookupMaps(
            Dictionary<string, int> zoneCapacityById,
            Dictionary<string, string> zoneTypeById,
            Dictionary<string, TopologyExitDto> topologyExitById)
        {
            if (zoneCapacityById != null)
            {
                zoneCapacityById.Clear();
                var zones = ResolveZonePoints();
                for (var i = 0; i < zones.Length; i++)
                {
                    var point = zones[i];
                    if (point == null)
                    {
                        continue;
                    }

                    TopologyZoneDto dto;
                    if (point.TryBuildDto(out dto) && dto != null && !string.IsNullOrWhiteSpace(dto.zone_id))
                    {
                        zoneCapacityById[dto.zone_id.Trim()] = Math.Max(1, dto.capacity);
                    }
                }
            }

            if (zoneTypeById != null)
            {
                zoneTypeById.Clear();
                var zones = ResolveZonePoints();
                for (var i = 0; i < zones.Length; i++)
                {
                    var point = zones[i];
                    if (point == null)
                    {
                        continue;
                    }

                    TopologyZoneDto dto;
                    if (point.TryBuildDto(out dto) && dto != null && !string.IsNullOrWhiteSpace(dto.zone_id))
                    {
                        zoneTypeById[dto.zone_id.Trim()] = string.IsNullOrWhiteSpace(dto.zone_type)
                            ? "other"
                            : dto.zone_type.Trim();
                    }
                }
            }

            if (topologyExitById != null)
            {
                topologyExitById.Clear();
                var exits = ResolveExitPoints();
                for (var i = 0; i < exits.Length; i++)
                {
                    var point = exits[i];
                    if (point == null)
                    {
                        continue;
                    }

                    TopologyExitDto dto;
                    if (point.TryBuildDto(out dto) && dto != null && !string.IsNullOrWhiteSpace(dto.exit_id))
                    {
                        topologyExitById[dto.exit_id.Trim()] = dto;
                    }
                }
            }
        }
    }
}
