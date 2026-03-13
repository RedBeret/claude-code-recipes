"""
Conversation Memory — Claude Code SDK Recipe

Pattern: persist conversation history in SQLite, inject recent context
into each query so the agent maintains state across calls.

Run:
    python recipe.py

Requires:
    ANTHROPIC_API_KEY environment variable
"""

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from claude_code_sdk import AssistantMessage, ClaudeCodeOptions, TextBlock, query

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-5"
CONTEXT_WINDOW = 10

Role = Literal["user", "assistant", "system"]


@dataclass
class ChatMessage:
    """A single message in a conversation."""
    role: Role
    content: str
    timestamp: float
    message_id: int | None = None

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


class ConversationStore:
    """SQLite-backed conversation history."""

    def __init__(self, db_path: str | Path = "conversations.db") -> None:
        self.db_path = Path(db_path).expanduser()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    metadata TEXT DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
                    content TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_session_ts "
                "ON messages(session_id, timestamp)"
            )
            conn.commit()
        finally:
            conn.close()

    def ensure_session(self, session_id: str) -> None:
        now = time.time()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO sessions(id, created_at, updated_at) VALUES(?,?,?)",
                (session_id, now, now),
            )
            conn.commit()
        finally:
            conn.close()

    def add_message(self, session_id: str, role: Role, content: str) -> int:
        """Append a message to the conversation. Returns rowid."""
        self.ensure_session(session_id)
        now = time.time()
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO messages(session_id, role, content, timestamp) VALUES(?,?,?,?)",
                (session_id, role, content, now),
            )
            conn.execute(
                "UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def get_recent(self, session_id: str, limit: int = CONTEXT_WINDOW) -> list[ChatMessage]:
        """Retrieve most recent messages, oldest-first."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, role, content, timestamp FROM messages "
                "WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        finally:
            conn.close()
        return [
            ChatMessage(role=r["role"], content=r["content"],
                        timestamp=r["timestamp"], message_id=r["id"])
            for r in reversed(rows)
        ]

    def get_all(self, session_id: str) -> list[ChatMessage]:
        """Retrieve all messages for a session."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, role, content, timestamp FROM messages "
                "WHERE session_id = ? ORDER BY timestamp",
                (session_id,),
            ).fetchall()
        finally:
            conn.close()
        return [
            ChatMessage(role=r["role"], content=r["content"],
                        timestamp=r["timestamp"], message_id=r["id"])
            for r in rows
        ]

    def list_sessions(self) -> list[str]:
        """Return all session IDs, most recently updated first."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        finally:
            conn.close()
        return [row["id"] for row in rows]

    def clear_session(self, session_id: str) -> int:
        """Delete all messages for a session. Returns count deleted."""
        conn = self._connect()
        try:
            cur = conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def format_history_for_prompt(messages: list[ChatMessage]) -> str:
    """Format conversation history as a string block for prompt injection."""
    if not messages:
        return ""
    lines = []
    for msg in messages:
        content = msg.content[:500] + "..." if len(msg.content) > 500 else msg.content
        lines.append(f"{msg.role.capitalize()}: {content}")
    return "\n".join(lines)


def build_system_prompt(base_system: str, history: list[ChatMessage]) -> str:
    """Build a system prompt with conversation history appended."""
    if not history:
        return base_system
    history_block = format_history_for_prompt(history)
    return f"{base_system}\n\n## Conversation History\n\n{history_block}"


class ConversationalAgent:
    """
    Stateful agent that remembers conversation history across calls.

    Usage:
        agent = ConversationalAgent(session_id="user-123")
        response = await agent.chat("What's 2 + 2?")
        response = await agent.chat("Multiply that by 10.")  # remembers context
    """

    def __init__(
        self,
        session_id: str,
        *,
        db_path: str | Path = "conversations.db",
        model: str = DEFAULT_MODEL,
        context_window: int = CONTEXT_WINDOW,
        base_system: str = "You are a helpful assistant.",
    ) -> None:
        self.session_id = session_id
        self.store = ConversationStore(db_path)
        self.model = model
        self.context_window = context_window
        self.base_system = base_system

    async def chat(self, user_message: str) -> str:
        """Send a message and get a response, with full conversation context."""
        self.store.add_message(self.session_id, "user", user_message)

        all_recent = self.store.get_recent(self.session_id, limit=self.context_window + 1)
        history = all_recent[:-1]

        system = build_system_prompt(self.base_system, history)
        options = ClaudeCodeOptions(model=self.model, system_prompt=system)

        full_text = ""
        async for message in query(prompt=user_message, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        full_text += block.text

        if not full_text:
            full_text = "[No response]"

        self.store.add_message(self.session_id, "assistant", full_text)
        return full_text

    def history(self) -> list[ChatMessage]:
        """Return the full conversation history for this session."""
        return self.store.get_all(self.session_id)

    def clear(self) -> None:
        """Wipe the conversation history for this session."""
        self.store.clear_session(self.session_id)


async def main() -> None:
    """Demonstrate conversation memory."""
    import os
    import tempfile

    print("=== Conversation Memory Demo ===\n")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    agent = ConversationalAgent(
        session_id="demo-001",
        db_path=db_path,
        model=DEFAULT_MODEL,
        base_system="You are a helpful assistant. Be concise.",
    )

    turns = [
        "My name is Morgan. Remember it.",
        "What is 15 multiplied by 7?",
        "What's my name? And double the result from the last question.",
    ]

    print("Running 3-turn conversation (each turn injects prior context):\n")
    for i, message in enumerate(turns, 1):
        print(f"Turn {i} — User: {message}")
        try:
            response = await agent.chat(message)
            print(f"         Assistant: {response.strip()[:200]}\n")
        except Exception as exc:
            print(f"         Error: {exc}\n")

    print("--- Stored History ---")
    for msg in agent.history():
        print(f"  [{msg.role:9}] {msg.content[:80].replace(chr(10), ' ')}")

    os.unlink(db_path)
    print("\nDemo DB cleaned up.")


if __name__ == "__main__":
    asyncio.run(main())
