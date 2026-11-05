"""环境变量文件加载器。"""

from __future__ import annotations

import os
from pathlib import Path

_LOADED = False


def get_backend_root() -> Path:
    """获取后端项目根目录。"""

    return Path(__file__).resolve().parents[2]


def _parse_env_line(line: str) -> tuple[str, str] | None:
    """解析单行环境变量。"""

    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None

    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip()

    if not key:
        return None

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]

    return key, value


def _load_env_file(path: Path) -> None:
    """加载单个环境变量文件，但不覆盖已有系统环境变量。"""

    if not path.exists() or not path.is_file():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        os.environ.setdefault(key, value)


def load_environment_files() -> str:
    """按当前环境加载通用与环境专属配置文件。"""

    global _LOADED
    if _LOADED:
        return os.getenv("APP_ENV", "local").strip().lower() or "local"

    backend_root = get_backend_root()
    _load_env_file(backend_root / ".env")

    app_env = os.getenv("APP_ENV", "local").strip().lower() or "local"
    _load_env_file(backend_root / f".env.{app_env}")

    _LOADED = True
    return app_env
