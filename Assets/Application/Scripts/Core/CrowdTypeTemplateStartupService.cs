using System;
using System.Collections.Generic;
using Cysharp.Threading.Tasks;
using CrowdSim.Data.Api;
using CrowdSim.Data.Common;
using CrowdSim.Data.CrowdTypes;
using UnityEngine;
using UnityEngine.Networking;

namespace CrowdSim.Core
{
    public sealed class CrowdTypeTemplateSyncResult
    {
        public bool Success;
        public string Message = string.Empty;
        public CrowdTypeTemplateDto[] Templates = Array.Empty<CrowdTypeTemplateDto>();
        public int CreatedCount;
        public int UpdatedCount;
        public int DeletedCount;
        public int UnchangedCount;
        public int BuiltinReadonlySkippedCount;
        public int InvalidConfigSkippedCount;
    }

    public sealed class CrowdTypeTemplateStartupService
    {
        private struct FetchTemplatesResult
        {
            public bool Success;
            public CrowdTypeTemplateDto[] Templates;
            public string Error;

            public static FetchTemplatesResult Create(bool success, CrowdTypeTemplateDto[] templates, string error)
            {
                return new FetchTemplatesResult
                {
                    Success = success,
                    Templates = templates ?? Array.Empty<CrowdTypeTemplateDto>(),
                    Error = string.IsNullOrWhiteSpace(error) ? string.Empty : error,
                };
            }
        }

        private readonly BackendApiClient apiClient;

        public CrowdTypeTemplateStartupService(BackendApiClient apiClient)
        {
            this.apiClient = apiClient;
        }

        public async UniTask<CrowdTypeTemplateSyncResult> FetchAndSyncAsync(CrowdTypeTemplateConfig config)
        {
            var result = new CrowdTypeTemplateSyncResult();

            var fetchResult = await TryFetchTemplatesAsync();
            if (!fetchResult.Success)
            {
                return BuildFailure(result, fetchResult.Error);
            }

            var fetchedTemplates = fetchResult.Templates ?? Array.Empty<CrowdTypeTemplateDto>();
            result.Templates = fetchedTemplates;

            if (config == null || !config.SyncBeforeCreate)
            {
                result.Success = true;
                result.Message = "Crowd template step: fetched " + fetchedTemplates.Length
                    + " template(s); local crowd config sync skipped.";
                return result;
            }

            var remoteByTypeId = BuildTemplateMap(fetchedTemplates);
            var desiredByTypeId = new Dictionary<string, CrowdTypeTemplateConfigEntry>(StringComparer.OrdinalIgnoreCase);
            var configEntries = config.CloneTemplates();

            for (var i = 0; i < configEntries.Length; i++)
            {
                var entry = configEntries[i];
                if (entry == null)
                {
                    result.InvalidConfigSkippedCount++;
                    continue;
                }

                CrowdTypeCreateRequestDto createRequest;
                string normalizedTypeId;
                string entryError;
                if (!entry.TryBuildCreateRequest(out createRequest, out normalizedTypeId, out entryError))
                {
                    result.InvalidConfigSkippedCount++;
                    continue;
                }

                if (desiredByTypeId.ContainsKey(normalizedTypeId))
                {
                    result.InvalidConfigSkippedCount++;
                    continue;
                }

                desiredByTypeId.Add(normalizedTypeId, entry);

                CrowdTypeTemplateDto remoteTemplate;
                if (remoteByTypeId.TryGetValue(normalizedTypeId, out remoteTemplate))
                {
                    if (remoteTemplate != null && remoteTemplate.is_builtin)
                    {
                        result.BuiltinReadonlySkippedCount++;
                        continue;
                    }

                    if (entry.IsSemanticallyEqualTo(remoteTemplate))
                    {
                        result.UnchangedCount++;
                        continue;
                    }

                    var updateRequest = entry.BuildUpdateRequest();
                    var updateBody = JsonUtility.ToJson(updateRequest);
                    var updatePath = "/api/v1/crowd-types/" + EscapeTypeId(normalizedTypeId);
                    var updateRaw = await apiClient.PutJson(updatePath, updateBody, null);

                    ApiResponseMeta updateMeta;
                    string updateError;
                    if (!TryEnsureApiSuccess(updateRaw, "PUT /api/v1/crowd-types/{type_id}", out updateMeta, out updateError))
                    {
                        return BuildFailure(result, updateError);
                    }

                    result.UpdatedCount++;
                    continue;
                }

                var createBody = JsonUtility.ToJson(createRequest);
                var createRaw = await apiClient.PostJson("/api/v1/crowd-types", createBody, null);

                ApiResponseMeta createMeta;
                string createError;
                if (!TryEnsureApiSuccess(createRaw, "POST /api/v1/crowd-types", out createMeta, out createError))
                {
                    return BuildFailure(result, createError);
                }

                result.CreatedCount++;
            }

            var deleteMissingCustomTemplates = config.DeleteRemoteCustomTemplatesNotInConfig
                && result.InvalidConfigSkippedCount <= 0;

            if (deleteMissingCustomTemplates)
            {
                var remoteTypeIds = new List<string>(remoteByTypeId.Keys);
                for (var i = 0; i < remoteTypeIds.Count; i++)
                {
                    var typeId = remoteTypeIds[i];
                    CrowdTypeTemplateDto remoteTemplate;
                    if (!remoteByTypeId.TryGetValue(typeId, out remoteTemplate)
                        || remoteTemplate == null)
                    {
                        continue;
                    }

                    if (remoteTemplate.is_builtin)
                    {
                        continue;
                    }

                    if (desiredByTypeId.ContainsKey(typeId))
                    {
                        continue;
                    }

                    var deletePath = "/api/v1/crowd-types/" + EscapeTypeId(typeId);
                    var deleteRaw = await apiClient.Delete(deletePath, null);

                    ApiResponseMeta deleteMeta;
                    string deleteError;
                    if (!TryEnsureApiSuccess(deleteRaw, "DELETE /api/v1/crowd-types/{type_id}", out deleteMeta, out deleteError))
                    {
                        return BuildFailure(result, deleteError);
                    }

                    result.DeletedCount++;
                }
            }

            if (result.CreatedCount > 0 || result.UpdatedCount > 0 || result.DeletedCount > 0)
            {
                var refreshResult = await TryFetchTemplatesAsync();
                if (!refreshResult.Success)
                {
                    return BuildFailure(result, "Crowd template sync finished but refresh failed: " + refreshResult.Error);
                }

                result.Templates = refreshResult.Templates;
            }

            result.Success = true;
            result.Message = BuildSummaryMessage(result, deleteMissingCustomTemplates);
            return result;
        }

