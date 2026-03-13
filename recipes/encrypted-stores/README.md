# Recipe: Encrypted Stores

Field-level Fernet encryption for SQLite agent data. Sensitive content is
encrypted at rest while search fields stay plain and queryable.

## The Problem

Agent data often contains sensitive information: user messages, API keys,
personal details, system configurations. Storing this in plain SQLite means
anyone who gets the `.db` file can read everything. This recipe shows how
to encrypt sensitive columns using Fernet (AES-128-CBC + HMAC-SHA256) while
keeping indexable fields plain for efficient querying.

## How It Works

```
Insert:
  { id: "abc", session_id: "s1", content: "sensitive text" }
    → content encrypted → "gAAAAA..." stored in DB

Query (by plain field):
  SELECT * WHERE session_id = 's1'
    → rows fetched → content decrypted on read

What an attacker with the .db file sees:
  id        session_id   content
  "abc"     "s1"         "gAAAAAbU3K..."  ← opaque Fernet token
```

## Key Concepts

### Field-level encryption

Not all fields need encryption. Encrypt fields that contain sensitive data;
leave search/filter fields plain:

```python
fields = [
    FieldSpec("id", "TEXT", primary_key=True),
    FieldSpec("created_at", "REAL"),              # plain — queryable
    FieldSpec("session_id", "TEXT"),              # plain — filterable
    FieldSpec("content", "TEXT", encrypted=True), # sensitive
    FieldSpec("metadata", "TEXT", encrypted=True),# sensitive JSON
]
```

### EncryptionEngine

Wraps a Fernet key with helper methods:

```python
engine = EncryptionEngine(key=generate_key())

# Encrypt/decrypt strings
token = engine.encrypt("My API key is sk-...")
plain = engine.decrypt(token)

# Encrypt/decrypt arbitrary JSON
token = engine.encrypt_json({"ip": "192.168.1.100", "user": "alice"})
obj   = engine.decrypt_json(token)

# Encrypt/decrypt raw bytes (for embeddings)
enc_bytes = engine.encrypt_bytes(embedding_vector)
raw_bytes  = engine.decrypt_bytes(enc_bytes)
```

### EncryptedStore

SQLite store with automatic encrypt-on-write, decrypt-on-read:

```python
store = EncryptedStore(
    db_path="memories.db",
    table="memories",
    fields=fields,
    engine=engine,
)

store.insert({"id": "abc", "session_id": "s1", "content": "secret text"})
row = store.get("abc")              # returns decrypted row
rows = store.query("session_id = ?", ("s1",))  # filter by plain field
store.update("abc", {"content": "updated secret"})
store.delete("abc")
```

## Key Management

**The encryption key is the secret.** The `.db` file is safe if the key is
stored separately. Never commit keys to git.

### Option 1: Environment variable (simple)

```python
key = os.environ.get("STORE_ENCRYPTION_KEY", "").encode()
# Set in .env or system environment
```

### Option 2: macOS Keychain (recommended for desktop apps)

```python
# Save once:
key = generate_key()
save_key_to_keychain(key, service="my-agent", account="store")

# Load on each run:
key = load_key_from_keychain(service="my-agent", account="store")
```

The macOS Keychain is encrypted by your login password and survives reboots.

### Option 3: Derive from password (for user-facing apps)

```python
salt = os.urandom(16)          # generate once, store alongside .db
key = derive_key(password, salt)
```

Never store the password itself — only derive the key when needed.

## What to Encrypt vs What Not To

| Field | Encrypt? | Reason |
|-------|----------|--------|
| Message content | Yes | May contain sensitive info |
| API responses | Yes | May contain personal data |
| Metadata / JSON | Yes | Often contains identifying info |
| Embeddings | Optional | Not readable but link to content |
| Session ID | No | Need to filter by this |
| Timestamps | No | Need to sort/range-query |
| Role (user/assistant) | No | Need to filter by this |
| Row ID / PK | No | Need for lookups |

## Pitfalls

**Key rotation is hard.** If you rotate keys, you need to re-encrypt all
existing rows. Plan your key rotation strategy before you need it.

**You cannot filter encrypted fields.** `WHERE content LIKE '%secret%'`
will never work. Structure your schema so filtering happens on plain fields.

**InvalidToken = wrong key.** If decryption fails with `InvalidToken`, the
key is wrong or the data was tampered. Don't silently return None — log and
alert.

**Backup the key separately from the DB.** If you lose the key, all data
in encrypted fields is gone forever.

## Install

```bash
pip install cryptography
```

## Usage

```bash
python recipe.py
```
