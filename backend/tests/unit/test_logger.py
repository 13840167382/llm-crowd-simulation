from __future__ import annotations

import time

from backend.app.infrastructure.observability import logger as logger_module


def test_safe_rotating_file_handler_can_skip_locked_rollover(monkeypatch, tmp_path) -> None:
    temp_dir = tmp_path
    handler = logger_module.SafeRotatingFileHandler(
        temp_dir / "locked.log",
        maxBytes=1,
        backupCount=1,
        encoding="utf-8",
    )

    def raise_locked_rollover(self) -> None:
        raise PermissionError(32, "另一个程序正在使用此文件，进程无法访问。")

    monkeypatch.setattr(logger_module.RotatingFileHandler, "doRollover", raise_locked_rollover)

    previous_stream = handler.stream
    handler.doRollover()

    assert handler._rollover_suppressed_until > time.monotonic()
    assert handler.stream is not None
    assert handler.stream is not previous_stream

    handler.close()
