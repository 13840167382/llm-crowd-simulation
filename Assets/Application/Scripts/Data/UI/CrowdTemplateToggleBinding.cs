using System;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace CrowdSim.Data.UI
{
    [Serializable]
    public sealed class CrowdTemplateToggleBinding
    {
        [SerializeField] private Toggle toggle;
        [SerializeField] private TMP_Text titleTmpText;
        [SerializeField] private TMP_Text metaTmpText;

        public Toggle Toggle
        {
            get { return toggle; }
        }

        public void EnsureTextReferences()
        {
            if (toggle == null)
            {
                return;
            }

            if (titleTmpText != null && metaTmpText != null)
            {
                return;
            }

            var texts = toggle.GetComponentsInChildren<TMP_Text>(true);
            if (texts == null || texts.Length <= 0)
            {
                return;
            }

            if (titleTmpText == null)
            {
                titleTmpText = texts[0];
            }

            if (metaTmpText == null && texts.Length > 1)
            {
                metaTmpText = texts[1];
            }
        }

        public void SetTitle(string value)
        {
            if (titleTmpText != null)
            {
                titleTmpText.text = value;
            }
        }

        public void SetMeta(string value)
        {
            if (metaTmpText != null)
            {
                metaTmpText.text = value;
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
