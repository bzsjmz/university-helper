"""Durable WE Learn accounts and task receipts, shared by Postgres and SQLite."""
import json
import hashlib
import hmac
import sqlite3
import threading
from contextlib import contextmanager
from uuid import uuid4

from app.config import settings
from app.core.credential_crypto import encrypt_str
from app.db.session import get_db_session
from .clients import PlatformError


def account_fingerprint(identity):
    return hmac.new(settings.SECRET_KEY.encode(), identity.encode(), hashlib.sha256).hexdigest()


class _SqliteCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, query, params=()):
        self.cursor.execute(query.replace('%s', '?'), params)
        return self

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()


class _SqliteConnection:
    def __init__(self, connection):
        self.connection = connection

    def cursor(self):
        return _SqliteCursor(self.connection.cursor())


@contextmanager
def account_session():
    if settings.STORAGE_BACKEND != 'sqlite':
        with get_db_session() as connection:
            yield connection
        return
    if not settings.SQLITE_PATH:
        raise RuntimeError('WE Learn account storage requires SQLITE_PATH')
    connection = sqlite3.connect(settings.SQLITE_PATH, timeout=5)
    connection.row_factory = lambda cur, row: dict(zip((c[0] for c in cur.description), row, strict=True))
    try:
        with connection:
            yield _SqliteConnection(connection)
    finally:
        connection.close()


class WelearnStore:
    def __init__(self):
        self.ready = False
        self.lock = threading.RLock()

    def ensure(self):
        with self.lock:
            if self.ready:
                return
            with account_session() as conn:
                cur = conn.cursor()
                cur.execute('''CREATE TABLE IF NOT EXISTS welearn_accounts (
                    id VARCHAR(64) PRIMARY KEY, owner_id VARCHAR(64) NOT NULL,
                    account_key VARCHAR(64) NOT NULL, label VARCHAR(128) NOT NULL,
                    credential_ciphertext TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(owner_id, account_key))''')
                cur.execute('''CREATE TABLE IF NOT EXISTS welearn_tasks (
                    id VARCHAR(64) PRIMARY KEY, owner_id VARCHAR(64) NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
                cur.execute('CREATE INDEX IF NOT EXISTS welearn_tasks_owner ON welearn_tasks(owner_id,updated_at)')
            self.ready = True

    def accounts(self, owner):
        self.ensure()
        with account_session() as conn:
            cur = conn.cursor()
            cur.execute('SELECT * FROM welearn_accounts WHERE owner_id=%s ORDER BY created_at,id', (str(owner),))
            return [dict(row) for row in cur.fetchall()]

    def account(self, owner, aid):
        self.ensure()
        with account_session() as conn:
            cur = conn.cursor()
            cur.execute('SELECT * FROM welearn_accounts WHERE owner_id=%s AND id=%s', (str(owner), aid))
            row = cur.fetchone()
        if not row:
            raise PlatformError('账号不存在')
        return dict(row)

    def save_account(self, owner, uid, credential, label, aid=''):
        self.ensure()
        cipher = encrypt_str(credential)
        if not cipher.startswith('fernet:'):
            raise PlatformError('保存登录需要启用凭据加密，请联系管理员')
        key = account_fingerprint('welearn:' + uid)
        if aid and self.account(owner, aid)['account_key'] != key:
            raise PlatformError('登录的不是原账号，请用原账号更新登录，或作为新账号添加')
        with account_session() as conn:
            cur = conn.cursor()
            cur.execute('''INSERT INTO welearn_accounts
                (id,owner_id,account_key,label,credential_ciphertext) VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT(owner_id,account_key) DO UPDATE SET
                credential_ciphertext=EXCLUDED.credential_ciphertext,updated_at=CURRENT_TIMESTAMP
                RETURNING id''', (aid or uuid4().hex, str(owner), key, label or 'WE Learn 账号', cipher))
            saved = cur.fetchone()['id']
        return self.account(owner, saved)

    def rename(self, owner, aid, label):
        self.account(owner, aid)
        with account_session() as conn:
            conn.cursor().execute('UPDATE welearn_accounts SET label=%s,updated_at=CURRENT_TIMESTAMP WHERE owner_id=%s AND id=%s',
                                  (label, str(owner), aid))

    def delete_account(self, owner, aid):
        self.account(owner, aid)
        with account_session() as conn:
            conn.cursor().execute('DELETE FROM welearn_accounts WHERE owner_id=%s AND id=%s', (str(owner), aid))

    def tasks(self, owner):
        self.ensure()
        with account_session() as conn:
            cur = conn.cursor()
            cur.execute('SELECT payload FROM welearn_tasks WHERE owner_id=%s ORDER BY updated_at DESC,id DESC LIMIT 2000', (str(owner),))
            return [json.loads(row['payload']) for row in cur.fetchall()]

    def task(self, owner, tid):
        self.ensure()
        with account_session() as conn:
            cur = conn.cursor()
            cur.execute('SELECT payload FROM welearn_tasks WHERE owner_id=%s AND id=%s', (str(owner), tid))
            row = cur.fetchone()
        if not row:
            raise PlatformError('任务不存在')
        return json.loads(row['payload'])

    def put_task(self, owner, task):
        self.ensure()
        with account_session() as conn:
            conn.cursor().execute('''INSERT INTO welearn_tasks (id,owner_id,payload) VALUES (%s,%s,%s)
                ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload,updated_at=CURRENT_TIMESTAMP''',
                (task['id'], str(owner), json.dumps(task, ensure_ascii=False)))

    def delete_task(self, owner, tid):
        self.task(owner, tid)
        with account_session() as conn:
            conn.cursor().execute('DELETE FROM welearn_tasks WHERE owner_id=%s AND id=%s', (str(owner), tid))
