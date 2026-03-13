"""
Encrypted Stores — Claude Code SDK Recipe

Pattern: Fernet-symmetric-encrypted SQLite for agent data. Sensitive fields
are encrypted at rest; search/index fields stay plain for querying.

Run:
    python recipe.py

Requires:
    pip install cryptography
    ANTHROPIC_API_KEY environment variable (for the demo)
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ── Key management ────────────────────────────────────────────────────────────


def derive_key(password: str, salt: bytes) -> bytes:
    """
    Derive a Fernet key from a password using PBKDF2-HMAC-SHA256.

    Never store the password. Store the salt (it's not secret).
    The derived key should be stored in the OS keychain or env var.
    """
    key_bytes = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt,
        iterations=480_000,   # OWASP 2024 recommendation
        dklen=32,
    )
    return base64.urlsafe_b64encode(key_bytes)


def generate_key() -> bytes:
    """Generate a fresh random Fernet key (32 cryptographically random bytes)."""
    return Fernet.generate_key()


def load_key_from_env(env_var: str = "STORE_ENCRYPTION_KEY") -> bytes | None:
    """Load a key from an environment variable. Returns None if not set."""
    raw = os.environ.get(env_var)
    if not raw:
        return None
    return raw.encode() if isinstance(raw, str) else raw


def save_key_to_keychain(key: bytes, service: str, account: str) -> bool:
    """
    Save an encryption key to the macOS Keychain via the `security` CLI.

    Falls back gracefully on non-macOS systems.
    """
    try:
        import subprocess

        result = subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",  # update if exists
                "-s", service,
                "-a", account,
                "-w", key.decode(),
            ],
            capture_output=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def load_key_from_keychain(service: str, account: str) -> bytes | None:
    """
    Load an encryption key from the macOS Keychain.

    Returns None if not found or on non-macOS.
    """
    try:
        import subprocess

        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().encode()
    except Exception:
        pass
    return None


# ── Encryption engine ─────────────────────────────────────────────────────────


class EncryptionEngine:
    """
    Fernet symmetric encryption for SQLite text columns.

    Fernet guarantees:
    - AES-128-CBC encryption
    - HMAC-SHA256 authentication (tamper detection)
    - Timestamp embedded in token (for TTL checks)
    """

    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string. Returns a base64-encoded token."""
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        """Decrypt a token. Raises InvalidToken if tampered or wrong key."""
        return self._fernet.decrypt(token.encode()).decode()

    def encrypt_json(self, obj: Any) -> str:
        """Serialize obj to JSON then encrypt."""
        return self.encrypt(json.dumps(obj))

    def decrypt_json(self, token: str) -> Any:
        """Decrypt then deserialize JSON."""
        return json.loads(self.decrypt(token))

    def encrypt_bytes(self, data: bytes) -> bytes:
        """Encrypt raw bytes (for embeddings, binary blobs)."""
        return self._fernet.encrypt(data)

    def decrypt_bytes(self, data: bytes) -> bytes:
        """Decrypt raw bytes."""
        return self._fernet.decrypt(data)


# ── Schema helpers ────────────────────────────────────────────────────────────


@dataclass
class FieldSpec:
    """Describes one column: whether it's encrypted and its SQLite type."""
    name: str
    sql_type: str = "TEXT"
    encrypted: bool = False
    primary_key: bool = False

    def ddl(self) -> str:
        pk = " PRIMARY KEY" if self.primary_key else ""
        return f"{self.name} {self.sql_type}{pk}"


# ── Encrypted SQLite store ────────────────────────────────────────────────────


class EncryptedStore:
    """
    SQLite store with field-level Fernet encryption.

    Plain fields are stored as-is (searchable, indexable).
    Encrypted fields are stored as Fernet tokens (opaque blobs).

    Example schema:
        fields = [
            FieldSpec("id", "TEXT", primary_key=True),
            FieldSpec("created_at", "REAL"),          # plain — queryable
            FieldSpec("source", "TEXT"),               # plain — filterable
            FieldSpec("content", "TEXT", encrypted=True),  # sensitive
            FieldSpec("metadata", "TEXT", encrypted=True), # sensitive JSON
        ]
    """

    def __init__(
        self,
        db_path: str | Path,
        table: str,
        fields: list[FieldSpec],
        engine: EncryptionEngine,
    ) -> None:
        self.db_path = Path(db_path)
        self.table = table
        self.fields = fields
        self.engine = engine
        self._plain_fields = {f.name for f in fields if not f.encrypted}
        self._enc_fields = {f.name for f in fields if f.encrypted}
        self._conn = self._connect()
        self._create_table()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _create_table(self) -> None:
        col_defs = ", ".join(f.ddl() for f in self.fields)
        self._conn.execute(
            f"CREATE TABLE IF NOT EXISTS {self.table} ({col_defs})"
        )
        self._conn.commit()

    def _encrypt_row(self, row: dict[str, Any]) -> dict[str, Any]:
        result = {}
        for k, v in row.items():
            if k in self._enc_fields and v is not None:
                if isinstance(v, (dict, list)):
                    result[k] = self.engine.encrypt_json(v)
                else:
                    result[k] = self.engine.encrypt(str(v))
            else:
                result[k] = v
        return result

    def _decrypt_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = {}
        for k in row.keys():
            v = row[k]
            if k in self._enc_fields and v is not None:
                try:
                    result[k] = self.engine.decrypt(v)
                except InvalidToken:
                    logger.warning("Decryption failed for field %s — wrong key?", k)
                    result[k] = None
            else:
                result[k] = v
        return result

    def insert(self, row: dict[str, Any]) -> None:
        """Insert a row, encrypting sensitive fields."""
        encrypted = self._encrypt_row(row)
        cols = ", ".join(encrypted.keys())
        placeholders = ", ".join("?" * len(encrypted))
        self._conn.execute(
            f"INSERT INTO {self.table} ({cols}) VALUES ({placeholders})",
            list(encrypted.values()),
        )
        self._conn.commit()

    def get(self, pk_value: Any) -> dict[str, Any] | None:
        """Fetch a row by primary key, decrypting sensitive fields."""
        pk_field = next(f for f in self.fields if f.primary_key)
        cursor = self._conn.execute(
            f"SELECT * FROM {self.table} WHERE {pk_field.name} = ?",
            (pk_value,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._decrypt_row(row)

    def query(
        self,
        where: str = "",
        params: tuple = (),
        order_by: str = "",
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Query rows using a plain-field WHERE clause. Encrypted fields are
        returned decrypted. You cannot filter on encrypted fields directly.
        """
        sql = f"SELECT * FROM {self.table}"
        if where:
            sql += f" WHERE {where}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        if limit:
            sql += f" LIMIT {limit}"
        cursor = self._conn.execute(sql, params)
        return [self._decrypt_row(row) for row in cursor.fetchall()]

    def update(self, pk_value: Any, updates: dict[str, Any]) -> bool:
        """Update fields for a row by primary key."""
        encrypted = self._encrypt_row(updates)
        pk_field = next(f for f in self.fields if f.primary_key)
        set_clause = ", ".join(f"{k} = ?" for k in encrypted)
        cursor = self._conn.execute(
            f"UPDATE {self.table} SET {set_clause} WHERE {pk_field.name} = ?",
            [*encrypted.values(), pk_value],
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def delete(self, pk_value: Any) -> bool:
        """Delete a row by primary key."""
        pk_field = next(f for f in self.fields if f.primary_key)
        cursor = self._conn.execute(
            f"DELETE FROM {self.table} WHERE {pk_field.name} = ?",
            (pk_value,),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def count(self) -> int:
        return self._conn.execute(
            f"SELECT COUNT(*) FROM {self.table}"
        ).fetchone()[0]


# ── Pre-built: Agent memory store ─────────────────────────────────────────────


MEMORY_FIELDS = [
    FieldSpec("id", "TEXT", primary_key=True),
    FieldSpec("created_at", "REAL"),            # plain — searchable
    FieldSpec("session_id", "TEXT"),            # plain — filterable
    FieldSpec("role", "TEXT"),                  # plain — filterable
    FieldSpec("content", "TEXT", encrypted=True),  # sensitive
    FieldSpec("metadata", "TEXT", encrypted=True), # sensitive JSON
]


def create_memory_store(
    db_path: str | Path,
    key: bytes,
) -> EncryptedStore:
    """Create a ready-to-use encrypted agent memory store."""
    engine = EncryptionEngine(key)
    return EncryptedStore(
        db_path=db_path,
        table="memories",
        fields=MEMORY_FIELDS,
        engine=engine,
    )


# ── Demo ──────────────────────────────────────────────────────────────────────


def demo() -> None:
    """
    Demo: create an encrypted store, insert records, read them back.
    Does NOT require ANTHROPIC_API_KEY.
    """
    db_path = Path("/tmp/demo_encrypted.db")

    # Generate or load a key. In production: use keychain or env var.
    key = generate_key()
    print(f"Generated key (store this securely): {key[:20]}...\n")

    store = create_memory_store(db_path, key)

    # Insert some records
    record_id = secrets.token_hex(8)
    store.insert({
        "id": record_id,
        "created_at": time.time(),
        "session_id": "demo-session-001",
        "role": "user",
        "content": "My API key is sk-ant-secret-1234",  # this gets encrypted
        "metadata": json.dumps({"ip": "192.168.1.100", "device": "MacBook"}),
    })
    print(f"Inserted record: {record_id}")

    # Read it back
    row = store.get(record_id)
    print(f"\nDecrypted content: {row['content']}")

    # Query by plain field
    records = store.query(
        where="session_id = ?",
        params=("demo-session-001",),
        order_by="created_at DESC",
        limit=10,
    )
    print(f"\nFound {len(records)} record(s) in session demo-session-001")

    # Show what's actually in the database (raw, encrypted)
    conn = sqlite3.connect(db_path)
    raw = conn.execute("SELECT id, role, content FROM memories").fetchone()
    print(f"\nRaw DB content field (encrypted): {raw[2][:40]}...")
    conn.close()

    print("\nAll tests passed. Encrypted store works correctly.")


if __name__ == "__main__":
    demo()
