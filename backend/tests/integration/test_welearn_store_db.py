"""Run only against the explicitly isolated disposable Postgres test database."""
import json
import os

import pytest
from cryptography.fernet import Fernet

pytestmark = pytest.mark.skipif(os.getenv('WELEARN_DB_TEST') != '1', reason='requires isolated test database')


def test_postgres_saved_login_and_history_survive_store_recreation(monkeypatch):
    from app.config import settings
    from app.core import credential_crypto
    from app.services.course.english.welearn_store import WelearnStore
    from app.services.course.english.clients import PlatformError
    assert settings.MAIN_DB_HOST == 'testdb'
    monkeypatch.setenv('CREDENTIAL_ENCRYPTION_KEY', Fernet.generate_key().decode())
    credential_crypto._reset_for_tests()
    try:
        store = WelearnStore()
        account = store.save_account('owner-a', 'uid-fixture', 'sid=isolated-fixture', 'Saved')
        same = WelearnStore().save_account('owner-a', 'uid-fixture', 'sid=updated', 'Ignore duplicate name')
        assert same['id'] == account['id'] and same['label'] == 'Saved'
        assert same['credential_ciphertext'].startswith('fernet:')
        assert credential_crypto.decrypt_str(same['credential_ciphertext']) == 'sid=updated'
        other = store.save_account('owner-b', 'uid-fixture', 'sid=other', 'Other owner')
        assert other['id'] != account['id'] and other['account_key'] == account['account_key']
        store.rename('owner-a', account['id'], 'Renamed')
        assert store.account('owner-a', account['id'])['label'] == 'Renamed'
        with pytest.raises(PlatformError): store.account('owner-b', account['id'])
        task = {'id': 'fixture-task', 'status': 'running', '_config': credential_crypto.encrypt_str(json.dumps({'selections': ['exact']}))}
        store.put_task('owner-a', task)
        task['status'] = 'interrupted'; store.put_task('owner-a', task)
        assert WelearnStore().task('owner-a', task['id']) == task
        assert store.tasks('owner-b') == []
        with pytest.raises(PlatformError): store.delete_task('owner-b', task['id'])
        store.delete_account('owner-a', account['id'])
        assert store.accounts('owner-a') == []
        assert len(store.tasks('owner-a')) == 1
        store.delete_task('owner-a', task['id'])
        assert store.tasks('owner-a') == []
    finally:
        credential_crypto._reset_for_tests()
