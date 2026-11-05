using System;
using System.Collections.Generic;
using Cysharp.Threading.Tasks;
using CrowdSim.Core;
using CrowdSim.Data.Api;
using CrowdSim.Data.Common;
using CrowdSim.Data.CrowdTypes;
using CrowdSim.Data.UI;
using TMPro;
using UnityEngine;
using UnityEngine.Events;
using UnityEngine.Networking;
using UnityEngine.UI;

namespace CrowdSim.UI
{
    public sealed class CrowdTypeTemplateManagementPanel : MonoBehaviour
    {
        private struct FetchBackendTemplatesResult
        {
            public bool Success;
            public string Error;
            public CrowdTypeTemplateDto[] Templates;

            public static FetchBackendTemplatesResult BuildSuccess(CrowdTypeTemplateDto[] templates)
            {
                return new FetchBackendTemplatesResult
                {
                    Success = true,
                    Error = string.Empty,
                    Templates = templates ?? Array.Empty<CrowdTypeTemplateDto>(),
                };
            }

            public static FetchBackendTemplatesResult BuildFailure(string error)
            {
                return new FetchBackendTemplatesResult
                {
                    Success = false,
                    Error = string.IsNullOrWhiteSpace(error) ? "Failed to fetch crowd templates." : error,
                    Templates = Array.Empty<CrowdTypeTemplateDto>(),
                };
            }
        }

        [Header("Local Config")]
        [SerializeField] private CrowdTypeTemplateConfig crowdTypeTemplateConfig;
        [SerializeField] private bool preferLocalConfigValuesForEditableTemplates = true;

        [Header("Template List")]
        [SerializeField] private CrowdTemplateToggleBinding[] templateToggleBindings = Array.Empty<CrowdTemplateToggleBinding>();

        [Header("Template Detail")]
        [SerializeField] private CrowdTemplateDetailBinding templateDetailBinding = new CrowdTemplateDetailBinding();
        [SerializeField] private TMP_Text selectedTemplateSummaryTmpText;

        [Header("Actions")]
        [SerializeField] private Button refreshButton;
        [SerializeField] private Button uploadButton;
        [SerializeField] private TMP_Text uploadButtonLabelTmpText;

        [Header("Status")]
        [SerializeField] private TMP_Text statusTmpText;
        [SerializeField] private bool refreshOnStart = true;

        [Header("Runtime")]
        [SerializeField, Min(0)] private int selectedTemplateIndex;

        private CrowdTypeTemplatePanelItem[] panelItems = Array.Empty<CrowdTypeTemplatePanelItem>();
        private UnityAction<bool>[] toggleHandlers = Array.Empty<UnityAction<bool>>();
        private bool suppressToggleEvent;
        private bool isBusy;

        private void Awake()
        {
            BindToggleListeners();
            BindActionButtons();
            ApplyActionStates();
        }

        private void Start()
        {
            if (refreshOnStart)
            {
                RefreshTemplatesFromSources();
            }
            else
            {
                ReloadFromLocalConfigOnly();
            }
        }

        private void OnDestroy()
        {
            UnbindToggleListeners();
            UnbindActionButtons();
        }

        public void RefreshTemplatesFromSources()
        {
            if (isBusy)
            {
                return;
            }

            RefreshTemplatesFromSourcesAsync().Forget();
        }

        public void OnClickUploadSelectedTemplate()
        {
            if (isBusy)
            {
                return;
            }

            UploadSelectedTemplateAsync().Forget();
        }

        private async UniTaskVoid RefreshTemplatesFromSourcesAsync()
        {
            isBusy = true;
            ApplyActionStates();

            try
            {
                var preferredTypeId = TryGetSelectedItem(out var selected)
                    ? NormalizeTypeId(selected.typeId)
                    : string.Empty;
                await ReloadTemplatesAsync(preferredTypeId, true);
            }
            finally
            {
                isBusy = false;
                ApplyActionStates();
            }
        }

