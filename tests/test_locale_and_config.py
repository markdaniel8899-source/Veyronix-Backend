import json

from app.config import Config
from app.utils.locale import get_language_instruction, get_locale, t


def test_translation_lookup_never_returns_raw_keys():
    message = t('api.simulationNotFound', id='sim_1')
    assert message != 'api.simulationNotFound'
    assert 'sim_1' in message
    assert message.isascii()


def test_unsupported_accept_language_falls_back_to_english():
    assert t('api.requireSimulationId') == 'Please provide simulation_id'


def test_language_instruction_is_english_or_roman_urdu_only():
    instruction = get_language_instruction()
    assert instruction
    assert not any('\u4e00' <= char <= '\u9fff' for char in instruction)
    assert not any('\u3040' <= char <= '\u30ff' for char in instruction)


def test_locale_files_contain_no_chinese_values():
    from app.utils import locale as locale_module

    for name, messages in locale_module._translations.items():
        for key, value in messages.items():
            assert isinstance(value, dict), f"{name}.{key} should be a section"
            for sub_key, text in value.items():
                if isinstance(text, str):
                    assert not any(
                        '\u4e00' <= char <= '\u9fff' for char in text
                    ), f"{name}.{key}.{sub_key} contains Chinese characters"


def test_config_validation_requires_llm_key_and_bearer_token(monkeypatch):
    monkeypatch.setattr(Config, 'LLM_API_KEY', '')
    monkeypatch.setattr(Config, 'API_BEARER_TOKEN', '')
    errors = Config.validate()
    assert any('LLM_API_KEY' in error for error in errors)
    assert any('API_BEARER_TOKEN' in error for error in errors)


def test_config_resolves_absolute_paths(monkeypatch):
    from app.config import BACKEND_ROOT, _abs_path

    resolved = _abs_path('./uploads/veyronix.db', './uploads/veyronix.db')
    assert resolved.startswith(BACKEND_ROOT)
    assert resolved.endswith('veyronix.db')
    assert json.dumps(Config.CORS_ORIGINS)
