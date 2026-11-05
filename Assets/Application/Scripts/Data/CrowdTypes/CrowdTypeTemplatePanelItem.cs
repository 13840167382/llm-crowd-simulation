using System;

namespace CrowdSim.Data.CrowdTypes
{
    [Serializable]
    public sealed class CrowdTypeTemplatePanelItem
    {
        public string typeId;
        public string displayName;
        public bool isBuiltin;
        public bool existsInBackend;
        public bool existsInLocalConfig;
        public CrowdTypeTemplateDto backendTemplate;
        public CrowdTypeTemplateConfigEntry localConfigEntry;
    }
}