        private async UniTaskVoid UploadSelectedTemplateAsync()
        {
            if (!TryGetSelectedItem(out var selectedItem) || selectedItem == null)
            {
                SetStatus("未选中模板。", true);
                return;
            }

            if (selectedItem.isBuiltin)
            {
                SetStatus("后端 builtin 模板只读，不能修改。", true);
                RefreshSelectedTemplateDetail();
                return;
            }

            CrowdTypeCreateRequestDto createRequest;
            CrowdTypeUpdateRequestDto updateRequest;
            string buildError;
            if (!templateDetailBinding.TryBuildRequests(selectedItem.typeId, out createRequest, out updateRequest, out buildError))
            {
                SetStatus(buildError, true);
                return;
            }

            isBusy = true;
            ApplyActionStates();

            try
            {
                if (selectedItem.existsInBackend)
                {
                    var path = "/api/v1/crowd-types/" + UnityWebRequest.EscapeURL(selectedItem.typeId);
                    var raw = await CreateApiClient().PutJson(path, JsonUtility.ToJson(updateRequest), null);

                    ApiResponseMeta meta;
                    string apiError;
                    if (!TryEnsureApiSuccess(raw, "PUT /api/v1/crowd-types/{type_id}", out meta, out apiError))
                    {
                        SetStatus(apiError, true);
                        return;
                    }

                    await ReloadTemplatesAsync(NormalizeTypeId(selectedItem.typeId), false);
                    SetStatus("已上传模板更新：" + selectedItem.typeId, false);
                    return;
                }

                var createRaw = await CreateApiClient().PostJson("/api/v1/crowd-types", JsonUtility.ToJson(createRequest), null);
                ApiResponseMeta createMeta;
                string createError;
                if (!TryEnsureApiSuccess(createRaw, "POST /api/v1/crowd-types", out createMeta, out createError))
                {
                    SetStatus(createError, true);
                    return;
                }

                await ReloadTemplatesAsync(NormalizeTypeId(createRequest.type_id), false);
                SetStatus("已创建并上传模板：" + createRequest.type_id, false);
            }
            finally
            {
                isBusy = false;
                ApplyActionStates();
            }
        }

        private void ReloadFromLocalConfigOnly()
        {
            var localEntries = LoadLocalConfigEntries();
            panelItems = BuildPanelItems(Array.Empty<CrowdTypeTemplateDto>(), localEntries);
            selectedTemplateIndex = ResolveSelectionIndex(panelItems, string.Empty);
            ApplyTemplateListView();
            SetStatus("仅加载本地 crowd config。", false);
        }

        private async UniTask ReloadTemplatesAsync(string preferredTypeId, bool updateStatus)
        {
            var localEntries = LoadLocalConfigEntries();
            var fetchResult = await FetchBackendTemplatesAsync();

            if (!fetchResult.Success)
            {
                panelItems = BuildPanelItems(Array.Empty<CrowdTypeTemplateDto>(), localEntries);
                selectedTemplateIndex = ResolveSelectionIndex(panelItems, preferredTypeId);
                ApplyTemplateListView();

                if (panelItems.Length > 0)
                {
                    SetStatus("后端拉取失败，已回退为本地配置展示。" + fetchResult.Error, true);
                }
                else
                {
                    SetStatus(fetchResult.Error, true);
                }

                return;
            }

            panelItems = BuildPanelItems(fetchResult.Templates, localEntries);
            selectedTemplateIndex = ResolveSelectionIndex(panelItems, preferredTypeId);
            ApplyTemplateListView();

            if (updateStatus)
            {
                SetStatus("模板已刷新：后端 " + fetchResult.Templates.Length + " 条，本地 " + localEntries.Length + " 条。", false);
            }
        }

        private void ApplyTemplateListView()
        {
            ApplyTemplateToggleLabels();
            SyncToggleSelection(selectedTemplateIndex);
            RefreshSelectedTemplateDetail();
        }

        private void ApplyTemplateToggleLabels()
        {
            if (templateToggleBindings == null || templateToggleBindings.Length <= 0)
            {
                return;
            }

            for (var i = 0; i < templateToggleBindings.Length; i++)
            {
                var binding = templateToggleBindings[i];
                if (binding == null || binding.Toggle == null)
                {
                    continue;
                }

                binding.EnsureTextReferences();
                var isActive = i < panelItems.Length;
                binding.SetActive(isActive);
                if (!isActive)
                {
                    continue;
                }

                var item = panelItems[i];
                binding.SetTitle(ResolveDisplayName(item.displayName, item.typeId));
                binding.SetMeta(BuildListMeta(item));
                // binding.Toggle.interactable = !isBusy;
            }

            if (panelItems.Length > templateToggleBindings.Length)
            {
                Debug.LogWarning("CrowdTypeTemplateManagementPanel: template count exceeds available toggle slots.");
            }
        }

