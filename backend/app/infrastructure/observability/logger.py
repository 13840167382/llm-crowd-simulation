"""日志工具。"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from backend.app.core.context import get_experiment_id, get_request_id

LOG_FORMAT = (
    "%(asctime)s %(levelname)s [%(name)s] "
    "[req=%(request_id)s exp=%(experiment_id)s] %(message)s"
)
CONSOLE_HANDLER_NAME = "crowd_flow_console"
FILE_HANDLER_NAME = "crowd_flow_file"
ROLLOVER_LOCK_CONFLICT_SUPPRESS_SECONDS = 30.0


class SafeRotatingFileHandler(RotatingFileHandler):
    """在 Windows 多进程共享日志文件时，遇到轮转锁冲突就临时跳过。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._rollover_suppressed_until = 0.0

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if self._rollover_suppressed_until > time.monotonic():
            return False
        return super().shouldRollover(record)

    def doRollover(self) -> None:
        try:
            super().doRollover()
            self._rollover_suppressed_until = 0.0
        except PermissionError as exc:
            self._handle_rollover_lock_conflict(exc)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 32:
                self._handle_rollover_lock_conflict(exc)
                return
            raise

    def _handle_rollover_lock_conflict(self, exc: BaseException) -> None:
        self._rollover_suppressed_until = time.monotonic() + ROLLOVER_LOCK_CONFLICT_SUPPRESS_SECONDS
        try:
            if self.stream:
                self.stream.flush()
                self.stream.close()
        except OSError:
            pass
        self.stream = self._open()
        sys.stderr.write(
            "日志轮转遇到文件占用，已暂时跳过本次轮转并继续写入当前文件。"
            f" detail={exc}\n"
        )
        sys.stderr.flush()


class RequestContextFilter(logging.Filter):
    """把请求上下文里的 request_id / experiment_id 注入日志记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        record.experiment_id = get_experiment_id() or "-"
        return True


def configure_logging(
    level: str = "info",
    *,
    log_to_file: bool = True,
    log_dir: str = "logs",
    log_filename: str = "backend.log",
    log_max_bytes: int = 10 * 1024 * 1024,
    log_backup_count: int = 7,
) -> None:
    """初始化全局日志格式，并按配置启用控制台与文件日志。"""

    root_logger = logging.getLogger()
    formatter = logging.Formatter(LOG_FORMAT)
    root_logger.setLevel(level.upper())

    _ensure_console_handler(root_logger, formatter)
    if log_to_file:
        _ensure_file_handler(
            root_logger,
            formatter,
            log_dir=log_dir,
            log_filename=log_filename,
            log_max_bytes=log_max_bytes,
            log_backup_count=log_backup_count,
        )

    _ensure_context_filter(root_logger)
    _configure_noisy_third_party_loggers()


def get_logger(name: str) -> logging.Logger:
    """获取约定好上下文字段的日志对象。"""

    return logging.getLogger(name)


def _ensure_context_filter(root_logger: logging.Logger) -> None:
    """确保根日志处理器都带上请求上下文过滤器。"""

    for handler in root_logger.handlers:
        if any(isinstance(existing_filter, RequestContextFilter) for existing_filter in handler.filters):
            continue
        handler.addFilter(RequestContextFilter())


def _ensure_console_handler(root_logger: logging.Logger, formatter: logging.Formatter) -> None:
    """确保至少存在一个控制台日志处理器，并统一日志格式。"""

    if not root_logger.handlers:
        console_handler = logging.StreamHandler()
        console_handler.set_name(CONSOLE_HANDLER_NAME)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
        return

    for handler in root_logger.handlers:
        if isinstance(handler, RotatingFileHandler):
            continue
        handler.setFormatter(formatter)
        if not handler.get_name():
            handler.set_name(CONSOLE_HANDLER_NAME)


def _ensure_file_handler(
    root_logger: logging.Logger,
    formatter: logging.Formatter,
    *,
    log_dir: str,
    log_filename: str,
    log_max_bytes: int,
    log_backup_count: int,
) -> None:
    """确保文件日志处理器存在，并写入本地滚动日志文件。"""

    log_path = _resolve_log_path(log_dir, log_filename)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    for handler in root_logger.handlers:
        if not isinstance(handler, RotatingFileHandler):
            continue
        handler_path = Path(getattr(handler, "baseFilename", ""))
        if handler_path == log_path:
            handler.setFormatter(formatter)
            handler.maxBytes = log_max_bytes
            handler.backupCount = log_backup_count
            if not handler.get_name():
                handler.set_name(FILE_HANDLER_NAME)
            return

    file_handler = SafeRotatingFileHandler(
        log_path,
        maxBytes=log_max_bytes,
        backupCount=log_backup_count,
        encoding="utf-8",
    )
    file_handler.set_name(FILE_HANDLER_NAME)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)


def _resolve_log_path(log_dir: str, log_filename: str) -> Path:
    """把日志目录解析成后端项目内的实际文件路径。"""

    backend_root = Path(__file__).resolve().parents[3]
    directory = Path(log_dir)
    if not directory.is_absolute():
        directory = backend_root / directory
    return directory / _build_dated_log_filename(log_filename)


def _build_dated_log_filename(log_filename: str) -> str:
    """在日志文件名中注入当天日期。

    示例：
    - backend.log -> backend-2026-04-13.log
    - backend.local.log -> backend.local-2026-04-13.log
    """

    filename_path = Path(log_filename)
    suffixes = "".join(filename_path.suffixes)
    stem = filename_path.name[: -len(suffixes)] if suffixes else filename_path.name
    current_date = datetime.now().strftime("%Y-%m-%d")
    if suffixes:
        return f"{stem}-{current_date}{suffixes}"
    return f"{stem}-{current_date}"


def _configure_noisy_third_party_loggers() -> None:
    """压低高频第三方日志，避免调试时被 watchfiles 等噪音刷屏。"""

    for logger_name in (
        "watchfiles",
        "watchfiles.main",
        "watchfiles.watcher",
    ):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
