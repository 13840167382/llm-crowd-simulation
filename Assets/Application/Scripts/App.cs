using System;
using Cysharp.Threading.Tasks;
using UnityEngine;
using CrowdSim.Core;
using CrowdSim.Data.Api;
using CrowdSim.Data.Experiments;

namespace CrowdSim
{
    public class App : MonoBehaviour
    {
        private static App instance;

        [Header("Backend")]
        [SerializeField] private BackendApiClientOptions backendOptions = new BackendApiClientOptions();

        [Header("M1 Flow")]
        [SerializeField] private M1FlowOptions m1FlowOptions = new M1FlowOptions();
        [SerializeField] private bool runM1FlowOnStart;
        [SerializeField] private bool allowKeyboardTrigger = true;
        [SerializeField] private KeyCode runFlowKey = KeyCode.F5;
        [SerializeField] private string lastRunSummary = "Not started";

        [Header("FPS")]
        [SerializeField] private TMPro.TMP_Text fpsText;
        [SerializeField, Min(0.1f)] private float fpsUpdateInterval = 0.5f;

        private bool _isRunning;

        private float _fpsAccumulator;
        private int _fpsFrameCount;
        private float _fpsNextUpdateTime;

        private void Awake()
        {
            if (instance != null && instance != this)
            {
                Destroy(gameObject);
                return;
            }

            instance = this;
            DontDestroyOnLoad(gameObject);
            Application.runInBackground = true;
        }

        private void Start()
        {
            if (runM1FlowOnStart)
            {
                RunM1MinimalFlowAsync().Forget();
            }
        }

        private void Update()
        {
            UpdateFpsCounter();

            if (allowKeyboardTrigger && Input.GetKeyDown(runFlowKey))
            {
                RunM1MinimalFlow();
            }
        }

        private void UpdateFpsCounter()
        {
            if (fpsText == null)
            {
                return;
            }

            _fpsAccumulator += Time.unscaledDeltaTime;
            _fpsFrameCount++;

            var now = Time.unscaledTime;
            if (now < _fpsNextUpdateTime)
            {
                return;
            }

            _fpsNextUpdateTime = now + Mathf.Max(0.1f, fpsUpdateInterval);

            var fps = _fpsFrameCount / Mathf.Max(0.0001f, _fpsAccumulator);
            fpsText.text = fps.ToString("F0") + " FPS";

            _fpsAccumulator = 0f;
            _fpsFrameCount = 0;
        }

        [ContextMenu("Run M1 Minimal Flow")]
        public void RunM1MinimalFlow()
        {
            if (_isRunning)
            {
                return;
            }

            RunM1MinimalFlowAsync().Forget();
        }

        private async UniTask RunM1MinimalFlowAsync()
        {
            _isRunning = true;
            lastRunSummary = "Running M1 minimal backend flow...";

            try
            {
                var apiClient = new BackendApiClient(backendOptions);
                var flowService = new M1MinimalFlowService(apiClient, m1FlowOptions);
                var result = await flowService.RunAsync();

                if (result == null)
                {
                    lastRunSummary = "M1 flow finished without a result.";
                    Debug.LogError(lastRunSummary);
                    return;
                }

                lastRunSummary = result.Message;
                if (result.Success)
                {
                    Debug.Log($"M1 flow succeeded. experiment_id={result.ExperimentId}, crowd_type={result.SelectedCrowdTypeId}");
                }
                else
                {
                    Debug.LogError($"M1 flow failed: {result.Message}");
                }
            }
            catch (Exception exception)
            {
                lastRunSummary = "M1 flow exception: " + exception.Message;
                Debug.LogException(exception);
            }
            finally
            {
                _isRunning = false;
            }
        }
    }
}
