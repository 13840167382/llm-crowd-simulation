using UnityEngine.Serialization;
using CrowdSim.Data.Enums;
using CrowdSim.Data.Topology;
using UnityEngine;

namespace CrowdSim.Simulation.Topology
{
    public sealed class TopologyExitPoint : MonoBehaviour
    {
        [SerializeField] private string exitId = "exit_01";
        [SerializeField] private bool autoResolveSourceZoneFromParent = true;
        [SerializeField] private TopologyZonePoint sourceZonePoint;
        [SerializeField] private TopologyZonePoint targetZonePoint;
        [SerializeField, HideInInspector, FormerlySerializedAs("sourceZoneId")] private string sourceZoneIdFallback = string.Empty;
        [SerializeField, HideInInspector, FormerlySerializedAs("targetZoneId")] private string targetZoneIdFallback = string.Empty;
        [SerializeField] private ExitType exitType = ExitType.Portal;
        [SerializeField, Min(1)] private int passCapacityPerSec = 3;

        public bool TryBuildDto(out TopologyExitDto exit)
        {
            var id = string.IsNullOrWhiteSpace(exitId) ? string.Empty : exitId.Trim();
            string sourceId;
            string targetId;

            if (string.IsNullOrWhiteSpace(id)
                || !TryResolveSourceZoneId(out sourceId)
                || !TryResolveTargetZoneId(out targetId))
            {
                exit = null;
                return false;
            }

            var position = transform.position;
            exit = new TopologyExitDto
            {
                exit_id = id,
                source_zone_id = sourceId,
                target_zone_id = targetId,
                exit_type = exitType.ToApiValue(),
                pass_capacity_per_sec = Mathf.Max(1, passCapacityPerSec),
                position = new Position2DDto
                {
                    x = position.x,
                    z = position.z,
                },
            };
            return true;
        }

        private bool TryResolveSourceZoneId(out string zoneId)
        {
            var sourcePoint = ResolveSourceZonePoint();
            if (sourcePoint != null && sourcePoint.TryGetZoneId(out zoneId))
            {
                return true;
            }

            zoneId = string.IsNullOrWhiteSpace(sourceZoneIdFallback) ? string.Empty : sourceZoneIdFallback.Trim();
            return !string.IsNullOrWhiteSpace(zoneId);
        }

        private bool TryResolveTargetZoneId(out string zoneId)
        {
            if (targetZonePoint != null && targetZonePoint.TryGetZoneId(out zoneId))
            {
                return true;
            }

            zoneId = string.IsNullOrWhiteSpace(targetZoneIdFallback) ? string.Empty : targetZoneIdFallback.Trim();
            return !string.IsNullOrWhiteSpace(zoneId);
        }

        private TopologyZonePoint ResolveSourceZonePoint()
        {
            if (sourceZonePoint != null)
            {
                return sourceZonePoint;
            }

            if (!autoResolveSourceZoneFromParent)
            {
                return null;
            }

            sourceZonePoint = GetComponentInParent<TopologyZonePoint>();
            return sourceZonePoint;
        }

#if UNITY_EDITOR
        private void OnValidate()
        {
            if (!autoResolveSourceZoneFromParent)
            {
                return;
            }

            var parentZone = GetComponentInParent<TopologyZonePoint>();
            if (parentZone != null)
            {
                sourceZonePoint = parentZone;
            }
        }

        private void OnDrawGizmos()
        {
            var oldColor = Gizmos.color;
            Gizmos.color = new Color(1f, 0.7f, 0.2f, 0.85f);
            Gizmos.DrawSphere(transform.position, 0.2f);
            Gizmos.DrawLine(transform.position, transform.position + transform.forward * 0.8f);

            if (targetZonePoint != null)
            {
                Gizmos.DrawLine(transform.position, targetZonePoint.transform.position);
            }

            Gizmos.color = oldColor;
        }
#endif
    }
}
