using System;
using Cysharp.Threading.Tasks;
using CrowdSim.Core;
using CrowdSim.Data.UI;
using TMPro;
using UnityEngine;
using UnityEngine.Events;
using UnityEngine.UI;

namespace CrowdSim.UI
{
    public sealed class StartupMapSelectionPanel : MonoBehaviour
    {
        [Header("Dependencies")]
        [SerializeField] private ExperimentManager experimentManager;

        [Header("Map Toggle List")]
        [SerializeField] private StartupMapToggleBinding[] mapToggleBindings = Array.Empty<StartupMapToggleBinding>();

        [Header("Selected Map Display")]
        [SerializeField] private TMP_Text selectedMapNameTmpText;
        [SerializeField] private Image selectedMapPreviewImage;

        [Header("State")]
        [SerializeField, Min(0)] private int selectedMapIndex;

        private UnityAction<bool>[] toggleHandlers = Array.Empty<UnityAction<bool>>();
        private bool suppressToggleEvent;

        private void Awake()
        {
            TryResolveManager();
            BindToggleListeners();
        }

        private void Start()
        {
            RefreshMapOptionsFromManager();
        }

        private void OnDestroy()
        {
            UnbindToggleListeners();
        }

        public void RefreshMapOptionsFromManager()
        {
            if (!TryResolveManager())
            {
                SetSelectedMapView(string.Empty, null);
                return;
            }

            var mapNames = experimentManager.GetStartupMapDisplayNames();
            var mapCount = mapNames.Length;
            selectedMapIndex = mapCount > 0
                ? Mathf.Clamp(experimentManager.GetSelectedStartupMapIndex(), 0, mapCount - 1)
                : 0;

            ApplyToggleLabels(mapNames);
            SyncToggleSelection(selectedMapIndex);
            RefreshSelectedMapView();
        }

        public void OnClickLoadSelectedMap()
        {
            if (!TryResolveManager())
            {
                Debug.LogWarning("StartupMapSelectionPanel: ExperimentManager is missing.");
                return;
            }

            experimentManager.SetSelectedStartupMapIndex(selectedMapIndex);
            experimentManager.StartupSelectMapAndLoadSceneAsync().Forget();
        }

        private void HandleToggleSelected(int index)
        {
            if (!TryResolveManager())
            {
                return;
            }

            var mapCount = experimentManager.GetStartupMapCount();
            if (mapCount <= 0)
            {
                selectedMapIndex = 0;
                SetSelectedMapView(string.Empty, null);
                return;
            }

            selectedMapIndex = Mathf.Clamp(index, 0, mapCount - 1);
            experimentManager.SetSelectedStartupMapIndex(selectedMapIndex);
            SyncToggleSelection(selectedMapIndex);
            RefreshSelectedMapView();
        }

        private void ApplyToggleLabels(string[] mapNames)
        {
            if (mapToggleBindings == null || mapToggleBindings.Length <= 0)
            {
                return;
            }

            var mapCount = mapNames != null ? mapNames.Length : 0;
            for (var i = 0; i < mapToggleBindings.Length; i++)
            {
                var binding = mapToggleBindings[i];
                if (binding == null || binding.Toggle == null)
                {
                    continue;
                }

                binding.EnsureLabelReferences();
                var isActive = i < mapCount;
                binding.SetActive(isActive);
                binding.Toggle.interactable = isActive;
                if (!isActive)
                {
                    continue;
                }

                binding.SetLabel(mapNames[i]);
            }

            if (mapCount > mapToggleBindings.Length)
            {
                Debug.LogWarning("StartupMapSelectionPanel: map count exceeds available toggle slots.");
            }
        }

        private void RefreshSelectedMapView()
        {
            if (!TryResolveManager())
            {
                SetSelectedMapView(string.Empty, null);
                return;
            }

            var mapCount = experimentManager.GetStartupMapCount();
            if (mapCount <= 0)
            {
                SetSelectedMapView(string.Empty, null);
                return;
            }

            selectedMapIndex = Mathf.Clamp(selectedMapIndex, 0, mapCount - 1);
            var mapName = experimentManager.GetStartupMapDisplayName(selectedMapIndex);
            var previewImage = experimentManager.GetStartupMapPreviewImage(selectedMapIndex);
            SetSelectedMapView(mapName, previewImage);
        }

        private void SetSelectedMapView(string mapName, Sprite previewSprite)
        {
            if (selectedMapNameTmpText != null)
            {
                selectedMapNameTmpText.text = mapName;
            }

            if (selectedMapPreviewImage != null)
            {
                selectedMapPreviewImage.sprite = previewSprite;
                selectedMapPreviewImage.enabled = previewSprite != null;
            }
        }

        private void SyncToggleSelection(int selectedIndex)
        {
            if (mapToggleBindings == null || mapToggleBindings.Length <= 0)
            {
                return;
            }

            suppressToggleEvent = true;
            try
            {
                for (var i = 0; i < mapToggleBindings.Length; i++)
                {
                    var binding = mapToggleBindings[i];
                    if (binding == null || binding.Toggle == null || !binding.Toggle.gameObject.activeSelf)
                    {
                        continue;
                    }

                    binding.Toggle.isOn = i == selectedIndex;
                }
            }
            finally
            {
                suppressToggleEvent = false;
            }
        }

        private void BindToggleListeners()
        {
            UnbindToggleListeners();

            if (mapToggleBindings == null || mapToggleBindings.Length <= 0)
            {
                toggleHandlers = Array.Empty<UnityAction<bool>>();
                return;
            }

            toggleHandlers = new UnityAction<bool>[mapToggleBindings.Length];
            for (var i = 0; i < mapToggleBindings.Length; i++)
            {
                var binding = mapToggleBindings[i];
                if (binding == null || binding.Toggle == null)
                {
                    continue;
                }

                var index = i;
                UnityAction<bool> handler = isOn =>
                {
                    if (!isOn || suppressToggleEvent)
                    {
                        return;
                    }

                    HandleToggleSelected(index);
                };

                toggleHandlers[i] = handler;
                binding.Toggle.onValueChanged.AddListener(handler);
            }
        }

        private void UnbindToggleListeners()
        {
            if (mapToggleBindings == null || toggleHandlers == null)
            {
                return;
            }

            var count = Math.Min(mapToggleBindings.Length, toggleHandlers.Length);
            for (var i = 0; i < count; i++)
            {
                var binding = mapToggleBindings[i];
                var handler = toggleHandlers[i];
                if (binding == null || binding.Toggle == null || handler == null)
                {
                    continue;
                }

                binding.Toggle.onValueChanged.RemoveListener(handler);
            }

            toggleHandlers = Array.Empty<UnityAction<bool>>();
        }

        private bool TryResolveManager()
        {
            if (experimentManager != null)
            {
                return true;
            }

            experimentManager = ExperimentManager.ActiveInstance;
            return experimentManager != null;
        }
    }
}
