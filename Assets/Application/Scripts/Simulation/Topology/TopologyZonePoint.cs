using CrowdSim.Data.Enums;
using CrowdSim.Data.Topology;
using UnityEngine;

namespace CrowdSim.Simulation.Topology
{
    public sealed class TopologyZonePoint : MonoBehaviour
    {
        [SerializeField] private string zoneId = "entry_zone_01";
        [SerializeField] private string zoneName = "Entry Zone";
        [SerializeField] private ZoneType zoneType = ZoneType.Plaza;
        [SerializeField, Min(1)] private int capacity = 100;
        [SerializeField] private bool isEntryZone = true;
        [SerializeField] private Vector2 size = new Vector2(10f, 10f);

        public bool TryGetZoneId(out string id)
        {
            id = string.IsNullOrWhiteSpace(zoneId) ? string.Empty : zoneId.Trim();
            return !string.IsNullOrWhiteSpace(id);
        }

        public bool TryBuildDto(out TopologyZoneDto zone)
        {
            string id;
            if (!TryGetZoneId(out id))
            {
                zone = null;
                return false;
            }

            var normalizedSize = new Vector2(Mathf.Max(0.1f, size.x), Mathf.Max(0.1f, size.y));
            var center = transform.position;
            var halfX = normalizedSize.x * 0.5f;
            var halfZ = normalizedSize.y * 0.5f;

            zone = new TopologyZoneDto
            {
                zone_id = id,
                zone_name = string.IsNullOrWhiteSpace(zoneName) ? id : zoneName.Trim(),
                zone_type = zoneType.ToApiValue(),
                capacity = Mathf.Max(1, capacity),
                is_entry_zone = isEntryZone,
                bounds = new Bounds2DDto
                {
                    min_x = center.x - halfX,
                    min_z = center.z - halfZ,
                    max_x = center.x + halfX,
                    max_z = center.z + halfZ,
                },
            };
            return true;
        }

#if UNITY_EDITOR
        private void OnDrawGizmos()
        {
            var center = transform.position;
            var drawSize = new Vector3(Mathf.Max(0.1f, size.x), 0.05f, Mathf.Max(0.1f, size.y));
            var oldColor = Gizmos.color;
            Gizmos.color = isEntryZone ? new Color(0.2f, 0.9f, 0.2f, 0.75f) : new Color(0.2f, 0.8f, 1f, 0.75f);
            Gizmos.DrawCube(center, drawSize);
            Gizmos.color = oldColor;
        }
#endif
    }
}