        private void RefreshSelectedTemplateDetail()
        {
            if (!TryGetSelectedItem(out var selected) || selected == null)
            {
                templateDetailBinding.ClearView();
                if (selectedTemplateSummaryTmpText != null)
                {
                    selectedTemplateSummaryTmpText.text = "";
                }

                ApplyActionStates();
                return;
            }

            var editorTemplate = ResolveEditorTemplate(selected);
            templateDetailBinding.PopulateFromTemplate(selected.typeId, editorTemplate);
            templateDetailBinding.SetEditable(!selected.isBuiltin, selected.isBuiltin);

            if (selectedTemplateSummaryTmpText != null)
            {
                selectedTemplateSummaryTmpText.text = BuildSelectedSummary(selected);
            }

            ApplyActionStates();
        }

        private void HandleToggleSelected(int index)
        {
            if (panelItems == null || panelItems.Length <= 0)
            {
                selectedTemplateIndex = 0;
                RefreshSelectedTemplateDetail();
                return;
            }

            selectedTemplateIndex = Mathf.Clamp(index, 0, panelItems.Length - 1);
            SyncToggleSelection(selectedTemplateIndex);
            RefreshSelectedTemplateDetail();
        }

        private void SyncToggleSelection(int selectedIndex)
        {
            if (templateToggleBindings == null || templateToggleBindings.Length <= 0)
            {
                return;
            }

            suppressToggleEvent = true;
            try
            {
                for (var i = 0; i < templateToggleBindings.Length; i++)
                {
                    var binding = templateToggleBindings[i];
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

            if (templateToggleBindings == null || templateToggleBindings.Length <= 0)
            {
                toggleHandlers = Array.Empty<UnityAction<bool>>();
                return;
            }

            toggleHandlers = new UnityAction<bool>[templateToggleBindings.Length];
            for (var i = 0; i < templateToggleBindings.Length; i++)
            {
                var binding = templateToggleBindings[i];
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
            if (templateToggleBindings == null || toggleHandlers == null)
            {
                return;
            }

            var count = Math.Min(templateToggleBindings.Length, toggleHandlers.Length);
            for (var i = 0; i < count; i++)
            {
                var binding = templateToggleBindings[i];
                var handler = toggleHandlers[i];
                if (binding == null || binding.Toggle == null || handler == null)
                {
                    continue;
                }

                binding.Toggle.onValueChanged.RemoveListener(handler);
            }

            toggleHandlers = Array.Empty<UnityAction<bool>>();
        }

        private void BindActionButtons()
        {
            if (refreshButton != null)
            {
                refreshButton.onClick.RemoveListener(RefreshTemplatesFromSources);
                refreshButton.onClick.AddListener(RefreshTemplatesFromSources);
            }

            if (uploadButton != null)
            {
                uploadButton.onClick.RemoveListener(OnClickUploadSelectedTemplate);
                uploadButton.onClick.AddListener(OnClickUploadSelectedTemplate);
            }
        }

        private void UnbindActionButtons()
        {
            if (refreshButton != null)
            {
                refreshButton.onClick.RemoveListener(RefreshTemplatesFromSources);
            }

            if (uploadButton != null)
            {
                uploadButton.onClick.RemoveListener(OnClickUploadSelectedTemplate);
            }
        }

        private void ApplyActionStates()
        {
            if (refreshButton != null)
            {
                refreshButton.interactable = !isBusy;
            }

            var canUpload = false;
            if (!isBusy && TryGetSelectedItem(out var selected) && selected != null)
            {
                canUpload = !selected.isBuiltin;
            }

            if (uploadButton != null)
            {
                uploadButton.interactable = canUpload;
            }

            if (uploadButtonLabelTmpText != null)
            {
                uploadButtonLabelTmpText.text = BuildUploadButtonLabel();
            }
        }

        private string BuildUploadButtonLabel()
        {
            if (isBusy)
            {
                return "处理中...";
            }

            if (!TryGetSelectedItem(out var selected) || selected == null)
            {
                return "Upload";
            }

            if (selected.isBuiltin)
            {
                return "Builtin 只读";
            }

            return selected.existsInBackend
                ? "上传更新"
                : "上传创建";
        }

        private CrowdTypeTemplateConfigEntry[] LoadLocalConfigEntries()
        {
            if (crowdTypeTemplateConfig == null)
            {
                return Array.Empty<CrowdTypeTemplateConfigEntry>();
            }

            var raw = crowdTypeTemplateConfig.CloneTemplates();
            if (raw == null || raw.Length <= 0)
            {
                return Array.Empty<CrowdTypeTemplateConfigEntry>();
            }

            var filtered = new List<CrowdTypeTemplateConfigEntry>(raw.Length);
            var seenTypeIds = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (var i = 0; i < raw.Length; i++)
            {
                var entry = raw[i];
                if (entry == null)
                {
                    continue;
                }

                var normalizedTypeId = NormalizeTypeId(entry.GetNormalizedTypeId());
                if (string.IsNullOrWhiteSpace(normalizedTypeId) || seenTypeIds.Contains(normalizedTypeId))
                {
                    continue;
                }

                seenTypeIds.Add(normalizedTypeId);
                filtered.Add(entry);
            }

            return filtered.ToArray();
        }

        private CrowdTypeTemplatePanelItem[] BuildPanelItems(
            CrowdTypeTemplateDto[] backendTemplates,
            CrowdTypeTemplateConfigEntry[] localEntries)
        {
            var result = new List<CrowdTypeTemplatePanelItem>();
            var localEntryByTypeId = new Dictionary<string, CrowdTypeTemplateConfigEntry>(StringComparer.OrdinalIgnoreCase);

            if (localEntries != null)
            {
                for (var i = 0; i < localEntries.Length; i++)
                {
                    var entry = localEntries[i];
                    if (entry == null)
                    {
                        continue;
                    }

                    var normalizedTypeId = NormalizeTypeId(entry.GetNormalizedTypeId());
                    if (string.IsNullOrWhiteSpace(normalizedTypeId) || localEntryByTypeId.ContainsKey(normalizedTypeId))
                    {
                        continue;
                    }

                    localEntryByTypeId.Add(normalizedTypeId, entry);
                }
            }

            var backendTypeIdSet = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            if (backendTemplates != null)
            {
                for (var i = 0; i < backendTemplates.Length; i++)
                {
                    var backendTemplate = backendTemplates[i];
                    if (backendTemplate == null)
                    {
                        continue;
                    }

                    var normalizedTypeId = NormalizeTypeId(backendTemplate.type_id);
                    if (string.IsNullOrWhiteSpace(normalizedTypeId) || backendTypeIdSet.Contains(normalizedTypeId))
                    {
                        continue;
                    }

                    backendTypeIdSet.Add(normalizedTypeId);

                    localEntryByTypeId.TryGetValue(normalizedTypeId, out var localEntry);
                    var item = new CrowdTypeTemplatePanelItem
                    {
                        typeId = normalizedTypeId,
                        displayName = ResolveDisplayName(backendTemplate.display_name, normalizedTypeId),
                        isBuiltin = backendTemplate.is_builtin,
                        existsInBackend = true,
                        existsInLocalConfig = localEntry != null,
                        backendTemplate = CloneTemplate(backendTemplate),
                        localConfigEntry = localEntry != null ? localEntry.Clone() : null,
                    };

                    result.Add(item);
                }
            }

            if (localEntries != null)
            {
                for (var i = 0; i < localEntries.Length; i++)
                {
                    var localEntry = localEntries[i];
                    if (localEntry == null)
                    {
                        continue;
                    }

                    CrowdTypeCreateRequestDto createRequest;
                    string normalizedTypeId;
                    string buildError;
                    if (!localEntry.TryBuildCreateRequest(out createRequest, out normalizedTypeId, out buildError))
                    {
                        continue;
                    }

                    if (backendTypeIdSet.Contains(normalizedTypeId))
                    {
                        continue;
                    }

                    var localOnlyItem = new CrowdTypeTemplatePanelItem
                    {
                        typeId = normalizedTypeId,
                        displayName = ResolveDisplayName(createRequest.display_name, normalizedTypeId),
                        isBuiltin = false,
                        existsInBackend = false,
                        existsInLocalConfig = true,
                        backendTemplate = null,
                        localConfigEntry = localEntry.Clone(),
                    };

                    result.Add(localOnlyItem);
                }
            }

            return result.ToArray();
        }

        private static int ResolveSelectionIndex(CrowdTypeTemplatePanelItem[] items, string preferredTypeId)
        {
            if (items == null || items.Length <= 0)
            {
                return 0;
            }

            if (!string.IsNullOrWhiteSpace(preferredTypeId))
            {
                var normalizedPreferred = NormalizeTypeId(preferredTypeId);
                for (var i = 0; i < items.Length; i++)
                {
                    var item = items[i];
                    if (item == null)
                    {
                        continue;
                    }

                    if (string.Equals(NormalizeTypeId(item.typeId), normalizedPreferred, StringComparison.OrdinalIgnoreCase))
                    {
                        return i;
                    }
                }
            }

            return 0;
        }

        private CrowdTypeTemplateDto ResolveEditorTemplate(CrowdTypeTemplatePanelItem item)
        {
            if (item == null)
            {
                return new CrowdTypeTemplateDto
                {
                    type_id = string.Empty,
                    display_name = string.Empty,
                    color_hex = "#336699",
                    base_speed = 1f,
                    social_distance = 0.8f,
                    tolerance_duration_ms = 30000,
                    attraction_weights = CrowdTypeAttractionWeightsDto.CreateDefault(),
                };
            }

            if (item.isBuiltin)
            {
                return item.backendTemplate != null
                    ? CloneTemplate(item.backendTemplate)
                    : BuildTemplateFromLocalEntry(item.localConfigEntry, item.typeId);
            }

            if (preferLocalConfigValuesForEditableTemplates && item.localConfigEntry != null)
            {
                return BuildTemplateFromLocalEntry(item.localConfigEntry, item.typeId);
            }

            if (item.backendTemplate != null)
            {
                return CloneTemplate(item.backendTemplate);
            }

            return BuildTemplateFromLocalEntry(item.localConfigEntry, item.typeId);
        }

        private static CrowdTypeTemplateDto BuildTemplateFromLocalEntry(CrowdTypeTemplateConfigEntry entry, string fallbackTypeId)
        {
            if (entry != null)
            {
                CrowdTypeCreateRequestDto request;
                string normalizedTypeId;
                string error;
                if (entry.TryBuildCreateRequest(out request, out normalizedTypeId, out error) && request != null)
                {
                    return new CrowdTypeTemplateDto
                    {
                        type_id = normalizedTypeId,
                        display_name = request.display_name,
                        color_hex = request.color_hex,
                        base_speed = request.base_speed,
                        social_distance = request.social_distance,
                        tolerance_duration_ms = request.tolerance_duration_ms,
                        attraction_weights = request.attraction_weights != null
                            ? request.attraction_weights.Clone()
                            : CrowdTypeAttractionWeightsDto.CreateDefault(),
                        is_builtin = false,
                    };
                }
            }

            var normalizedFallbackTypeId = NormalizeTypeId(fallbackTypeId);
            return new CrowdTypeTemplateDto
            {
                type_id = normalizedFallbackTypeId,
                display_name = ResolveDisplayName(string.Empty, normalizedFallbackTypeId),
                color_hex = "#336699",
                base_speed = 1f,
                social_distance = 0.8f,
                tolerance_duration_ms = 30000,
                attraction_weights = CrowdTypeAttractionWeightsDto.CreateDefault(),
                is_builtin = false,
            };
        }

        private static CrowdTypeTemplateDto CloneTemplate(CrowdTypeTemplateDto source)
        {
            if (source == null)
            {
                return null;
            }

            return new CrowdTypeTemplateDto
            {
                type_id = source.type_id,
                display_name = source.display_name,
                color_hex = source.color_hex,
                base_speed = source.base_speed,
                social_distance = source.social_distance,
                tolerance_duration_ms = source.tolerance_duration_ms,
                attraction_weights = source.attraction_weights != null
                    ? source.attraction_weights.Clone()
                    : CrowdTypeAttractionWeightsDto.CreateDefault(),
                is_builtin = source.is_builtin,
                created_at_ms = source.created_at_ms,
                updated_at_ms = source.updated_at_ms,
            };
        }

        private static string BuildListMeta(CrowdTypeTemplatePanelItem item)
        {
            if (item == null)
            {
                return string.Empty;
            }

            var source = item.existsInBackend ? "backend" : "local-only";
            var localTag = item.existsInLocalConfig ? " + local" : string.Empty;
            var builtinTag = item.isBuiltin ? " | builtin" : "";
            return item.typeId + " | " + source + localTag + builtinTag;
        }

        private static string BuildSelectedSummary(CrowdTypeTemplatePanelItem item)
        {
            if (item == null)
            {
                return string.Empty;
            }

            if (item.isBuiltin)
            {
                return "builtin 模板（只读）。";
            }

            if (item.existsInBackend)
            {
                return item.existsInLocalConfig
                    ? "后端模板，可编辑上传；本地 crowd config 存在对应条目。"
                    : "后端模板，可编辑上传。";
            }

            return "本地模板（后端不存在），上传后会创建。";
        }

        private bool TryGetSelectedItem(out CrowdTypeTemplatePanelItem item)
        {
            item = null;
            if (panelItems == null || panelItems.Length <= 0)
            {
                return false;
            }

            selectedTemplateIndex = Mathf.Clamp(selectedTemplateIndex, 0, panelItems.Length - 1);
            item = panelItems[selectedTemplateIndex];
            return item != null;
        }

        private static string NormalizeTypeId(string rawTypeId)
        {
            return string.IsNullOrWhiteSpace(rawTypeId)
                ? string.Empty
                : rawTypeId.Trim().ToLowerInvariant();
        }

        private static string ResolveDisplayName(string rawDisplayName, string normalizedTypeId)
        {
            if (!string.IsNullOrWhiteSpace(rawDisplayName))
            {
                return rawDisplayName.Trim();
            }

            if (!string.IsNullOrWhiteSpace(normalizedTypeId))
            {
                return normalizedTypeId;
            }

            return "(unnamed template)";
        }

        private BackendApiClient CreateApiClient()
        {
            return new BackendApiClient(ExperimentManager.SharedBackendOptions);
        }

        private async UniTask<FetchBackendTemplatesResult> FetchBackendTemplatesAsync()
        {
            var raw = await CreateApiClient().Get("/api/v1/crowd-types", null);

            ApiResponseMeta meta;
            string error;
            if (!TryEnsureApiSuccess(raw, "GET /api/v1/crowd-types", out meta, out error))
            {
                return FetchBackendTemplatesResult.BuildFailure(error);
            }

            var response = JsonUtility.FromJson<CrowdTypesListResponseDto>(raw.Body);
            var templates = response != null && response.data != null
                ? response.data.GetTemplateList()
                : null;

            if (templates == null || templates.Length <= 0)
            {
                return FetchBackendTemplatesResult.BuildSuccess(Array.Empty<CrowdTypeTemplateDto>());
            }

            var filtered = new List<CrowdTypeTemplateDto>(templates.Length);
            var seenTypeIds = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (var i = 0; i < templates.Length; i++)
            {
                var template = templates[i];
                if (template == null)
                {
                    continue;
                }

                var normalizedTypeId = NormalizeTypeId(template.type_id);
                if (string.IsNullOrWhiteSpace(normalizedTypeId) || seenTypeIds.Contains(normalizedTypeId))
                {
                    continue;
                }

                seenTypeIds.Add(normalizedTypeId);
                filtered.Add(CloneTemplate(template));
            }

            return FetchBackendTemplatesResult.BuildSuccess(filtered.ToArray());
        }

        private static bool TryEnsureApiSuccess(
            BackendApiRawResponse raw,
            string endpoint,
            out ApiResponseMeta meta,
            out string error)
        {
            meta = TryParseMeta(raw != null ? raw.Body : null);

            if (raw == null)
            {
                error = endpoint + " failed: no response.";
                return false;
            }

            if (!raw.IsSuccess)
            {
                if (meta != null)
                {
                    error = endpoint + " failed: status=" + raw.StatusCode + ", api_code=" + meta.code + ", message=" + meta.message;
                    return false;
                }

                error = endpoint + " failed: status=" + raw.StatusCode + ", transport=" + raw.TransportSuccess + ", error=" + raw.Error;
                return false;
            }

            if (meta == null)
            {
                error = endpoint + " failed: cannot parse response envelope.";
                return false;
            }

            if (meta.code != 0)
            {
                error = endpoint + " failed: api_code=" + meta.code + ", message=" + meta.message;
                return false;
            }

            error = string.Empty;
            return true;
        }

        private static ApiResponseMeta TryParseMeta(string body)
        {
            if (string.IsNullOrWhiteSpace(body))
            {
                return null;
            }

            try
            {
                return JsonUtility.FromJson<ApiResponseMeta>(body);
            }
            catch
            {
                return null;
            }
        }

        private void SetStatus(string message, bool isError)
        {
            if (statusTmpText != null)
            {
                statusTmpText.text = string.IsNullOrWhiteSpace(message) ? string.Empty : message;
            }

            if (isError)
            {
                Debug.LogWarning("[CrowdTypeTemplateManagementPanel] " + message);
                return;
            }

            Debug.Log("[CrowdTypeTemplateManagementPanel] " + message);
        }
    }
}
