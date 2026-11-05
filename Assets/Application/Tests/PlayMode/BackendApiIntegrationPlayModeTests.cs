using System;
using System.Collections;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.TestTools;

public static class BackendApiTestConfig
{
    public static readonly string BaseUrl = ReadString("UNITY_BACKEND_BASE_URL", "http://127.0.0.1:8000");
    public static readonly int TimeoutSeconds = ReadInt("UNITY_BACKEND_TIMEOUT_SECONDS", 10);

    private static string ReadString(string key, string fallback)
    {
        var value = Environment.GetEnvironmentVariable(key);
        return string.IsNullOrWhiteSpace(value) ? fallback : value.Trim();
    }

    private static int ReadInt(string key, int fallback)
    {
        var value = Environment.GetEnvironmentVariable(key);
        if (int.TryParse(value, out var parsed) && parsed > 0)
        {
            return parsed;
        }

        return fallback;
    }
}

public class BackendApiIntegrationPlayModeTests
{
    [Serializable]
    private class HealthzResponse
    {
        public int code;
        public HealthzData data;
    }

    [Serializable]
    private class HealthzData
    {
        public string status;
        public string environment;
    }

    [Serializable]
    private class CrowdTypesResponse
    {
        public int code;
        public CrowdTypesData data;
    }

    [Serializable]
    private class CrowdTypesData
    {
        public CrowdTypeItem[] templates;
        public CrowdTypeItem[] items;
        public int total_count;
    }

    [Serializable]
    private class CrowdTypeItem
    {
        public string type_id;
        public string code;
        public string display_name;
    }

    [UnityTest]
    public IEnumerator Healthz_ReturnsOkPayload()
    {
        yield return SendGet("/healthz", request =>
        {
            AssertSuccess(request, "/healthz");

            var payload = JsonUtility.FromJson<HealthzResponse>(request.downloadHandler.text);
            Assert.NotNull(payload, "Healthz response JSON parse failed.");
            Assert.AreEqual(0, payload.code, "Healthz response code should be 0.");
            Assert.NotNull(payload.data, "Healthz response data is null.");
            Assert.AreEqual("ok", payload.data.status, "Healthz status should be ok.");
        });
    }

    [UnityTest]
    public IEnumerator HealthzDb_ReturnsConnectionInfo()
    {
        yield return SendGet("/healthz/db", request =>
        {
            AssertSuccess(request, "/healthz/db");

            var body = NormalizeJson(request.downloadHandler.text).ToLowerInvariant();
            StringAssert.Contains("connected", body);
            StringAssert.Contains("timescaledb", body);
        });
    }

    [UnityTest]
    public IEnumerator CrowdTypes_ReturnsBuiltinItems()
    {
        yield return SendGet("/api/v1/crowd-types", request =>
        {
            AssertSuccess(request, "/api/v1/crowd-types");

            var payload = JsonUtility.FromJson<CrowdTypesResponse>(request.downloadHandler.text);
            Assert.NotNull(payload, "Crowd types response JSON parse failed.");
            Assert.AreEqual(0, payload.code, "Crowd types response code should be 0.");
            Assert.NotNull(payload.data, "Crowd types response data is null.");
            Assert.Greater(payload.data.total_count, 0, "Crowd types total_count should be > 0.");

            var templates = payload.data.templates ?? payload.data.items;
            Assert.NotNull(templates, "Crowd types templates should not be null.");
            Assert.Greater(templates.Length, 0, "Crowd types templates should not be empty.");

            var foundTourist = false;
            var foundShopper = false;
            for (var i = 0; i < templates.Length; i++)
            {
                var typeKey = string.IsNullOrWhiteSpace(templates[i].type_id)
                    ? templates[i].code
                    : templates[i].type_id;

                if (string.Equals(typeKey, "tourist", StringComparison.OrdinalIgnoreCase))
                {
                    foundTourist = true;
                }

                if (string.Equals(typeKey, "shopper", StringComparison.OrdinalIgnoreCase))
                {
                    foundShopper = true;
                }
            }

            Assert.IsTrue(foundTourist, "Crowd types should include tourist.");
            Assert.IsTrue(foundShopper, "Crowd types should include shopper.");
        });
    }

    private static IEnumerator SendGet(string endpoint, Action<UnityWebRequest> assertOnResponse)
    {
        var url = BuildUrl(endpoint);
        using (var request = UnityWebRequest.Get(url))
        {
            request.timeout = BackendApiTestConfig.TimeoutSeconds;
            request.SetRequestHeader("X-Request-Id", "unity-playmode-test-" + Guid.NewGuid().ToString("N"));
            yield return request.SendWebRequest();
            assertOnResponse(request);
        }
    }

    private static void AssertSuccess(UnityWebRequest request, string endpoint)
    {
        if (request.result != UnityWebRequest.Result.Success)
        {
            Assert.Fail(
                "GET " + endpoint + " failed. result=" + request.result +
                ", code=" + request.responseCode +
                ", error=" + request.error +
                ", body=" + request.downloadHandler?.text);
        }

        Assert.AreEqual(200, request.responseCode, "Unexpected HTTP status code.");
    }

    private static string BuildUrl(string endpoint)
    {
        var baseUrl = BackendApiTestConfig.BaseUrl.TrimEnd('/');
        if (string.IsNullOrEmpty(endpoint))
        {
            return baseUrl;
        }

        return endpoint.StartsWith("/") ? baseUrl + endpoint : baseUrl + "/" + endpoint;
    }

    private static string NormalizeJson(string json)
    {
        if (string.IsNullOrEmpty(json))
        {
            return string.Empty;
        }

        return json
            .Replace("\r", string.Empty)
            .Replace("\n", string.Empty)
            .Replace("\t", string.Empty)
            .Replace(" ", string.Empty);
    }
}