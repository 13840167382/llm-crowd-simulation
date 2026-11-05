using System;
using System.Globalization;
using TMPro;
using UnityEngine;

namespace CrowdSim.Data.UI
{
    [Serializable]
    public sealed class CrowdTypeCountInputBinding
    {
        [SerializeField] private GameObject rowRoot;
        [SerializeField] private TMP_Text typeNameTmpText;
        [SerializeField] private TMP_InputField countInputField;

        public TMP_InputField CountInputField
        {
            get { return countInputField; }
        }

        public void SetActive(bool isActive)
        {
            if (rowRoot != null)
            {
                rowRoot.SetActive(isActive);
                return;
            }

            if (countInputField != null)
            {
                countInputField.gameObject.SetActive(isActive);
            }
        }

        public void SetInteractable(bool interactable)
        {
            if (countInputField != null)
            {
                countInputField.interactable = interactable;
            }
        }

        public void SetTypeName(string typeName)
        {
            if (typeNameTmpText != null)
            {
                typeNameTmpText.text = typeName;
            }
        }

        public void SetCount(int count)
        {
            if (countInputField == null)
            {
                return;
            }

            countInputField.text = Math.Max(0, count).ToString(CultureInfo.InvariantCulture);
        }

        public string GetRawInput()
        {
            if (countInputField == null || string.IsNullOrWhiteSpace(countInputField.text))
            {
                return string.Empty;
            }

            return countInputField.text.Trim();
        }
    }
}
