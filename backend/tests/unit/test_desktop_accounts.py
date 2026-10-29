"""Exercise real SQLite, without platform requests or a PostgreSQL service."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from app.core import credential_crypto
from app.services.course.chaoxing import managed_accounts as module


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv('CREDENTIAL_ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setattr(credential_crypto, '_cipher', None)
    monkeypatch.setattr(module.settings, 'STORAGE_BACKEND', 'sqlite')
    monkeypatch.setattr(module.settings, 'SQLITE_PATH', str(tmp_path / 'local.db'))
    monkeypatch.setattr(module, 'get_db_session', lambda: pytest.fail('Desktop opened PostgreSQL'))
    return module.ManagedAccounts()


def test_local_accounts_encrypt_persist_update_and_delete_without_history_loss(accounts):
    first = accounts.save('local', '13800000000', 'fixture-password', '我的账号')
    assert 'password' not in str(first)
    assert accounts.credentials('local', first['id'])[1:] == ('13800000000', 'fixture-password')
    assert 'fixture-password' not in accounts.get('local', first['id'])['password_ciphertext']
    reopened = module.ManagedAccounts()
    assert reopened.list('local') == [first]
    reopened.update('local', first['id'], 'new-password', '修改名称')
    assert reopened.credentials('local', first['id'])[1:] == ('13800000000', 'new-password')
    assert reopened.list('local')[0]['label'] == '修改名称'
    # Deleting credentials must not remove persisted task records.
    from app.storage.sqlite import SqliteStorage
    storage = SqliteStorage(module.settings.SQLITE_PATH)
    assert storage.tasks.upsert_task('chaoxing', {'task_id': 'old-task', 'user_id': 'local', 'status': 'completed'})
    reopened.delete('local', first['id'])
    assert reopened.list('local') == []
    assert storage.tasks.list_tasks('chaoxing', 'local')[0]['task_id'] == 'old-task'
    storage.close()


def test_owner_isolation_and_duplicate_detection(accounts):
    saved = accounts.save('local', 'fixture-user', 'secret', '')
    with pytest.raises(HTTPException) as error:
        accounts.save('local', 'fixture-user', 'secret', '')
    assert error.value.status_code == 409
    assert accounts.list('someone-else') == []
    for action in (lambda: accounts.get('someone-else', saved['id']),
                   lambda: accounts.update('someone-else', saved['id'], 'changed', ''),
                   lambda: accounts.delete('someone-else', saved['id'])):
        with pytest.raises(HTTPException) as error:
            action()
        assert error.value.status_code == 404
    assert accounts.credentials('local', saved['id'])[2] == 'secret'


def test_concurrent_local_accounts_have_independent_connections(accounts):
    with ThreadPoolExecutor(max_workers=4) as workers:
        rows = list(workers.map(lambda number: accounts.save('local', f'fixture-{number}', 'secret', ''), range(8)))
    assert len({row['id'] for row in rows}) == len(accounts.list('local')) == 8
