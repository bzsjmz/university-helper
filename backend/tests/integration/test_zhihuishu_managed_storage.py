"""Run only against a disposable database provisioned by the local test script."""
import json
import os
from urllib.parse import quote

from cryptography.fernet import Fernet
import pytest

from app.core import credential_crypto
from app.services.course.zhihuishu.adapter import ZhihuishuAdapter
from app.services.course.zhihuishu.managed import ACCOUNT_KIND, QUEUE_KIND, ManagedError, ZhihuishuManager
from app.storage.factory import get_storage

pytestmark = pytest.mark.skipif(os.environ.get('ZHIHUISHU_DB_TEST') != '1', reason='isolated PostgreSQL fixture required')


@pytest.fixture
def manager(monkeypatch):
    monkeypatch.setenv('CREDENTIAL_ENCRYPTION_KEY', Fernet.generate_key().decode())
    credential_crypto._reset_for_tests()
    result = ZhihuishuManager(store=get_storage().tasks)
    yield result
    result.shutdown()
    credential_crypto._reset_for_tests()


def save_account(manager, identity):
    adapter = ZhihuishuAdapter()
    cookie = quote(json.dumps({'uuid': identity}))
    adapter.auth.cookies = {'CASLOGC': cookie}
    adapter.auth.session.cookies.set('CASLOGC', cookie, domain='.zhihuishu.com')
    adapter._init_services(adapter.auth.cookies)
    return manager.save_login('fixture-owner', adapter, '测试账号')


def test_postgres_encrypts_session_and_restores_in_new_manager(manager):
    row = save_account(manager, 'postgres-fixture-identity')
    raw = manager.store.get_task(ACCOUNT_KIND, row['account_id'], 'fixture-owner')
    assert 'postgres-fixture-identity' not in json.dumps(raw)
    assert raw['session_ciphertext'].startswith('fernet:')
    restarted = ZhihuishuManager(store=manager.store)
    assert restarted.adapter('fixture-owner', row['account_id']).auth.uuid == 'postgres-fixture-identity'
    assert restarted.accounts('different-owner') == []
    with pytest.raises(ManagedError):
        restarted.delete('different-owner', row['account_id'])
    restarted.delete('fixture-owner', row['account_id'])


def test_postgres_recovers_queue_without_replaying_platform_operations(manager):
    row = save_account(manager, 'postgres-queue-identity')
    payload = {'task_id': 'fixture-queue', 'user_id': 'fixture-owner', 'account_id': row['account_id'],
        'status': 'running', 'speed': 2, 'task_type': 'course',
        'courses': [{'course_id': 'course-a', 'end_chapter_id': 'chapter-2', 'status': 'running'},
                    {'course_id': 'course-b', 'end_chapter_id': None, 'status': 'waiting'}]}
    assert manager.store.upsert_task(QUEUE_KIND, payload)
    restarted = ZhihuishuManager(store=manager.store)
    restarted.recover()
    saved = restarted.list_queues('fixture-owner')[0]
    assert saved['status'] == 'interrupted'
    assert saved['courses'][0]['end_chapter_id'] == 'chapter-2'
    assert saved['courses'][1]['status'] == 'waiting'
    assert not restarted.workers
    assert restarted.list_queues('different-owner') == []
