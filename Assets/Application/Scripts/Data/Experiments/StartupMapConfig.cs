using System;
using UnityEngine;

namespace CrowdSim.Data.Experiments
{
    [CreateAssetMenu(fileName = "StartupMapConfig", menuName = "CrowdSim/Experiments/Startup Map Config")]
    public sealed class StartupMapConfig : ScriptableObject
    {
        [SerializeField] private string mapId = "unity_2d_map";
        [SerializeField] private string displayName = "Sample Scene";
        [SerializeField] private string scenePath = "Assets/Application/Scenes/SampleScene.unity";
        [SerializeField] private Sprite previewImage;
        [SerializeField, TextArea(2, 6)] private string description = "";

        public string MapId
        {
            get { return mapId; }
        }

        public string DisplayName
        {
            get { return displayName; }
        }

        public string ScenePath
        {
            get { return scenePath; }
        }

        public Sprite PreviewImage
        {
            get { return previewImage; }
        }

        public string Description
        {
            get { return description; }
        }

        public string BuildLabel()
        {
            var normalizedName = string.IsNullOrWhiteSpace(displayName)
                ? "(unnamed map)"
                : displayName.Trim();
            var normalizedMapId = string.IsNullOrWhiteSpace(mapId)
                ? "(empty map_id)"
                : mapId.Trim();
            var normalizedScenePath = string.IsNullOrWhiteSpace(scenePath)
                ? "(empty scene path)"
                : scenePath.Trim();

            return normalizedName + " | map_id=" + normalizedMapId + " | scene=" + normalizedScenePath;
        }

        public bool TryGetSceneName(out string sceneName)
        {
            if (string.IsNullOrWhiteSpace(scenePath))
            {
                sceneName = string.Empty;
                return false;
            }

            var normalizedPath = scenePath.Trim().Replace("\\", "/");
            var lastSlash = normalizedPath.LastIndexOf('/');
            var fileName = lastSlash >= 0 ? normalizedPath.Substring(lastSlash + 1) : normalizedPath;
            if (fileName.EndsWith(".unity", StringComparison.OrdinalIgnoreCase))
            {
                fileName = fileName.Substring(0, fileName.Length - ".unity".Length);
            }

            sceneName = string.IsNullOrWhiteSpace(fileName) ? string.Empty : fileName.Trim();
            return !string.IsNullOrWhiteSpace(sceneName);
        }

        public bool IsConfigured()
        {
            if (string.IsNullOrWhiteSpace(mapId))
            {
                return false;
            }

            string sceneName;
            return TryGetSceneName(out sceneName);
        }
    }
}
