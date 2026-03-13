"""
Session Management — Claude Code SDK Recipe

Pattern: persist session IDs to disk so long-running agent tasks can be
interrupted and resumed exactly where they left off.

Run:
    python recipe.py

Requires:
    ANTHROPIC_API_KEY environment variable
"""

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from claude_code_sdk import (
    AssistantMessage,
    ClaudeCodeOptions,
    ResultMessage,
    TextBlock,
    query,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-5"
SESSIONS_FILE = Path("sessions.json")


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class SavedSession:
    """A persisted agent session that can be resumed."""

    session_id: str
    task: str
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    completed: bool = False
    result_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SavedSession":
        return cls(**data)


# ── Session store ─────────────────────────────────────────────────────────────


class SessionStore:
    """Persist and retrieve agent sessions from a JSON file."""

    def __init__(self, path: str | Path = SESSIONS_FILE) -> None:
        self.path = Path(path)
        self._sessions: dict[str, SavedSession] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            data = json.loads(self.path.read_text())
            self._sessions = {
                sid: SavedSession.from_dict(info)
                for sid, info in data.items()
            }

    def _save(self) -> None:
        self.path.write_text(
            json.dumps(
                {sid: s.to_dict() for sid, s in self._sessions.items()},
                indent=2,
            )
        )

    def save(self, session: SavedSession) -> None:
        """Persist a session to disk."""
        session.last_active = time.time()
        self._sessions[session.session_id] = session
        self._save()

    def get(self, session_id: str) -> SavedSession | None:
        return self._sessions.get(session_id)

    def list_active(self) -> list[SavedSession]:
        return [s for s in self._sessions.values() if not s.completed]

    def list_all(self) -> list[SavedSession]:
        return list(self._sessions.values())

    def mark_completed(self, session_id: str, summary: str = "") -> None:
        session = self._sessions.get(session_id)
        if session:
            session.completed = True
            session.result_summary = summary
            self._save()

    def delete(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            self._save()
            return True
        return False


# ── Agent ─────────────────────────────────────────────────────────────────────


class ResumableAgent:
    """
    An agent that saves its session ID and can be resumed after interruption.

    Usage:
        agent = ResumableAgent()
        result = await agent.run("Analyze my codebase and write a report")

        # Later, if interrupted:
        session_id = store.list_active()[0].session_id
        agent = ResumableAgent()
        result = await agent.resume(session_id, "Continue the report")
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        store: SessionStore | None = None,
    ) -> None:
        self.model = model
        self.store = store or SessionStore()

    async def run(
        self,
        task: str,
        system_prompt: str = "You are a helpful assistant.",
    ) -> tuple[str, str]:
        """
        Run a new agent task. Returns (session_id, response_text).

        The session_id is saved to disk. If the task is interrupted, call
        resume(session_id, follow_up) to continue.
        """
        options = ClaudeCodeOptions(
            model=self.model,
            system_prompt=system_prompt,
        )

        session_id: str = ""
        text_parts: list[str] = []

        logger.info("Starting new session for task: %s", task[:60])

        async for message in query(prompt=task, options=options):
            if isinstance(message, ResultMessage):
                # The SDK provides a session_id on the ResultMessage
                if hasattr(message, "session_id") and message.session_id:
                    session_id = message.session_id
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)

        response = "\n".join(text_parts)

        if session_id:
            saved = SavedSession(session_id=session_id, task=task)
            self.store.save(saved)
            logger.info("Session saved: %s", session_id)
        else:
            logger.warning("No session_id returned — cannot resume later")

        return session_id, response

    async def resume(
        self,
        session_id: str,
        follow_up: str,
    ) -> tuple[str, str]:
        """
        Resume an existing session with a follow-up prompt.
        Returns (session_id, response_text).
        """
        saved = self.store.get(session_id)
        if not saved:
            raise ValueError(f"Unknown session: {session_id}")

        logger.info("Resuming session %s", session_id)

        options = ClaudeCodeOptions(
            model=self.model,
            resume=session_id,
        )

        new_session_id = session_id
        text_parts: list[str] = []

        async for message in query(prompt=follow_up, options=options):
            if isinstance(message, ResultMessage):
                if hasattr(message, "session_id") and message.session_id:
                    new_session_id = message.session_id
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)

        response = "\n".join(text_parts)

        # Update the stored session with the new session_id (may have rotated)
        if new_session_id != session_id:
            saved_copy = SavedSession(
                session_id=new_session_id,
                task=saved.task,
                created_at=saved.created_at,
            )
            self.store.save(saved_copy)
        else:
            self.store.save(saved)

        logger.info("Session resumed and updated: %s", new_session_id)
        return new_session_id, response

    def list_sessions(self, active_only: bool = True) -> list[SavedSession]:
        """List saved sessions."""
        if active_only:
            return self.store.list_active()
        return self.store.list_all()


# ── Utilities ─────────────────────────────────────────────────────────────────


def format_session_list(sessions: list[SavedSession]) -> str:
    """Format a list of sessions for display."""
    if not sessions:
        return "(no sessions)"
    lines = []
    for s in sessions:
        age = time.time() - s.created_at
        age_str = f"{int(age // 3600)}h {int((age % 3600) // 60)}m ago"
        status = "✓ done" if s.completed else "⏳ active"
        lines.append(f"  {s.session_id[:16]}...  [{status}]  {age_str}  {s.task[:50]}")
    return "\n".join(lines)


# ── Demo ──────────────────────────────────────────────────────────────────────


async def demo() -> None:
    """
    Demonstrate session save/resume.

    Run once to start a task and save the session.
    Run again to resume the session with a follow-up.
    """
    store = SessionStore(path="/tmp/demo_sessions.json")
    agent = ResumableAgent(store=store)

    active = agent.list_sessions(active_only=True)

    if active:
        # Resume the most recent active session
        session = active[0]
        print(f"\nResuming session: {session.session_id[:20]}...")
        print(f"  Original task: {session.task}\n")

        sid, response = await agent.resume(
            session.session_id,
            follow_up="Please summarize what we discussed so far in two sentences.",
        )
        print(f"Response:\n{response}\n")
        store.mark_completed(sid, summary=response[:200])
        print("Session marked complete.")

    else:
        # Start a fresh task
        print("\nStarting new session...")
        sid, response = await agent.run(
            task="What are three key principles of good software architecture?",
            system_prompt="You are a software engineering expert. Be concise.",
        )
        print(f"Session ID: {sid}")
        print(f"\nResponse:\n{response}\n")
        print(f"Session saved. Run again to resume.")


if __name__ == "__main__":
    asyncio.run(demo())
