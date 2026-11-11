import json
import os
import threading
from flask import request, has_request_context

_thread_local = threading.local()

DEFAULT_LOCALE = 'en'

_locales_dir = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'locales')
)

# Load language registry
with open(os.path.join(_locales_dir, 'languages.json'), 'r', encoding='utf-8') as f:
    _languages = json.load(f)

# Load translation files
_translations = {}
for filename in os.listdir(_locales_dir):
    if filename.endswith('.json') and filename != 'languages.json':
        locale_name = filename[:-5]
        with open(os.path.join(_locales_dir, filename), 'r', encoding='utf-8') as f:
            _translations[locale_name] = json.load(f)

# Never route translations to a locale file that does not exist.
if DEFAULT_LOCALE not in _translations:
    DEFAULT_LOCALE = next(iter(_translations), 'en')


def set_locale(locale: str):
    """Set locale for current thread. Call at the start of background threads."""
    _thread_local.locale = locale if locale in _translations else DEFAULT_LOCALE


def _match_locale(raw: str) -> str:
    """Resolve an Accept-Language style value to a loaded locale."""
    for part in str(raw or '').split(','):
        code = part.split(';')[0].strip().lower()
        if not code:
            continue
        if code in _translations:
            return code
        base = code.split('-')[0]
        if base in _translations:
            return base
    return DEFAULT_LOCALE


def get_locale() -> str:
    if has_request_context():
        return _match_locale(request.headers.get('Accept-Language', DEFAULT_LOCALE))
    return getattr(_thread_local, 'locale', DEFAULT_LOCALE) or DEFAULT_LOCALE


def _lookup(messages: dict, key: str):
    value = messages
    for part in key.split('.'):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def t(key: str, **kwargs) -> str:
    locale = get_locale()
    value = _lookup(_translations.get(locale, {}), key)
    if value is None:
        value = _lookup(_translations.get(DEFAULT_LOCALE, {}), key)
    if value is None:
        return key

    if kwargs:
        for k, v in kwargs.items():
            value = value.replace(f'{{{k}}}', str(v))

    return value


def get_language_instruction() -> str:
    locale = get_locale()
    lang_config = _languages.get(locale) or _languages.get(DEFAULT_LOCALE) or {}
    return lang_config.get(
        'llmInstruction',
        'Respond in English or Roman Urdu only. Never use Chinese, Japanese, '
        'Korean, Arabic, or Cyrillic scripts.',
    )
