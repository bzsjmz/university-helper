"""Run only against the deployment's dedicated disposable test database."""
import os

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from app.core import credential_crypto
from app.db.session import get_db_session
from app.services.course.chaoxing.managed_accounts import ManagedAccounts

pytestmark = pytest.mark.skipif(os.getenv('MULTI_ACCOUNT_DB_TEST') != '1', reason='Requires isolated test database')


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv('CREDENTIAL_ENCRYPTION_KEY', Fernet.generate_key().decode())
    credential_crypto._reset_for_tests()
    instance = ManagedAccounts()
    instance.ensure_schema()
    with get_db_session() as conn: conn.cursor().execute('DELETE FROM learning_accounts')
    yield instance
    credential_crypto._reset_for_tests()


def test_credentials_are_encrypted_and_public_response_is_redacted(store):
    saved = store.save('owner-a', '13800000000', 'dummy-secret', 'Account A')
    assert saved['account_label'] == '138****0000'
    assert 'password' not in str(saved) and 'dummy-secret' not in str(saved)
    row, username, password = store.credentials('owner-a', saved['id'])
    assert username == '13800000000' and password == 'dummy-secret'
    assert row['username_ciphertext'].startswith('fernet:')
    assert row['password_ciphertext'].startswith('fernet:')
    assert '13800000000' not in row['username_ciphertext']


def test_owner_cannot_read_edit_or_delete_another_account(store):
    saved = store.save('owner-a', '13800000000', 'dummy-secret', 'Account A')
    assert store.list('owner-b') == []
    for operation in [lambda: store.credentials('owner-b', saved['id']),
                      lambda: store.update('owner-b', saved['id'], 'other', ''),
                      lambda: store.delete('owner-b', saved['id'])]:
        with pytest.raises(HTTPException) as error: operation()
        assert error.value.status_code == 404


def test_account_survives_service_recreation_and_can_be_updated_deleted(store):
    saved = store.save('owner-a', '13800000000', 'dummy-secret', 'Account A')
    recreated = ManagedAccounts()
    assert recreated.list('owner-a')[0]['id'] == saved['id']
    recreated.update('owner-a', saved['id'], 'new-dummy-secret', 'renamed')
    row, _, password = recreated.credentials('owner-a', saved['id'])
    assert password == 'new-dummy-secret' and row['label'] == 'renamed'
    recreated.delete('owner-a', saved['id'])
    assert recreated.list('owner-a') == []


def test_duplicate_account_is_rejected_without_overwriting_password(store):
    saved = store.save('owner-a', '13800000000', 'dummy-secret', 'Account A')
    with pytest.raises(HTTPException) as error: store.save('owner-a', '13800000000', 'changed', 'B')
    assert error.value.status_code == 409
    assert store.credentials('owner-a', saved['id'])[2] == 'dummy-secret'
