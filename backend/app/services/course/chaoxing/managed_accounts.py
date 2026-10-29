"""Owner-scoped, encrypted account records and independent browsing sessions."""
import hashlib
import hmac
import sqlite3
import threading
from contextlib import contextmanager
from uuid import uuid4

from fastapi import HTTPException

from app.config import settings
from app.core.credential_crypto import decrypt_str, encrypt_str
from app.db.session import get_db_session
from .signin import ChaoxingSigninClient


class _AccountCursor:
    """Parameter adapter limited to the account repository's static SQL."""
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, query, params=()):
        self.cursor.execute(query.replace('%s', '?'), params)
        return self

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()


class _AccountConnection:
    def __init__(self, connection):
        self.connection = connection

    def cursor(self):
        return _AccountCursor(self.connection.cursor())


@contextmanager
def account_session():
    if settings.STORAGE_BACKEND != 'sqlite':
        with get_db_session() as connection:
            yield connection
        return
    if not settings.SQLITE_PATH:
        raise RuntimeError('Local account storage requires SQLITE_PATH')
    connection = sqlite3.connect(settings.SQLITE_PATH, timeout=5)
    connection.row_factory = lambda cur, row: dict(zip((c[0] for c in cur.description), row, strict=True))
    try:
        with connection:
            yield _AccountConnection(connection)
    finally:
        connection.close()


def account_fingerprint(username):
    return hmac.new(settings.SECRET_KEY.encode(), username.strip().encode(), hashlib.sha256).hexdigest()


def mask_account(username):
    username = username.strip()
    if len(username) < 5:
        return username[:1] + '***'
    return username[:3] + '****' + username[-4:] if len(username) >= 10 else username[:2] + '***' + username[-2:]


class ManagedAccounts:
    def __init__(self):
        self._guard = threading.Lock()
        self._locks = {}
        self._clients = {}
        self._schema_ready = False

    def lock(self, owner, account_id):
        with self._guard:
            return self._locks.setdefault((str(owner), account_id), threading.RLock())

    def ensure_schema(self):
        with self._guard:
            if self._schema_ready:
                return
            with account_session() as conn:
                conn.cursor().execute('''CREATE TABLE IF NOT EXISTS learning_accounts (
                    id VARCHAR(64) PRIMARY KEY, owner_id VARCHAR(64) NOT NULL,
                    account_key VARCHAR(64) NOT NULL, label VARCHAR(128) NOT NULL,
                    account_label VARCHAR(128) NOT NULL,
                    username_ciphertext TEXT NOT NULL, password_ciphertext TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(owner_id, account_key))''')
            self._schema_ready = True

    def list(self, owner):
        self.ensure_schema()
        with account_session() as conn:
            cur = conn.cursor()
            cur.execute('SELECT id,label,account_label,created_at,updated_at FROM learning_accounts WHERE owner_id=%s ORDER BY created_at', (str(owner),))
            return [dict(row) for row in cur.fetchall()]

    def get(self, owner, account_id):
        self.ensure_schema()
        with account_session() as conn:
            cur = conn.cursor()
            cur.execute('SELECT * FROM learning_accounts WHERE owner_id=%s AND id=%s', (str(owner), account_id))
            row = cur.fetchone()
        if not row:
            raise HTTPException(404, 'Account not found')
        return dict(row)

    def save(self, owner, username, password, label):
        self.ensure_schema()
        encrypted_username, encrypted_password = encrypt_str(username), encrypt_str(password)
        if not encrypted_username.startswith('fernet:') or not encrypted_password.startswith('fernet:'):
            raise HTTPException(503, 'Credential encryption is required')
        with account_session() as conn:
            cur = conn.cursor()
            cur.execute('''INSERT INTO learning_accounts
                (id,owner_id,account_key,label,account_label,username_ciphertext,password_ciphertext)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(owner_id,account_key) DO NOTHING RETURNING id''',
                (uuid4().hex, str(owner), account_fingerprint(username), label or mask_account(username),
                 mask_account(username), encrypted_username, encrypted_password))
            row = cur.fetchone()
        if not row:
            raise HTTPException(409, 'This learning account is already saved')
        return self.public(self.get(owner, row['id']))

    @staticmethod
    def public(row):
        return {key: row[key] for key in ('id', 'label', 'account_label', 'created_at', 'updated_at')}

    def credentials(self, owner, account_id):
        row = self.get(owner, account_id)
        return row, decrypt_str(row['username_ciphertext']), decrypt_str(row['password_ciphertext'])

    def update(self, owner, account_id, password, label):
        row = self.get(owner, account_id)
        encrypted = encrypt_str(password) if password else row['password_ciphertext']
        if not encrypted.startswith('fernet:'):
            raise HTTPException(503, 'Credential encryption is required')
        with account_session() as conn:
            conn.cursor().execute('UPDATE learning_accounts SET password_ciphertext=%s,label=%s,updated_at=CURRENT_TIMESTAMP WHERE owner_id=%s AND id=%s',
                                  (encrypted, label or row['label'], str(owner), account_id))
        self.forget(owner, account_id)
        return self.public(self.get(owner, account_id))

    def forget(self, owner, account_id):
        client = self._clients.pop((str(owner), account_id), None)
        if client:
            client.session.close()

    def delete(self, owner, account_id):
        self.get(owner, account_id)
        with account_session() as conn:
            conn.cursor().execute('DELETE FROM learning_accounts WHERE owner_id=%s AND id=%s', (str(owner), account_id))
        self.forget(owner, account_id)

    def client(self, owner, account_id):
        # Callers hold the per-account lock through the browsing request.
        row, username, password = self.credentials(owner, account_id)
        key = (str(owner), account_id)
        client = self._clients.get(key)
        if client is None:
            client = ChaoxingSigninClient()
            try:
                result = client.login(username=username, password=password)
            except Exception:
                client.session.close()
                raise HTTPException(502, 'Account login failed; check credentials or platform verification') from None
            if not result.get('status'):
                client.session.close()
                # 401 is reserved for the management login; the frontend would
                # otherwise clear its token when just a learning login fails.
                raise HTTPException(422, 'Learning account login failed; update its password or handle verification')
            self._clients[key] = client
        return client


managed_accounts = ManagedAccounts()
