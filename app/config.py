"""
Configuration management for Veyronix backend.

Loads settings from ``backend/.env`` (preferred) or the project root ``.env``.
All filesystem paths are resolved to absolute paths so the server behaves the
same when started from any working directory (gunicorn, systemd, CLI).
"""

import os

from dotenv import load_dotenv

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_ROOT)

_backend_env = os.path.join(BACKEND_ROOT, '.env')
_project_env = os.path.join(PROJECT_ROOT, '.env')

if os.path.exists(_backend_env):
    load_dotenv(_backend_env, override=False)
elif os.path.exists(_project_env):
    load_dotenv(_project_env, override=False)
else:
    load_dotenv(override=False)


def _abs_path(value: str, default: str) -> str:
    """Resolve a possibly-relative path against the backend root."""
    raw = (value or default or '').strip()
    if not raw:
        raw = default
    return os.path.normpath(
        raw if os.path.isabs(raw) else os.path.join(BACKEND_ROOT, raw)
    )


class Config:
    """Flask configuration for Veyronix backend."""

    # Flask configuration
    SECRET_KEY = os.environ.get('SECRET_KEY', 'veyronix-change-me-in-production')
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    JSON_AS_ASCII = False

    # ===== NVIDIA NIM — Primary LLM Provider =====
    # NOTE: NVIDIA NIM model ids must include the publisher prefix, e.g.
    # "nvidia/nemotron-3.5-super-vl-120b-a12b". The bare id returns HTTP 404.
    LLM_API_KEY = (os.environ.get('LLM_API_KEY') or '').strip()
    LLM_BASE_URL = os.environ.get(
        'LLM_BASE_URL', 'https://integrate.api.nvidia.com/v1'
    ).strip()
    LLM_MODEL_NAME = os.environ.get(
        'LLM_MODEL_NAME', 'nvidia/nemotron-3.5-super-vl-120b-a12b'
    ).strip()

    # LLM Router (both tiers use Nemotron 3.5 Super VL 120B A12B)
    LLM_ROUTER_SMALL = os.environ.get(
        'LLM_ROUTER_SMALL', 'nvidia/nemotron-3.5-super-vl-120b-a12b'
    ).strip()
    LLM_ROUTER_LARGE = os.environ.get(
        'LLM_ROUTER_LARGE', 'nvidia/nemotron-3.5-super-vl-120b-a12b'
    ).strip()

    # Rate limiting for the NVIDIA NIM free tier (typically 10 RPM)
    LLM_RPM = int(os.environ.get('LLM_RPM', '10'))
    LLM_MAX_CONCURRENT = int(os.environ.get('LLM_MAX_CONCURRENT', '3'))

    # Delay between agent chunks inside one simulation round (seconds)
    SIMULATION_CHUNK_WAIT_SECONDS = float(
        os.environ.get('SIMULATION_CHUNK_WAIT_SECONDS', '6')
    )

    # File upload configuration
    MAX_CONTENT_LENGTH = int(
        os.environ.get('MAX_CONTENT_LENGTH_MB', '50')
    ) * 1024 * 1024
    UPLOAD_FOLDER = _abs_path(
        os.environ.get('STORAGE_PATH', './uploads'), './uploads'
    )
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}

    # Text processing configuration
    DEFAULT_CHUNK_SIZE = int(os.environ.get('DEFAULT_CHUNK_SIZE', '500'))
    DEFAULT_CHUNK_OVERLAP = int(os.environ.get('DEFAULT_CHUNK_OVERLAP', '50'))

    # OASIS simulation configuration
    OASIS_DEFAULT_MAX_ROUNDS = int(
        os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '5')
    )
    OASIS_SIMULATION_DATA_DIR = os.path.join(UPLOAD_FOLDER, 'simulations')

    # OASIS platform available actions
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING',
        'QUOTE_POST',
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE',
    ]

    # Report Agent configuration
    REPORT_AGENT_MAX_TOOL_CALLS = int(
        os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5')
    )
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(
        os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2')
    )
    REPORT_AGENT_TEMPERATURE = float(
        os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5')
    )

    # Local SQLite storage (replaces Zep Cloud)
    STORAGE_TYPE = os.environ.get('STORAGE_TYPE', 'local')
    STORAGE_PATH = UPLOAD_FOLDER
    DATABASE_PATH = _abs_path(
        os.environ.get('DATABASE_PATH', './uploads/veyronix.db'),
        './uploads/veyronix.db',
    )

    # Memory consolidation interval (every N rounds)
    MEMORY_CONSOLIDATION_INTERVAL = int(
        os.environ.get('MEMORY_CONSOLIDATION_INTERVAL', '5')
    )

    # Security
    API_BEARER_TOKEN = (os.environ.get('API_BEARER_TOKEN') or '').strip()
    CORS_ORIGINS = os.environ.get('CORS_ORIGINS', '*')

    # Logging
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
    LOG_FILE = _abs_path(
        os.environ.get('LOG_FILE', './logs/veyronix.log'),
        './logs/veyronix.log',
    )

    @classmethod
    def validate(cls) -> list[str]:
        """Validate required configuration."""
        errors: list[str] = []
        if not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY is required")
        if not cls.API_BEARER_TOKEN:
            errors.append("API_BEARER_TOKEN is required")
        if cls.LLM_RPM <= 0:
            errors.append("LLM_RPM must be greater than zero")
        if cls.LLM_MAX_CONCURRENT <= 0:
            errors.append("LLM_MAX_CONCURRENT must be greater than zero")
        if cls.DEBUG:
            import warnings
            warnings.warn(
                "Flask DEBUG mode is enabled. Do not use in production.",
                RuntimeWarning,
            )
        return errors
