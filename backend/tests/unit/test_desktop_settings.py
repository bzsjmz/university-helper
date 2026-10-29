import os
import stat

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import desktop_settings
from app.api.v1 import desktop


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('UH_DESKTOP_DATA', str(tmp_path))
    for key in ('CHAOXING_AI_ENDPOINT', 'CHAOXING_AI_MODEL', 'CHAOXING_AI_PRIMARY_KEY_FILE', 'CHAOXING_AI_KEY_FILE'):
        monkeypatch.delenv(key, raising=False)
    desktop_settings.apply_settings(tmp_path)
    # No DNS or HTTP requests from configuration tests.
    monkeypatch.setattr(desktop, 'assert_public_endpoint', lambda value: value.strip())
    app = FastAPI()
    app.include_router(desktop.router)
    with TestClient(app, base_url='http://127.0.0.1:45678') as session:
        yield session


def save(client, **payload):
    return client.put('/settings', json={'endpoint': desktop_settings.DEFAULT_ENDPOINT, 'model': 'fixture-model', **payload},
                      headers={'Origin': 'http://127.0.0.1:45678'})


def test_keys_never_return_and_blank_preserves_them_across_restart(client, tmp_path):
    reply = save(client, primary_key='fixture-primary', fallback_key='fixture-fallback')
    assert reply.status_code == 200
    assert reply.json()['primary_configured'] is True
    assert 'fixture-primary' not in reply.text and 'fixture-fallback' not in reply.text
    assert save(client).json()['fallback_configured'] is True
    desktop_settings.apply_settings(tmp_path)
    assert os.environ['CHAOXING_AI_MODEL'] == 'fixture-model'
    assert (tmp_path / 'ai-primary.key').read_text() == 'fixture-primary'
    if os.name != 'nt':
        assert stat.S_IMODE((tmp_path / 'ai-primary.key').stat().st_mode) == 0o600
    assert save(client, primary_key='').json()['primary_configured'] is False
    assert client.get('/settings').json()['fallback_configured'] is True


def test_endpoint_change_clears_old_credentials(client, tmp_path):
    save(client, primary_key='fixture-primary', fallback_key='fixture-fallback')
    reply = save(client, endpoint='https://another.example/api', primary_key='new-fixture')
    assert reply.status_code == 200
    assert reply.json()['primary_configured'] is True
    assert reply.json()['fallback_configured'] is False
    assert (tmp_path / 'ai-primary.key').read_text() == 'new-fixture'


def test_cross_origin_and_missing_origin_cannot_change_keys(client):
    for headers in ({}, {'Origin': 'https://other.example'}):
        reply = client.put('/settings', json={'endpoint': desktop_settings.DEFAULT_ENDPOINT, 'model': 'model'}, headers=headers)
        assert reply.status_code == 403
    assert client.get('/settings').json()['model'] == ''


def test_unsafe_endpoint_is_rejected_without_changing_settings(client, monkeypatch):
    def unsafe(value):
        raise desktop.UnsafeEndpointError()
    monkeypatch.setattr(desktop, 'assert_public_endpoint', unsafe)
    assert save(client, endpoint='http://127.0.0.1').status_code == 422
    assert client.get('/settings').json()['model'] == ''


def test_new_ai_task_reads_local_configuration_without_changing_existing_task(client, monkeypatch):
    from app.services.course.chaoxing.answer_providers import ai as ai_module
    monkeypatch.setenv('PROFILE', 'local')
    monkeypatch.setattr(ai_module, 'assert_public_endpoint', lambda value: value)
    monkeypatch.setattr(ai_module.AI, '_build_client', lambda self: None)
    save(client, model='first-model', primary_key='first-fixture', fallback_key='fallback-fixture')
    def provider():
        value = ai_module.AI()
        value.config_set({'provider': 'AI'})
        value.init_tiku()
        return value
    previous = provider()
    assert previous.key == 'first-fixture'
    save(client, model='second-model', primary_key='second-fixture')
    current = provider()
    assert current.key == 'second-fixture' and current.model == 'second-model'
    assert previous.key == 'first-fixture' and previous.model == 'first-model'
    assert current._keys == ('second-fixture', 'fallback-fixture')
