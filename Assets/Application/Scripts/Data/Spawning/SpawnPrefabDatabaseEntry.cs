using System;
using UnityEngine;

namespace CrowdSim.Data.Spawning
{
    [Serializable]
    public sealed class SpawnPrefabDatabaseEntry
    {
        public string typeId;
        public GameObject prefab;
    }
}
