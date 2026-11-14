"""Free accounts and saved profiles, using only the Python standard library.

- Username + password only. No email, so CaddieSync holds as little personal data as possible.
- Passwords are hashed with scrypt and a random salt; only the hash is stored.
- Sessions are random tokens in an HttpOnly cookie; the database stores only a SHA-256 of each token.
- Profiles hold health-related needs, so a user can delete their account and everything with it.

Storage is SQLite at CADDIESYNC_DB (default: instance/caddiesync.db, never committed). On Render's free plan that
file is erased on redeploy; the browser keeps a copy of the profile so it can be restored by signing up again.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import ROOT
from .models import Profile

DB_PATH = Path(os.getenv("CADDIESYNC_DB", ROOT / "instance" / "caddiesync.db"))
SESSION_DAYS = 30
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


class AccountError(ValueError):
    """Message is safe to show the user."""


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with _db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE,
            salt BLOB NOT NULL, pw_hash BLOB NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS profiles (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            data TEXT NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires REAL NOT NULL);
        """)


def _hash_pw(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _check_new(username: str, password: str) -> None:
    if not USERNAME_RE.match(username):
        raise AccountError("Usernames are 3-30 letters, numbers, dots, dashes or underscores.")
    if len(password) < 8:
        raise AccountError("Use a password with at least 8 characters.")
    if len(password) > 200:
        raise AccountError("That password is too long.")


def create_user(username: str, password: str) -> int:
    _check_new(username, password)
    salt = secrets.token_bytes(16)
    try:
        with _db() as con:
            cur = con.execute("INSERT INTO users (username, salt, pw_hash, created) VALUES (?, ?, ?, ?)",
                              (username, salt, _hash_pw(password, salt), time.time()))
            return int(cur.lastrowid)
    except sqlite3.IntegrityError:
        raise AccountError("That username is taken. Try another, or sign in.") from None


def verify_user(username: str, password: str) -> int:
    with _db() as con:
        row = con.execute("SELECT id, salt, pw_hash FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        _hash_pw(password, b"0" * 16)  # same work either way, so timing doesn't reveal which usernames exist
        raise AccountError("Wrong username or password.")
    if not hmac.compare_digest(_hash_pw(password, row[1]), row[2]):
        raise AccountError("Wrong username or password.")
    return int(row[0])


def new_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with _db() as con:
        con.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
        con.execute("INSERT INTO sessions VALUES (?, ?, ?)", (_token_hash(token), user_id, time.time() + SESSION_DAYS * 86400))
    return token


def user_for_token(token: str | None) -> tuple[int, str] | None:
    if not token:
        return None
    with _db() as con:
        row = con.execute("SELECT u.id, u.username FROM sessions s JOIN users u ON u.id = s.user_id "
                          "WHERE s.token_hash = ? AND s.expires > ?", (_token_hash(token), time.time())).fetchone()
    return (int(row[0]), row[1]) if row else None


def end_session(token: str | None) -> None:
    if token:
        with _db() as con:
            con.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def get_profile(user_id: int) -> Profile | None:
    with _db() as con:
        row = con.execute("SELECT data FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
    return Profile.model_validate_json(row[0]) if row else None


def save_profile(user_id: int, profile: Profile) -> None:
    with _db() as con:
        con.execute("INSERT INTO profiles (user_id, data, updated) VALUES (?, ?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET data = excluded.data, updated = excluded.updated",
                    (user_id, json.dumps(profile.model_dump()), time.time()))


def delete_user(user_id: int) -> None:
    with _db() as con:  # profiles and sessions go with it (ON DELETE CASCADE)
        con.execute("DELETE FROM users WHERE id = ?", (user_id,))
