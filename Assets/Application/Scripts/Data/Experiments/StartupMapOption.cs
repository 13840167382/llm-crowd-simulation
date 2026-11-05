using System;

namespace CrowdSim.Data.Experiments
{
    [Serializable]
    public sealed class StartupMapOption
    {
        public string displayName = "Sample Scene";
        public string mapId = "unity_2d_map";
        public string scenePath = "Assets/Application/Scenes/SampleScene.unity";

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