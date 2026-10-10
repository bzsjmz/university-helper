from unittest.mock import Mock

import pytest

from app.services.course.chaoxing.answer_providers import AI
from app.services.course.chaoxing.answer_providers import ai as ai_module
from app.services.course.chaoxing import endpoint_security


@pytest.fixture
def server_config(monkeypatch, tmp_path):
    endpoint = "https://api.deepseek.com/chat/completions"
    key_file = tmp_path / "key"
    key_file.write_text("test-server-key\n")
    monkeypatch.setenv("CHAOXING_AI_ENDPOINT", endpoint)
    monkeypatch.setenv("CHAOXING_AI_MODEL", "deepseek-flash")
    monkeypatch.setenv("CHAOXING_AI_KEY_FILE", str(key_file))
    monkeypatch.setenv("CHAOXING_AI_TIMEOUT_SECONDS", "120")
    monkeypatch.setattr(ai_module, "assert_public_endpoint", lambda value: value)
    monkeypatch.setattr(AI, "_build_client", Mock())
    return endpoint, key_file


def make_provider(config):
    provider = AI()
    provider.config_set(config)
    provider.init_tiku()
    return provider


def test_web_ai_selection_uses_server_settings_without_mutating_payload(server_config):
    config = {"provider": "AI", "submit": "false"}
    provider = make_provider(config)
    assert provider.endpoint == server_config[0]
    assert provider.model == "deepseek-flash"
    assert provider.key == "test-server-key"
    assert provider.timeout == 120
    assert config == {"provider": "AI", "submit": "false"}


def test_server_key_cannot_be_sent_to_another_endpoint(server_config):
    with pytest.raises(ValueError, match="configured server endpoint"):
        make_provider({"provider": "AI", "endpoint": "https://other.example/chat/completions"})


def test_explicit_provider_credentials_remain_supported(server_config):
    provider = make_provider({"provider": "AI", "endpoint": "https://other.example/chat/completions",
                              "key": "test-caller-key", "model": "caller-model", "timeout": 45})
    assert provider.key == "test-caller-key"
    assert provider.model == "caller-model"
    assert provider.timeout == 45


def test_blank_key_is_reported_without_secret_values(server_config):
    server_config[1].write_text("")
    with pytest.raises(ValueError, match="has not been configured"):
        make_provider({"provider": "AI"})


def test_new_tasks_read_rotated_key(server_config):
    assert make_provider({"provider": "AI"}).key == "test-server-key"
    server_config[1].write_text("test-rotated-key")
    assert make_provider({"provider": "AI"}).key == "test-rotated-key"


def test_task_admission_validates_server_endpoint_without_adding_key(server_config, monkeypatch):
    validate = Mock(return_value=server_config[0])
    monkeypatch.setattr(endpoint_security, "assert_public_endpoint", validate)
    payload = {"provider": "TikuGo,AI"}
    endpoint_security.validate_tiku_config(payload)
    validate.assert_called_once_with(server_config[0])
    assert payload == {"provider": "TikuGo,AI"}


def test_server_endpoint_still_rejects_private_addresses(server_config, monkeypatch):
    monkeypatch.setenv("CHAOXING_AI_ENDPOINT", "http://127.0.0.1/chat/completions")
    with pytest.raises(endpoint_security.UnsafeEndpointError):
        endpoint_security.validate_tiku_config({"provider": "AI"})


def test_primary_key_precedes_existing_fallback_and_is_not_in_config(server_config, monkeypatch, tmp_path):
    primary = tmp_path / 'primary'
    primary.write_text('test-primary-key')
    monkeypatch.setenv('CHAOXING_AI_PRIMARY_KEY_FILE', str(primary))
    config = {'provider': 'AI'}
    provider = make_provider(config)
    assert provider._keys == ('test-primary-key', 'test-server-key')
    assert provider.key == 'test-primary-key'
    assert config == {'provider': 'AI'}
    primary.write_text('')
    assert make_provider(config)._keys == ('test-server-key',)
    primary.write_text('test-server-key')
    assert make_provider(config)._keys == ('test-server-key',)


def test_caller_key_does_not_inherit_server_fallback(server_config, monkeypatch, tmp_path):
    primary = tmp_path / 'primary'
    primary.write_text('test-primary-key')
    monkeypatch.setenv('CHAOXING_AI_PRIMARY_KEY_FILE', str(primary))
    provider = make_provider({'provider': 'AI', 'key': 'caller-key'})
    assert provider._keys == ('caller-key',)


def test_unreadable_primary_file_is_reported_without_credentials(server_config, monkeypatch, tmp_path):
    monkeypatch.setenv('CHAOXING_AI_PRIMARY_KEY_FILE', str(tmp_path / 'missing'))
    with pytest.raises(ValueError, match='cannot be read'):
        make_provider({'provider': 'AI'})
