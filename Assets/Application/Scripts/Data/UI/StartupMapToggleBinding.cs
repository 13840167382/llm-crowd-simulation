using System;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace CrowdSim.Data.UI
{
    [Serializable]
    public sealed class StartupMapToggleBinding
    {
        [SerializeField] private Toggle toggle;
        [SerializeField] private TMP_Text labelTmpText;

        public Toggle Toggle
        {
            get { return toggle; }
        }

        public void EnsureLabelReferences()
        {
            if (toggle == null)
            {
                return;
            }

            if (labelTmpText == null)
            {
                labelTmpText = toggle.GetComponentInChildren<TMP_Text>(true);
            }
        }

        public void SetLabel(string text)
        {
            if (labelTmpText != null)
            {
                labelTmpText.text = text;
            }
        }

        public void SetActive(bool isActive)
        {
            if (toggle != null)
            {
                toggle.gameObject.SetActive(isActive);
            }
        }
    }
}
