using System;
using System.Collections.Generic;
using UnityEngine;

namespace CrowdSim.Data.CrowdTypes
{
    [CreateAssetMenu(fileName = "CrowdTypeTemplateConfig", menuName = "CrowdSim/Crowd/Crowd Type Template Config")]
    public sealed class CrowdTypeTemplateConfig : ScriptableObject
    {
        [SerializeField] private bool syncBeforeCreate = true;
        [SerializeField] private bool deleteRemoteCustomTemplatesNotInConfig;
        [SerializeField] private CrowdTypeTemplateConfigEntry[] templates = Array.Empty<CrowdTypeTemplateConfigEntry>();

        public bool SyncBeforeCreate
        {
            get { return syncBeforeCreate; }
        }

        public bool DeleteRemoteCustomTemplatesNotInConfig
        {
            get { return deleteRemoteCustomTemplatesNotInConfig; }
        }

        public CrowdTypeTemplateConfigEntry[] CloneTemplates()
        {
            if (templates == null || templates.Length <= 0)
            {
                return Array.Empty<CrowdTypeTemplateConfigEntry>();
            }

            var cloned = new List<CrowdTypeTemplateConfigEntry>(templates.Length);
            for (var i = 0; i < templates.Length; i++)
            {
                var source = templates[i];
                if (source == null)
                {
                    continue;
                }

                cloned.Add(source.Clone());
            }

            return cloned.ToArray();
        }
    }
}
