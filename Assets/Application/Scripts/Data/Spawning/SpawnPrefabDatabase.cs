using System;
using UnityEngine;

namespace CrowdSim.Data.Spawning
{
    [CreateAssetMenu(fileName = "SpawnPrefabDatabase", menuName = "CrowdSim/Spawn Prefab Database")]
    public sealed class SpawnPrefabDatabase : ScriptableObject
    {
        [SerializeField] private GameObject defaultPrefab;
        [SerializeField] private SpawnPrefabDatabaseEntry[] entries = Array.Empty<SpawnPrefabDatabaseEntry>();

        public bool TryGetPrefab(string typeId, out GameObject prefab)
        {
            var normalizedTypeId = string.IsNullOrWhiteSpace(typeId) ? string.Empty : typeId.Trim();

            if (entries != null)
            {
                for (var i = 0; i < entries.Length; i++)
                {
                    var entry = entries[i];
                    if (entry == null)
                    {
                        continue;
                    }

                    var configuredTypeId = string.IsNullOrWhiteSpace(entry.typeId) ? string.Empty : entry.typeId.Trim();
                    if (!string.Equals(configuredTypeId, normalizedTypeId, StringComparison.OrdinalIgnoreCase))
                    {
                        continue;
                    }

                    if (entry.prefab != null)
                    {
                        prefab = entry.prefab;
                        return true;
                    }
                }
            }

            prefab = defaultPrefab;
            return prefab != null;
        }
    }
}
