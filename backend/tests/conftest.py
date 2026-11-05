"""测试运行时的公共初始化。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import MethodType
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

# 统一把项目根目录加入 sys.path，避免整包 pytest 收集时出现 backend 包导入不稳定。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
project_root_str = str(PROJECT_ROOT)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

from backend.tests.support import build_scoped_backend_settings, cleanup_scoped_redis_artifacts


def create_api_client(app) -> TestClient:
    """创建会为 `/api/v1` 请求自动补唯一 `X-Request-Id` 的测试客户端。"""

    client = TestClient(app)
    original_request = client.request

    def request_with_auto_request_id(self: TestClient, method: str, url: str, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        if isinstance(url, str) and url.startswith("/api/v1") and "X-Request-Id" not in headers:
            headers["X-Request-Id"] = f"test-{uuid4().hex[:8]}"
        kwargs["headers"] = headers
        return original_request(method, url, **kwargs)

    client.request = MethodType(request_with_auto_request_id, client)
    return client


@pytest.fixture
def scoped_backend_settings(monkeypatch):
    """为 integration / e2e 测试生成隔离的 Redis / RQ 命名空间。"""

    settings = build_scoped_backend_settings(monkeypatch)
    try:
        yield settings
    finally:
        cleanup_scoped_redis_artifacts(queue_name=settings.maintenance_queue_name)
