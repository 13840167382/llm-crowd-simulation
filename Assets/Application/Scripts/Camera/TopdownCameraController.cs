using UnityEngine;

namespace CrowdSim.CameraControl
{
    [AddComponentMenu("CrowdSim/Camera/Topdown Camera Controller")]
    public sealed class TopdownCameraController : MonoBehaviour
    {
        [Header("Move")]
        [SerializeField, Min(0f)] private float moveSpeed = 10f;
        [SerializeField] private bool useUnscaledDeltaTime;

        [Header("Zoom")]
        [SerializeField] private bool enableWheelZoom = true;
        [SerializeField, Min(0f)] private float zoomSpeed = 10f;
        [SerializeField, Min(0.01f)] private float minOrthographicSize = 5f;
        [SerializeField, Min(0.01f)] private float maxOrthographicSize = 30f;

        [Header("Bounds")]
        [SerializeField] private bool clampToBounds;
        [SerializeField] private Vector2 worldBoundsMin = new Vector2(-50f, -50f);
        [SerializeField] private Vector2 worldBoundsMax = new Vector2(50f, 50f);

        private Camera cachedCamera;

        private void Awake()
        {
            cachedCamera = GetComponent<Camera>();
        }

        private void Update()
        {
            var deltaTime = useUnscaledDeltaTime ? Time.unscaledDeltaTime : Time.deltaTime;

            var horizontal = 0f;
            var vertical = 0f;

            if (Input.GetKey(KeyCode.A))
            {
                horizontal -= 1f;
            }

            if (Input.GetKey(KeyCode.D))
            {
                horizontal += 1f;
            }

            if (Input.GetKey(KeyCode.S))
            {
                vertical -= 1f;
            }

            if (Input.GetKey(KeyCode.W))
            {
                vertical += 1f;
            }

            if (Mathf.Approximately(horizontal, 0f) && Mathf.Approximately(vertical, 0f))
            {
                if (enableWheelZoom)
                {
                    HandleWheelZoom(deltaTime);

                    if (clampToBounds)
                    {
                        ClampPositionToBounds();
                    }
                }

                return;
            }

            var forward = Vector3.ProjectOnPlane(transform.forward, Vector3.up);
            if (forward.sqrMagnitude <= 0.0001f)
            {
                forward = Vector3.forward;
            }
            forward.Normalize();

            var right = Vector3.ProjectOnPlane(transform.right, Vector3.up);
            if (right.sqrMagnitude <= 0.0001f)
            {
                right = Vector3.right;
            }
            right.Normalize();

            var movement = (right * horizontal + forward * vertical).normalized;
            transform.position += movement * moveSpeed * deltaTime;

            if (enableWheelZoom)
            {
                HandleWheelZoom(deltaTime);
            }

            if (clampToBounds)
            {
                ClampPositionToBounds();
            }
        }

        private void HandleWheelZoom(float deltaTime)
        {
            var scrollDelta = Input.mouseScrollDelta.y;
            if (Mathf.Approximately(scrollDelta, 0f))
            {
                return;
            }

            if (cachedCamera != null && cachedCamera.orthographic)
            {
                cachedCamera.orthographicSize = Mathf.Clamp(
                    cachedCamera.orthographicSize - scrollDelta * zoomSpeed * deltaTime,
                    minOrthographicSize,
                    maxOrthographicSize);
                return;
            }

            var zoomDirection = Vector3.ProjectOnPlane(transform.forward, Vector3.up);
            if (zoomDirection.sqrMagnitude <= 0.0001f)
            {
                zoomDirection = transform.forward;
            }

            if (zoomDirection.sqrMagnitude <= 0.0001f)
            {
                return;
            }

            zoomDirection.Normalize();
            transform.position += zoomDirection * (scrollDelta * zoomSpeed * deltaTime);
        }

        private void ClampPositionToBounds()
        {
            var minX = Mathf.Min(worldBoundsMin.x, worldBoundsMax.x);
            var maxX = Mathf.Max(worldBoundsMin.x, worldBoundsMax.x);
            var minZ = Mathf.Min(worldBoundsMin.y, worldBoundsMax.y);
            var maxZ = Mathf.Max(worldBoundsMin.y, worldBoundsMax.y);

            var position = transform.position;
            position.x = Mathf.Clamp(position.x, minX, maxX);
            position.z = Mathf.Clamp(position.z, minZ, maxZ);
            transform.position = position;
        }
    }
}
