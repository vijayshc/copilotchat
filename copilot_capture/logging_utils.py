"""Shared runtime logging helpers for the Copilot proxy."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


DEFAULT_RUNTIME_LOG_FILE = "copilot-runtime.log"
_ROOT_LOGGER_FLAG = "_copilot_runtime_logging_configured"
_ROOT_LOGGER_PATH = "_copilot_runtime_log_path"


def runtime_log_dir() -> Path:
    configured_dir = (os.environ.get("COPILOT_LOG_DIR") or "").strip()
    if configured_dir:
        return Path(configured_dir).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "logs"


def runtime_log_path(filename: str = DEFAULT_RUNTIME_LOG_FILE) -> Path:
    path = runtime_log_dir() / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def configure_runtime_logging(
    *,
    level: int = logging.INFO,
    filename: str = DEFAULT_RUNTIME_LOG_FILE,
) -> Path:
    root = logging.getLogger()
    existing_path = getattr(root, _ROOT_LOGGER_PATH, None)
    if getattr(root, _ROOT_LOGGER_FLAG, False) and existing_path:
        return Path(existing_path)

    log_path = runtime_log_path(filename)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")

    stream_handler_exists = any(
        isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
        for handler in root.handlers
    )
    if not stream_handler_exists:
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(level)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    file_handler_exists = any(
        isinstance(handler, RotatingFileHandler)
        and Path(getattr(handler, "baseFilename", "")).resolve() == log_path
        for handler in root.handlers
    )
    if not file_handler_exists:
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    root.setLevel(level)
    setattr(root, _ROOT_LOGGER_FLAG, True)
    setattr(root, _ROOT_LOGGER_PATH, str(log_path))
    return log_path