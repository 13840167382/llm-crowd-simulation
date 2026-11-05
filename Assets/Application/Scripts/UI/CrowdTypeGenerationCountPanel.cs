using System;
using System.Collections.Generic;
using System.Globalization;
using Cysharp.Threading.Tasks;
using CrowdSim.Core;
using CrowdSim.Data.Allocation;
using CrowdSim.Data.Api;
using CrowdSim.Data.Common;
using CrowdSim.Data.CrowdTypes;
using CrowdSim.Data.UI;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace CrowdSim.UI
{
    public sealed class CrowdTypeGenerationCountPanel : MonoBehaviour
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
                    Error = string.IsNullOrWhiteSpace(error) ? "拉取 crowd template 失败。" : error,
                    Templates = Array.Empty<CrowdTypeTemplateDto>(),
                };
            }
        }

        private struct CrowdTypeCountItem
        {
            public string TypeId;
            public string DisplayName;
        }

        [Header("Dependencies")]
        [SerializeField] private ExperimentManager experimentManager;

        [Header("Local Config")]
        [SerializeField] private CrowdTypeTemplateConfig crowdTypeTemplateConfig;

        [Header("Count Input List")]
        [SerializeField] private CrowdTypeCountInputBinding[] countInputBindings = Array.Empty<CrowdTypeCountInputBinding>();
        [SerializeField, Min(0)] private int defaultCountPerTemplate;

        [Header("Actions")]
        [SerializeField] private Button refreshButton;
        [SerializeField] private Button applyAndReportButton;
        [SerializeField] private TMP_Text applyAndReportButtonLabelTmpText;

        [Header("Server Report")]
        [SerializeField] private bool reportToServerAfterApply = true;
        [SerializeField] private bool generateSpawnPositionsAfterConfigure = true;
        [SerializeField] private string explicitExperimentId = string.Empty;

        [Header("Status")]
        [SerializeField] private TMP_Text statusTmpText;
        [SerializeField] private bool refreshOnStart = true;

        [Header("Runtime")]
        [SerializeField] private bool isBusy;
        [SerializeField] private string lastStatus = "Not started.";

        private CrowdTypeCountItem[] countItems = Array.Empty<CrowdTypeCountItem>();

        private void Awake()
        {
            TryResolveManager();
            BindActionButtons();
            ApplyActionStates();
        }

        private void Start()
        {
            if (refreshOnStart)
            {
                RefreshTemplateCounts();
            }
        }

        private void OnDestroy()
        {
            UnbindActionButtons();
        }

        public void RefreshTemplateCounts()
        {
            if (isBusy)
            {
                return;
            }

            RefreshTemplateCountsAsync().Forget();
        }

        public void OnClickApplyAndReportCounts()
        {
            if (isBusy)
            {
                return;
            }

            ApplyAndReportCountsAsync().Forget();
        }

        private async UniTaskVoid RefreshTemplateCountsAsync()
        {
            isBusy = true;
            ApplyActionStates();

            try
            {
                var localEntries = LoadLocalConfigEntries();
                var fetchResult = await FetchBackendTemplatesAsync();

                var sourceTemplates = fetchResult.Success
                    ? fetchResult.Templates
                    : Array.Empty<CrowdTypeTemplateDto>();

                countItems = BuildCountItems(sourceTemplates, localEntries);
                ApplyCountInputView();

                if (!fetchResult.Success)
                {
                    if (countItems.Length > 0)
                    {
                        SetStatus("后端模板拉取失败，已回退为本地模板列表。" + fetchResult.Error, true);
                    }
                    else
                    {
                        SetStatus(fetchResult.Error, true);
                    }

                    return;
                }

                SetStatus("模板数量配置已刷新：后端 " + fetchResult.Templates.Length + " 条，本地 " + localEntries.Length + " 条。", false);
            }
            finally
            {
                isBusy = false;
                ApplyActionStates();
            }
        }

        private async UniTaskVoid ApplyAndReportCountsAsync()
        {
            if (!TryResolveManager())
            {
                SetStatus("未找到 ExperimentManager。", true);
                return;
            }

            if (countItems == null || countItems.Length <= 0)
            {
                SetStatus("没有可配置的 crowd template，请先刷新模板列表。", true);
                return;
            }

            if (countInputBindings == null || countInputBindings.Length < countItems.Length)
            {
                SetStatus("输入行数量不足，无法完整上报所有模板。请增加输入行后重试。", true);
                return;
            }

            CrowdTypeAllocationConfig[] allocations;
            string parseError;
            if (!TryBuildAllocationsFromInput(out allocations, out parseError))
            {
                SetStatus(parseError, true);
                return;
            }

            isBusy = true;
            ApplyActionStates();

            try
            {
                experimentManager.ApplyCrowdTypeAllocations(allocations);

                if (!reportToServerAfterApply)
                {
                    SetStatus("已应用本地生成数量配置，共 " + allocations.Length + " 个模板。", false);
                    return;
                }

                var experimentId = ResolveExperimentIdForReport();
                if (string.IsNullOrWhiteSpace(experimentId))
                {
                    SetStatus("已应用本地配置，但未找到 experiment id。后续执行 Configure/Start 流程时会携带这些数量。", true);
                    return;
                }

                var configureResult = await experimentManager.ConfigureExperimentAsync(experimentId);
                if (configureResult == null || !configureResult.Success)
                {
                    var message = configureResult != null
                        ? configureResult.Message
                        : "上报生成数量失败：Configure 返回为空。";
                    SetStatus("上报生成数量失败：" + message, true);
                    return;
                }

                if (!generateSpawnPositionsAfterConfigure)
                {
                    SetStatus("已上报生成数量到服务器配置步骤。experiment_id=" + experimentId, false);
                    return;
                }

                var spawnResult = await experimentManager.GenerateSpawnPositionsAsync(experimentId);
                if (spawnResult == null || !spawnResult.Success)
                {
                    var message = spawnResult != null
                        ? spawnResult.Message
                        : "生成步骤返回为空。";
                    SetStatus("已上报数量但生成人群失败：" + message, true);
                    return;
                }

                SetStatus("已上报并执行生成人群。experiment_id=" + experimentId, false);
            }
            finally
            {
                isBusy = false;
                ApplyActionStates();
            }
        }

        private void ApplyCountInputView()
        {
            var countMap = BuildCurrentCountMapFromManager();
            var visibleCount = Math.Min(
                countItems != null ? countItems.Length : 0,
                countInputBindings != null ? countInputBindings.Length : 0);

            if (countInputBindings == null || countInputBindings.Length <= 0)
            {
                return;
            }

            for (var i = 0; i < countInputBindings.Length; i++)
            {
                var binding = countInputBindings[i];
                if (binding == null || binding.CountInputField == null)
                {
                    continue;
                }

                var isActive = i < visibleCount;
                binding.SetActive(isActive);
                binding.SetInteractable(!isBusy);
                if (!isActive)
                {
                    continue;
                }

                var item = countItems[i];
                binding.SetTypeName(BuildTypeDisplay(item));

                int count;
                if (!countMap.TryGetValue(item.TypeId, out count))
                {
                    count = Math.Max(0, defaultCountPerTemplate);
                }

                binding.SetCount(count);
            }

            if (countItems.Length > countInputBindings.Length)
            {
                Debug.LogWarning("CrowdTypeGenerationCountPanel: template count exceeds input rows. Please add more input rows.");
            }
        }

        private bool TryBuildAllocationsFromInput(out CrowdTypeAllocationConfig[] allocations, out string error)
        {
            allocations = Array.Empty<CrowdTypeAllocationConfig>();
            error = string.Empty;

            if (countItems == null || countItems.Length <= 0)
            {
                error = "没有可提交的模板数量配置。";
                return false;
            }

            var list = new List<CrowdTypeAllocationConfig>(countItems.Length);
            for (var i = 0; i < countItems.Length; i++)
            {
                var binding = countInputBindings[i];
                if (binding == null || binding.CountInputField == null)
                {
                    error = "模板 " + countItems[i].TypeId + " 缺少输入框绑定。";
                    return false;
                }

                int count;
                if (!TryParseCount(binding.GetRawInput(), out count))
                {
                    error = "模板 " + countItems[i].TypeId + " 的数量格式无效，请输入非负整数。";
                    return false;
                }

                if (count <= 0)
                {
                    continue;
                }

                list.Add(new CrowdTypeAllocationConfig
                {
                    typeId = countItems[i].TypeId,
                    count = count,
                });
            }

            if (list.Count <= 0)
            {
                error = "至少需要一个模板的生成数量大于 0。";
                return false;
            }

            allocations = list.ToArray();
            return true;
        }

        private Dictionary<string, int> BuildCurrentCountMapFromManager()
        {
            var map = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            if (!TryResolveManager())
            {
                return map;
            }

            var allocations = experimentManager.GetCrowdTypeAllocationsSnapshot();
            for (var i = 0; i < allocations.Length; i++)
            {
                var allocation = allocations[i];
                if (allocation == null)
                {
                    continue;
                }

                string normalizedTypeId;
                int normalizedCount;
                if (!allocation.TryGetNormalized(out normalizedTypeId, out normalizedCount))
                {
                    continue;
                }

                map[normalizedTypeId] = normalizedCount;
            }

            return map;
        }

        private CrowdTypeCountItem[] BuildCountItems(
            CrowdTypeTemplateDto[] backendTemplates,
            CrowdTypeTemplateConfigEntry[] localEntries)
        {
            var items = new List<CrowdTypeCountItem>();
            var typeIdSet = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            if (backendTemplates != null)
            {
                for (var i = 0; i < backendTemplates.Length; i++)
                {
                    var template = backendTemplates[i];
                    if (template == null)
                    {
                        continue;
                    }

                    var typeId = NormalizeTypeId(template.type_id);
                    if (string.IsNullOrWhiteSpace(typeId) || typeIdSet.Contains(typeId))
                    {
                        continue;
                    }

                    typeIdSet.Add(typeId);
                    items.Add(new CrowdTypeCountItem
                    {
                        TypeId = typeId,
                        DisplayName = ResolveDisplayName(template.display_name, typeId),
                    });
                }
            }

            if (localEntries != null)
            {
                for (var i = 0; i < localEntries.Length; i++)
                {
                    var entry = localEntries[i];
                    if (entry == null)
                    {
                        continue;
                    }

                    CrowdTypeCreateRequestDto createRequest;
                    string typeId;
                    string buildError;
                    if (!entry.TryBuildCreateRequest(out createRequest, out typeId, out buildError)
                        || string.IsNullOrWhiteSpace(typeId)
                        || typeIdSet.Contains(typeId))
                    {
                        continue;
                    }

                    typeIdSet.Add(typeId);
                    items.Add(new CrowdTypeCountItem
                    {
                        TypeId = typeId,
                        DisplayName = ResolveDisplayName(createRequest != null ? createRequest.display_name : string.Empty, typeId),
                    });
                }
            }

            return items.ToArray();
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

        private string ResolveExperimentIdForReport()
        {
            if (!string.IsNullOrWhiteSpace(explicitExperimentId))
            {
                return explicitExperimentId.Trim();
            }

            if (!TryResolveManager())
            {
                return string.Empty;
            }

            if (!string.IsNullOrWhiteSpace(experimentManager.RunningExperimentId))
            {
                return experimentManager.RunningExperimentId;
            }

            return experimentManager.LastCreatedExperimentId;
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
                filtered.Add(template);
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

        private void BindActionButtons()
        {
            if (refreshButton != null)
            {
                refreshButton.onClick.RemoveListener(RefreshTemplateCounts);
                refreshButton.onClick.AddListener(RefreshTemplateCounts);
            }

            if (applyAndReportButton != null)
            {
                applyAndReportButton.onClick.RemoveListener(OnClickApplyAndReportCounts);
                applyAndReportButton.onClick.AddListener(OnClickApplyAndReportCounts);
            }
        }

        private void UnbindActionButtons()
        {
            if (refreshButton != null)
            {
                refreshButton.onClick.RemoveListener(RefreshTemplateCounts);
            }

            if (applyAndReportButton != null)
            {
                applyAndReportButton.onClick.RemoveListener(OnClickApplyAndReportCounts);
            }
        }

        private void ApplyActionStates()
        {
            if (refreshButton != null)
            {
                refreshButton.interactable = !isBusy;
            }

            if (applyAndReportButton != null)
            {
                applyAndReportButton.interactable = !isBusy;
            }

            if (applyAndReportButtonLabelTmpText != null)
            {
                applyAndReportButtonLabelTmpText.text = BuildApplyButtonLabel();
            }

            if (countInputBindings == null || countInputBindings.Length <= 0)
            {
                return;
            }

            for (var i = 0; i < countInputBindings.Length; i++)
            {
                var binding = countInputBindings[i];
                if (binding == null)
                {
                    continue;
                }

                binding.SetInteractable(!isBusy);
            }
        }

        private string BuildApplyButtonLabel()
        {
            if (isBusy)
            {
                return "处理中...";
            }

            if (!reportToServerAfterApply)
            {
                return "应用数量";
            }

            return generateSpawnPositionsAfterConfigure
                ? "上报并生成人群"
                : "上报数量";
        }

        private static string BuildTypeDisplay(CrowdTypeCountItem item)
        {
            if (string.IsNullOrWhiteSpace(item.DisplayName)
                || string.Equals(item.DisplayName, item.TypeId, StringComparison.OrdinalIgnoreCase))
            {
                return item.TypeId;
            }

            return item.DisplayName + " (" + item.TypeId + ")";
        }

        private static bool TryParseCount(string rawText, out int count)
        {
            if (string.IsNullOrWhiteSpace(rawText))
            {
                count = 0;
                return true;
            }

            if (!int.TryParse(rawText, NumberStyles.Integer, CultureInfo.InvariantCulture, out count)
                && !int.TryParse(rawText, out count))
            {
                count = 0;
                return false;
            }

            if (count < 0)
            {
                count = 0;
                return false;
            }

            return true;
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

        private void SetStatus(string message, bool isError)
        {
            lastStatus = string.IsNullOrWhiteSpace(message) ? string.Empty : message;

            if (statusTmpText != null)
            {
                statusTmpText.text = lastStatus;
            }

            if (string.IsNullOrWhiteSpace(lastStatus))
            {
                return;
            }

            if (isError)
            {
                Debug.LogWarning("[CrowdTypeGenerationCountPanel] " + lastStatus);
                return;
            }

            Debug.Log("[CrowdTypeGenerationCountPanel] " + lastStatus);
        }
    }
}
