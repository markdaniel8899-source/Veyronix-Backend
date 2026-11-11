"""
Unified logging configuration for Veyronix.

Console output plus a rotating log file at ``Config.LOG_FILE``
(default: ``backend/logs/veyronix.log``).
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from ..config import Config


def _ensure_utf8_stdout() -> None:
    """Make Windows consoles render UTF-8 instead of mojibake."""
    if sys.platform == 'win32':
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')


def _resolve_level(level) -> int:
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        return getattr(logging, level.upper(), logging.INFO)
    return getattr(logging, str(Config.LOG_LEVEL or 'INFO').upper(), logging.INFO)


def setup_logger(name: str = 'Veyronix', level=None) -> logging.Logger:
    """Configure a logger with console and rotating-file handlers."""
    resolved_level = _resolve_level(level)
    log_file = Config.LOG_FILE
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(resolved_level)
    logger.propagate = False

    if logger.handlers:
        return logger

    detailed_formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    simple_formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%H:%M:%S',
    )

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8',
    )
    file_handler.setLevel(resolved_level)
    file_handler.setFormatter(detailed_formatter)

    _ensure_utf8_stdout()
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(max(resolved_level, logging.INFO))
    console_handler.setFormatter(simple_formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def get_logger(name: str = 'Veyronix') -> logging.Logger:
    """Return a configured logger, creating handlers on first use."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        return setup_logger(name)
    return logger


# Default application logger
logger = setup_logger()


def debug(msg: str, *args, **kwargs) -> None:
    logger.debug(msg, *args, **kwargs)


def info(msg: str, *args, **kwargs) -> None:
    logger.info(msg, *args, **kwargs)


def warning(msg: str, *args, **kwargs) -> None:
    logger.warning(msg, *args, **kwargs)


def error(msg: str, *args, **kwargs) -> None:
    logger.error(msg, *args, **kwargs)


def critical(msg: str, *args, **kwargs) -> None:
    logger.critical(msg, *args, **kwargs)