        private static CrowdTypeTemplateSyncResult BuildFailure(CrowdTypeTemplateSyncResult result, string error)
        {
            result.Success = false;
            result.Message = string.IsNullOrWhiteSpace(error)
                ? "Crowd template step failed."
                : error;
            return result;
        }

        private async UniTask<FetchTemplatesResult> TryFetchTemplatesAsync()
        {
            var raw = await apiClient.Get("/api/v1/crowd-types", null);

            ApiResponseMeta meta;
            string error;
            if (!TryEnsureApiSuccess(raw, "GET /api/v1/crowd-types", out meta, out error))
            {
                return FetchTemplatesResult.Create(false, Array.Empty<CrowdTypeTemplateDto>(), error);
            }

            var response = JsonUtility.FromJson<CrowdTypesListResponseDto>(raw.Body);
            var source = response != null && response.data != null
                ? response.data.GetTemplateList()
                : null;

            if (source == null || source.Length <= 0)
            {
                return FetchTemplatesResult.Create(true, Array.Empty<CrowdTypeTemplateDto>(), string.Empty);
            }

            var filtered = new List<CrowdTypeTemplateDto>(source.Length);
            for (var i = 0; i < source.Length; i++)
            {
                var item = source[i];
                if (item == null || string.IsNullOrWhiteSpace(item.type_id))
                {
                    continue;
                }

                filtered.Add(item);
            }

            return FetchTemplatesResult.Create(true, filtered.ToArray(), string.Empty);
        }

        private static Dictionary<string, CrowdTypeTemplateDto> BuildTemplateMap(CrowdTypeTemplateDto[] templates)
        {
            var map = new Dictionary<string, CrowdTypeTemplateDto>(StringComparer.OrdinalIgnoreCase);
            if (templates == null || templates.Length <= 0)
            {
                return map;
            }

            for (var i = 0; i < templates.Length; i++)
            {
                var template = templates[i];
                if (template == null || string.IsNullOrWhiteSpace(template.type_id))
                {
                    continue;
                }

                var normalizedTypeId = template.type_id.Trim();
                if (map.ContainsKey(normalizedTypeId))
                {
                    continue;
                }

                map.Add(normalizedTypeId, template);
            }

            return map;
        }

        private static string BuildSummaryMessage(CrowdTypeTemplateSyncResult result, bool deleteEnabled)
        {
            return "Crowd template step finished. fetched=" + (result.Templates != null ? result.Templates.Length : 0)
                + ", created=" + result.CreatedCount
                + ", updated=" + result.UpdatedCount
                + ", deleted=" + result.DeletedCount
                + ", unchanged=" + result.UnchangedCount
                + ", builtin_skipped=" + result.BuiltinReadonlySkippedCount
                + ", invalid_skipped=" + result.InvalidConfigSkippedCount
                + ", delete_missing_custom=" + deleteEnabled;
        }

        private static string EscapeTypeId(string typeId)
        {
            return UnityWebRequest.EscapeURL(typeId ?? string.Empty);
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
    }
}
