"""Local administrator credentials and revocable, server-side sessions."""
import hashlib
import hmac
import secrets
import sqlite3
import time
from pathlib import Path


class AuthStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS admin (username TEXT PRIMARY KEY, password TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (key TEXT PRIMARY KEY, count INTEGER NOT NULL, until REAL NOT NULL);
            """)
        path.chmod(0o600)
        self.dummy = self.hash_password(secrets.token_urlsafe(24))

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    @staticmethod
    def hash_password(password):
        if not isinstance(password, str) or not 12 <= len(password) <= 256:
            raise ValueError("密码长度需要为 12–256 个字符")
        salt = secrets.token_hex(16)
        result = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000)
        return f"pbkdf2_sha256$600000${salt}${result.hex()}"

    @staticmethod
    def verify(password, encoded):
        if not isinstance(password, str) or len(password) > 256:
            return False
        _, rounds, salt, expected = encoded.split("$")
        result = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(result.hex(), expected)

    def initialize(self, username, password):
        if not isinstance(username, str) or not 1 <= len(username) <= 64:
            raise ValueError("账号长度需要为 1–64 个字符")
        encoded = self.hash_password(password)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM admin").fetchone():
                raise ValueError("管理员已创建，不能重复初始化")
            db.execute("INSERT INTO admin VALUES (?, ?)", (username, encoded))

    def username(self):
        with self.connect() as db:
            row = db.execute("SELECT username FROM admin").fetchone()
            return row[0] if row else None

    def login(self, username, password, client):
        now = time.time()
        # One administrator: a shared limit also prevents bypass by rotating client IPs.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM attempts WHERE until < ?", (now,))
            row = db.execute("SELECT count FROM attempts WHERE key='login'").fetchone()
            if row and row[0] >= 10:
                return None, "rate"
            row = db.execute("SELECT username, password FROM admin").fetchone()
            valid = self.verify(password, row[1] if row else self.dummy)
            if not row or not valid or not hmac.compare_digest(str(username).encode(), row[0].encode()):
                db.execute("INSERT INTO attempts VALUES ('login', 1, ?) ON CONFLICT(key) DO UPDATE SET count=count+1", (now + 300,))
                return None, "invalid"
            db.execute("DELETE FROM attempts")
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            db.execute("DELETE FROM sessions WHERE expires < ?", (now,))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (self.digest(token), csrf, now + 12 * 3600))
            return (token, csrf), None

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def session(self, token):
        with self.connect() as db:
            row = db.execute("SELECT csrf FROM sessions WHERE digest=? AND expires>?", (self.digest(token), time.time())).fetchone()
            return row[0] if row else None

    def logout(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE digest=?", (self.digest(token),))

    def change_password(self, old, new):
        encoded = self.hash_password(new)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT password FROM admin").fetchone()
            if not row or not self.verify(old, row[0]):
                raise ValueError("当前密码不正确")
            db.execute("UPDATE admin SET password=?", (encoded,))
            db.execute("DELETE FROM sessions")
