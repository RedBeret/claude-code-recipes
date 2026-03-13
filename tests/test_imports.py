"""
Import tests — verify all recipes import without errors.

These tests don't make API calls. They check:
1. Each recipe imports successfully
2. Key classes and functions are defined
3. Basic logic (no API calls) works correctly
"""

import importlib
import importlib.util
import inspect
import sys
import types
from pathlib import Path

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def import_recipe(recipe_name: str) -> types.ModuleType:
    """Import a recipe module by directory name."""
    recipes_dir = Path(__file__).parent.parent / "recipes"
    recipe_dir = recipes_dir / recipe_name
    assert recipe_dir.exists(), f"Recipe directory not found: {recipe_dir}"

    spec = importlib.util.spec_from_file_location(
        recipe_name.replace("-", "_"),
        recipe_dir / "recipe.py",
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ── Recipe 1: Rate Limit Fallback ─────────────────────────────────────────────

class TestRateLimitFallback:

    @pytest.fixture(scope="class")
    def module(self):
        return import_recipe("rate-limit-fallback")

    def test_model_chain_defined(self, module):
        assert hasattr(module, "MODEL_CHAIN")
        assert len(module.MODEL_CHAIN) >= 2

    def test_is_rate_limit_error_detection(self, module):
        class E(Exception): pass
        assert module.is_rate_limit_error(E("rate limit exceeded")) is True
        assert module.is_rate_limit_error(E("HTTP 429 Too Many Requests")) is True
        assert module.is_rate_limit_error(E("API overloaded")) is True
        assert module.is_rate_limit_error(E("529 service unavailable")) is True

    def test_is_rate_limit_error_ignores_others(self, module):
        class E(Exception): pass
        assert module.is_rate_limit_error(E("authentication failed")) is False
        assert module.is_rate_limit_error(E("invalid model")) is False
        assert module.is_rate_limit_error(E("context window exceeded")) is False

    def test_retry_state_backoff(self, module):
        state = module.RetryState(current_model_index=0)
        delay1 = state.next_delay()
        state.attempt += 1
        delay2 = state.next_delay()
        state.attempt += 1
        delay3 = state.next_delay()
        assert delay1 == 1.0
        assert delay2 == 2.0
        assert delay3 == 4.0

    def test_retry_state_model_advance(self, module):
        state = module.RetryState(current_model_index=0)
        original = state.current_model
        advanced = state.advance_model()
        assert advanced is True
        assert state.current_model != original

    def test_retry_state_model_advance_at_end(self, module):
        last = len(module.MODEL_CHAIN) - 1
        state = module.RetryState(current_model_index=last)
        assert state.advance_model() is False

    def test_retry_state_max_delay_cap(self, module):
        state = module.RetryState(current_model_index=0)
        for i in range(20):
            delay = state.next_delay()
            state.attempt += 1
        assert delay <= module.MAX_DELAY

    def test_query_with_fallback_is_async(self, module):
        assert inspect.iscoroutinefunction(module.query_with_fallback)


# ── Recipe 2: Conversation Memory ─────────────────────────────────────────────

class TestConversationMemory:

    @pytest.fixture(scope="class")
    def module(self):
        return import_recipe("conversation-memory")

    @pytest.fixture
    def store(self, module, tmp_path):
        return module.ConversationStore(tmp_path / "test.db")

    def test_conversation_store_init(self, store):
        assert store.db_path.exists()

    def test_add_and_retrieve_messages(self, store):
        session = "test-001"
        store.add_message(session, "user", "Hello")
        store.add_message(session, "assistant", "Hi there!")
        history = store.get_all(session)
        assert len(history) == 2
        assert history[0].role == "user"
        assert history[0].content == "Hello"
        assert history[1].role == "assistant"

    def test_get_recent_limit(self, store):
        session = "test-002"
        for i in range(15):
            role = "user" if i % 2 == 0 else "assistant"
            store.add_message(session, role, f"Message {i}")
        recent = store.get_recent(session, limit=5)
        assert len(recent) == 5

    def test_get_recent_chronological_order(self, store):
        session = "test-003"
        store.add_message(session, "user", "first")
        store.add_message(session, "assistant", "second")
        store.add_message(session, "user", "third")
        recent = store.get_recent(session, limit=10)
        assert recent[0].content == "first"
        assert recent[-1].content == "third"

    def test_clear_session(self, store):
        session = "test-004"
        store.add_message(session, "user", "to delete")
        store.clear_session(session)
        assert store.get_all(session) == []

    def test_list_sessions(self, store):
        for name in ["alpha", "beta", "gamma"]:
            store.add_message(f"s-{name}", "user", "hello")
        sessions = store.list_sessions()
        for name in ["alpha", "beta", "gamma"]:
            assert f"s-{name}" in sessions

    def test_format_history_empty(self, module):
        assert module.format_history_for_prompt([]) == ""

    def test_format_history_with_messages(self, module):
        msgs = [
            module.ChatMessage(role="user", content="Hello", timestamp=1.0),
            module.ChatMessage(role="assistant", content="Hi!", timestamp=2.0),
        ]
        result = module.format_history_for_prompt(msgs)
        assert "User: Hello" in result
        assert "Assistant: Hi!" in result

    def test_build_system_prompt_no_history(self, module):
        base = "You are helpful."
        assert module.build_system_prompt(base, []) == base

    def test_build_system_prompt_with_history(self, module):
        base = "You are helpful."
        msgs = [module.ChatMessage(role="user", content="My name is Alice", timestamp=1.0)]
        result = module.build_system_prompt(base, msgs)
        assert "You are helpful." in result
        assert "Alice" in result

    def test_conversational_agent_init(self, module, tmp_path):
        agent = module.ConversationalAgent(
            session_id="test-agent-001",
            db_path=tmp_path / "agent.db",
        )
        assert agent.session_id == "test-agent-001"

    def test_chat_is_async(self, module):
        assert inspect.iscoroutinefunction(module.ConversationalAgent.chat)


# ── Recipe 3: MCP Servers ─────────────────────────────────────────────────────

class TestMCPServers:

    @pytest.fixture(scope="class")
    def module(self):
        return import_recipe("mcp-servers")

    def test_github_mcp_server_returns_dict(self, module):
        server = module.github_mcp_server(token="fake-token")
        assert isinstance(server, dict)
        assert server.get("type") == "http"
        assert "url" in server

    def test_github_mcp_server_sets_auth_header(self, module):
        server = module.github_mcp_server(token="test-token-123")
        assert "headers" in server
        assert "test-token-123" in server["headers"]["Authorization"]

    def test_playwright_mcp_server_returns_dict(self, module):
        server = module.playwright_mcp_server()
        assert isinstance(server, dict)
        assert "command" in server
        assert server["command"] == "npx"

    def test_playwright_mcp_server_uses_playwright_mcp(self, module):
        server = module.playwright_mcp_server()
        assert "@playwright/mcp" in server.get("args", [])

    def test_filesystem_mcp_server_returns_dict(self, module):
        server = module.filesystem_mcp_server(["/tmp"])
        assert isinstance(server, dict)
        assert "/tmp" in server.get("args", [])

    def test_custom_stdio_server(self, module):
        server = module.custom_stdio_server(
            command="python",
            args=["-m", "my_server"],
            env={"KEY": "value"},
        )
        assert isinstance(server, dict)
        assert server["command"] == "python"
        assert server.get("env", {}).get("KEY") == "value"

    def test_build_options_structure(self, module):
        from claude_code_sdk import ClaudeCodeOptions
        server = module.github_mcp_server(token="test")
        opts = module.build_options(
            {"github": server},
            model="claude-sonnet-4-5",
            system="Be helpful.",
        )
        assert isinstance(opts, ClaudeCodeOptions)
        assert "github" in opts.mcp_servers
        assert opts.model == "claude-sonnet-4-5"
        assert opts.system_prompt == "Be helpful."

    def test_build_options_with_allowed_tools(self, module):
        server = module.github_mcp_server(token="test")
        opts = module.build_options(
            {"github": server},
            allowed_tools=["github__search_repositories"],
        )
        assert "github__search_repositories" in opts.allowed_tools


# ── Recipe 4: Parallel Tasks ──────────────────────────────────────────────────

class TestParallelTasks:

    @pytest.fixture(scope="class")
    def module(self):
        return import_recipe("parallel-tasks")

    def test_task_dataclass(self, module):
        task = module.Task(name="test", prompt="hello")
        assert task.name == "test"
        assert task.prompt == "hello"
        assert task.metadata == {}

    def test_task_result_dataclass(self, module):
        task = module.Task(name="test", prompt="hello")
        result = module.TaskResult(
            task=task, response="ok", error=None, duration_s=1.5, success=True
        )
        assert result.success is True
        assert result.duration_s == 1.5

    def test_parallel_runner_init(self, module):
        runner = module.ParallelRunner(max_concurrency=5, model="claude-haiku-4-5")
        assert runner.max_concurrency == 5
        assert runner.model == "claude-haiku-4-5"

    def test_chunk_function(self, module):
        items = list(range(10))
        chunks = module.chunk(items, 3)
        assert len(chunks) == 4
        assert chunks[0] == [0, 1, 2]
        assert chunks[-1] == [9]

    def test_chunk_exact_multiple(self, module):
        items = list(range(9))
        chunks = module.chunk(items, 3)
        assert len(chunks) == 3
        assert all(len(c) == 3 for c in chunks)

    def test_chunk_empty(self, module):
        assert module.chunk([], 5) == []

    def test_parallel_runner_run_is_async(self, module):
        assert inspect.iscoroutinefunction(module.ParallelRunner.run)

    def test_run_in_batches_is_async(self, module):
        assert inspect.iscoroutinefunction(module.run_in_batches)

    @pytest.mark.asyncio
    async def test_parallel_runner_empty_tasks(self, module):
        runner = module.ParallelRunner()
        results = await runner.run([])
        assert results == []

    def test_create_worktree_callable(self, module):
        assert callable(module.create_worktree)

    def test_remove_worktree_callable(self, module):
        assert callable(module.remove_worktree)

# ── Recipe 5: Session Management ─────────────────────────────────────────────


class TestSessionManagement:

    @pytest.fixture(scope="class")
    def module(self):
        return import_recipe("session-management")

    @pytest.fixture
    def store(self, module, tmp_path):
        return module.SessionStore(path=tmp_path / "sessions.json")

    def test_session_store_creates_file_on_save(self, module, tmp_path):
        store = module.SessionStore(path=tmp_path / "s.json")
        session = module.SavedSession(session_id="abc123", task="test task")
        store.save(session)
        assert (tmp_path / "s.json").exists()

    def test_session_store_save_and_get(self, store, module):
        session = module.SavedSession(session_id="sid-001", task="Analyze code")
        store.save(session)
        retrieved = store.get("sid-001")
        assert retrieved is not None
        assert retrieved.session_id == "sid-001"
        assert retrieved.task == "Analyze code"

    def test_session_store_get_missing_returns_none(self, store):
        assert store.get("nonexistent-id") is None

    def test_session_store_list_active_excludes_completed(self, store, module):
        store.save(module.SavedSession(session_id="active-1", task="Active"))
        store.save(module.SavedSession(session_id="done-1", task="Done"))
        store.mark_completed("done-1", summary="finished")
        active = store.list_active()
        active_ids = [s.session_id for s in active]
        assert "active-1" in active_ids
        assert "done-1" not in active_ids

    def test_session_store_list_all_includes_completed(self, store, module):
        store.save(module.SavedSession(session_id="s-all-1", task="A"))
        store.save(module.SavedSession(session_id="s-all-2", task="B"))
        store.mark_completed("s-all-2", "done")
        all_sessions = store.list_all()
        all_ids = [s.session_id for s in all_sessions]
        assert "s-all-1" in all_ids
        assert "s-all-2" in all_ids

    def test_mark_completed_sets_flag(self, store, module):
        store.save(module.SavedSession(session_id="to-complete", task="Task"))
        store.mark_completed("to-complete", summary="All done")
        s = store.get("to-complete")
        assert s.completed is True
        assert s.result_summary == "All done"

    def test_session_store_delete(self, store, module):
        store.save(module.SavedSession(session_id="to-delete", task="Delete me"))
        assert store.delete("to-delete") is True
        assert store.get("to-delete") is None

    def test_session_store_delete_missing_returns_false(self, store):
        assert store.delete("never-existed") is False

    def test_saved_session_to_dict(self, module):
        s = module.SavedSession(session_id="abc", task="Test")
        d = s.to_dict()
        assert d["session_id"] == "abc"
        assert d["task"] == "Test"
        assert "created_at" in d

    def test_saved_session_from_dict(self, module):
        data = {
            "session_id": "xyz",
            "task": "From dict",
            "created_at": 1234567890.0,
            "last_active": 1234567890.0,
            "completed": False,
            "result_summary": "",
        }
        s = module.SavedSession.from_dict(data)
        assert s.session_id == "xyz"
        assert s.task == "From dict"

    def test_format_session_list_empty(self, module):
        result = module.format_session_list([])
        assert "(no sessions)" in result

    def test_format_session_list_shows_session(self, module):
        s = module.SavedSession(session_id="abc123def456", task="Write a report")
        result = module.format_session_list([s])
        assert "abc123" in result

    def test_resumable_agent_run_is_async(self, module):
        assert inspect.iscoroutinefunction(module.ResumableAgent.run)

    def test_resumable_agent_resume_is_async(self, module):
        assert inspect.iscoroutinefunction(module.ResumableAgent.resume)

    def test_resumable_agent_init(self, module, tmp_path):
        store = module.SessionStore(path=tmp_path / "sessions.json")
        agent = module.ResumableAgent(store=store)
        assert agent.store is store


# ── Recipe 6: Scheduled Agents ────────────────────────────────────────────────


class TestScheduledAgents:

    @pytest.fixture(scope="class")
    def module(self):
        return import_recipe("scheduled-agents")

    def test_cron_matches_wildcard(self, module):
        from datetime import datetime, timezone
        dt = datetime(2026, 3, 13, 14, 30, tzinfo=timezone.utc)  # 14:30 Thursday
        assert module.cron_matches("* * * * *", dt) is True

    def test_cron_matches_exact_minute(self, module):
        from datetime import datetime, timezone
        dt = datetime(2026, 3, 13, 14, 30, tzinfo=timezone.utc)
        assert module.cron_matches("30 14 * * *", dt) is True
        assert module.cron_matches("29 14 * * *", dt) is False

    def test_cron_matches_step(self, module):
        from datetime import datetime, timezone
        dt_30 = datetime(2026, 3, 13, 14, 30, tzinfo=timezone.utc)
        dt_31 = datetime(2026, 3, 13, 14, 31, tzinfo=timezone.utc)
        dt_15 = datetime(2026, 3, 13, 14, 15, tzinfo=timezone.utc)
        assert module.cron_matches("*/15 * * * *", dt_30) is True
        assert module.cron_matches("*/15 * * * *", dt_15) is True
        assert module.cron_matches("*/15 * * * *", dt_31) is False

    def test_cron_matches_range(self, module):
        from datetime import datetime, timezone
        # 9am Monday-Friday
        mon = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)   # Monday
        sat = datetime(2026, 3, 14, 9, 0, tzinfo=timezone.utc)  # Saturday
        assert module.cron_matches("0 9 * * 1-5", mon) is True
        assert module.cron_matches("0 9 * * 1-5", sat) is False

    def test_cron_alias_hourly(self, module):
        from datetime import datetime, timezone
        dt = datetime(2026, 3, 13, 14, 0, tzinfo=timezone.utc)  # top of hour
        assert module.cron_matches("@hourly", dt) is True

    def test_cron_alias_daily(self, module):
        from datetime import datetime, timezone
        midnight = datetime(2026, 3, 13, 0, 0, tzinfo=timezone.utc)
        other = datetime(2026, 3, 13, 12, 0, tzinfo=timezone.utc)
        assert module.cron_matches("@daily", midnight) is True
        assert module.cron_matches("@daily", other) is False

    def test_cron_invalid_field_count(self, module):
        import pytest
        from datetime import datetime, timezone
        dt = datetime(2026, 3, 13, 14, 30, tzinfo=timezone.utc)
        with pytest.raises(ValueError):
            module.cron_matches("* * * *", dt)  # only 4 fields

    def test_parse_field_wildcard(self, module):
        values = module._parse_field("*", 0, 59)
        assert len(values) == 60
        assert 0 in values and 59 in values

    def test_parse_field_exact(self, module):
        values = module._parse_field("30", 0, 59)
        assert values == {30}

    def test_parse_field_range(self, module):
        values = module._parse_field("1-5", 0, 59)
        assert values == {1, 2, 3, 4, 5}

    def test_parse_field_comma(self, module):
        values = module._parse_field("1,15,30", 0, 59)
        assert values == {1, 15, 30}

    def test_scheduled_task_matches(self, module):
        from datetime import datetime, timezone
        task = module.ScheduledTask(
            name="test",
            schedule="0 * * * *",
            prompt="hello",
        )
        top_of_hour = datetime(2026, 3, 13, 15, 0, tzinfo=timezone.utc)
        not_top = datetime(2026, 3, 13, 15, 1, tzinfo=timezone.utc)
        assert task.matches(top_of_hour) is True
        assert task.matches(not_top) is False

    def test_scheduled_task_disabled(self, module):
        from datetime import datetime, timezone
        task = module.ScheduledTask(
            name="test",
            schedule="* * * * *",
            prompt="hello",
            enabled=False,
        )
        dt = datetime(2026, 3, 13, 15, 0, tzinfo=timezone.utc)
        assert task.matches(dt) is False

    def test_agent_scheduler_init(self, module):
        tasks = [module.ScheduledTask(name="t1", schedule="@hourly", prompt="p")]
        scheduler = module.AgentScheduler(tasks=tasks)
        assert len(scheduler.tasks) == 1

    def test_load_tasks_from_config(self, module):
        config = [
            {"name": "task1", "schedule": "@hourly", "prompt": "Check status."},
            {"name": "task2", "schedule": "0 9 * * *", "prompt": "Morning report."},
        ]
        tasks = module.load_tasks_from_config(config)
        assert len(tasks) == 2
        assert tasks[0].name == "task1"
        assert tasks[1].schedule == "0 9 * * *"

    def test_load_tasks_default_enabled(self, module):
        config = [{"name": "t", "schedule": "@daily", "prompt": "p"}]
        tasks = module.load_tasks_from_config(config)
        assert tasks[0].enabled is True

    def test_seconds_until_next_minute(self, module):
        from datetime import datetime, timezone
        dt = datetime(2026, 3, 13, 14, 30, 45, 500000, tzinfo=timezone.utc)
        secs = module.seconds_until_next_minute(dt)
        assert 14.0 <= secs <= 15.0

    def test_scheduler_run_is_async(self, module):
        assert inspect.iscoroutinefunction(module.AgentScheduler.run)

    def test_scheduler_run_now_is_async(self, module):
        assert inspect.iscoroutinefunction(module.AgentScheduler.run_now)


# ── Recipe 7: Signal Integration ─────────────────────────────────────────────


class TestSignalIntegration:

    @pytest.fixture(scope="class")
    def module(self):
        return import_recipe("signal-integration")

    def test_signal_config_from_env(self, module, monkeypatch):
        monkeypatch.setenv("SIGNAL_ACCOUNT", "+12065551234")
        monkeypatch.setenv("SIGNAL_ALLOWLIST", "+19995550001,+19995550002")
        config = module.SignalConfig.from_env()
        assert config.account == "+12065551234"
        assert len(config.allowlist) == 2
        assert "+19995550001" in config.allowlist

    def test_signal_config_empty_allowlist(self, module, monkeypatch):
        monkeypatch.setenv("SIGNAL_ACCOUNT", "+12065551234")
        monkeypatch.delenv("SIGNAL_ALLOWLIST", raising=False)
        config = module.SignalConfig.from_env()
        assert config.allowlist == []

    def test_signal_bot_allowlist_check(self, module):
        config = module.SignalConfig(
            account="+12065551234",
            allowlist=["+19995550001"],
        )
        bot = module.SignalBot(config=config)
        assert bot.is_allowed("+19995550001") is True
        assert bot.is_allowed("+19995550000") is False

    def test_signal_bot_empty_allowlist_blocks_all(self, module):
        config = module.SignalConfig(account="+12065551234", allowlist=[])
        bot = module.SignalBot(config=config)
        assert bot.is_allowed("+12065551234") is False
        assert bot.is_allowed("+19995550001") is False

    def test_incoming_message_dataclass(self, module):
        msg = module.IncomingMessage(
            sender="+12065551234",
            text="Hello there",
            timestamp=1709900000,
        )
        assert msg.sender == "+12065551234"
        assert msg.text == "Hello there"
        assert msg.group_id is None

    def test_signal_transport_init(self, module):
        config = module.SignalConfig(account="+12065551234", allowlist=[])
        transport = module.SignalTransport(config=config)
        assert transport.config is config

    def test_handle_message_is_async(self, module):
        assert inspect.iscoroutinefunction(module.SignalBot.handle_message)

    def test_bot_run_is_async(self, module):
        assert inspect.iscoroutinefunction(module.SignalBot.run)

    def test_bot_run_once_is_async(self, module):
        assert inspect.iscoroutinefunction(module.SignalBot.run_once)

    def test_send_notification_callable(self, module):
        assert callable(module.send_notification)

    def test_signal_config_defaults(self, module):
        config = module.SignalConfig(account="+1234", allowlist=[])
        assert "127.0.0.1" in config.daemon_url
        assert config.poll_timeout == 5


# ── Recipe 8: Encrypted Stores ────────────────────────────────────────────────


class TestEncryptedStores:

    @pytest.fixture(scope="class")
    def module(self):
        return import_recipe("encrypted-stores")

    @pytest.fixture
    def key(self, module):
        return module.generate_key()

    @pytest.fixture
    def engine(self, module, key):
        return module.EncryptionEngine(key=key)

    @pytest.fixture
    def store(self, module, tmp_path, key):
        fields = module.MEMORY_FIELDS
        engine = module.EncryptionEngine(key=key)
        return module.EncryptedStore(
            db_path=tmp_path / "test.db",
            table="memories",
            fields=fields,
            engine=engine,
        )

    def test_generate_key_is_valid_fernet_key(self, module):
        key = module.generate_key()
        # Fernet keys are URL-safe base64 of 32 bytes = 44 chars
        assert len(key) == 44
        # Should be able to create a Fernet instance
        from cryptography.fernet import Fernet
        f = Fernet(key)
        assert f is not None

    def test_engine_encrypt_decrypt_roundtrip(self, engine):
        plaintext = "My secret API key is sk-abc123"
        token = engine.encrypt(plaintext)
        assert token != plaintext
        recovered = engine.decrypt(token)
        assert recovered == plaintext

    def test_engine_encrypt_json_roundtrip(self, engine):
        obj = {"ip": "192.168.1.100", "user": "alice", "tags": [1, 2, 3]}
        token = engine.encrypt_json(obj)
        recovered = engine.decrypt_json(token)
        assert recovered == obj

    def test_engine_encrypt_bytes_roundtrip(self, engine):
        data = b"\x00\x01\x02\x03secret bytes"
        encrypted = engine.encrypt_bytes(data)
        assert encrypted != data
        assert engine.decrypt_bytes(encrypted) == data

    def test_engine_wrong_key_raises(self, module):
        from cryptography.fernet import InvalidToken
        key1 = module.generate_key()
        key2 = module.generate_key()
        e1 = module.EncryptionEngine(key=key1)
        e2 = module.EncryptionEngine(key=key2)
        token = e1.encrypt("secret")
        with pytest.raises(InvalidToken):
            e2.decrypt(token)

    def test_encrypted_store_insert_and_get(self, store, module):
        import time
        rid = "row-001"
        store.insert({
            "id": rid,
            "created_at": time.time(),
            "session_id": "s1",
            "role": "user",
            "content": "My secret message",
            "metadata": '{"key": "value"}',
        })
        row = store.get(rid)
        assert row is not None
        assert row["id"] == rid
        assert row["content"] == "My secret message"

    def test_encrypted_store_content_is_encrypted_in_db(self, tmp_path, module):
        import sqlite3
        import time
        key = module.generate_key()
        engine = module.EncryptionEngine(key=key)
        store = module.EncryptedStore(
            db_path=tmp_path / "enc.db",
            table="memories",
            fields=module.MEMORY_FIELDS,
            engine=engine,
        )
        store.insert({
            "id": "enc-001",
            "created_at": time.time(),
            "session_id": "s1",
            "role": "user",
            "content": "plaintext secret",
            "metadata": "{}",
        })
        # Check raw DB — content should NOT be plaintext
        conn = sqlite3.connect(tmp_path / "enc.db")
        raw = conn.execute("SELECT content FROM memories WHERE id = 'enc-001'").fetchone()
        conn.close()
        assert raw[0] != "plaintext secret"
        assert len(raw[0]) > 50  # Fernet tokens are much longer

    def test_encrypted_store_query_by_plain_field(self, store, module):
        import time
        for i in range(3):
            store.insert({
                "id": f"q-{i}",
                "created_at": time.time(),
                "session_id": "session-query",
                "role": "user",
                "content": f"Message {i}",
                "metadata": "{}",
            })
        results = store.query("session_id = ?", ("session-query",))
        assert len(results) == 3
        contents = [r["content"] for r in results]
        assert "Message 0" in contents

    def test_encrypted_store_update(self, store, module):
        import time
        store.insert({
            "id": "upd-001",
            "created_at": time.time(),
            "session_id": "s1",
            "role": "user",
            "content": "original",
            "metadata": "{}",
        })
        assert store.update("upd-001", {"content": "updated secret"}) is True
        row = store.get("upd-001")
        assert row["content"] == "updated secret"

    def test_encrypted_store_delete(self, store, module):
        import time
        store.insert({
            "id": "del-001",
            "created_at": time.time(),
            "session_id": "s1",
            "role": "user",
            "content": "to delete",
            "metadata": "{}",
        })
        assert store.delete("del-001") is True
        assert store.get("del-001") is None

    def test_encrypted_store_count(self, store, module):
        import time
        initial = store.count()
        store.insert({
            "id": "cnt-001",
            "created_at": time.time(),
            "session_id": "s1",
            "role": "user",
            "content": "counting",
            "metadata": "{}",
        })
        assert store.count() == initial + 1

    def test_create_memory_store_factory(self, module, tmp_path):
        key = module.generate_key()
        store = module.create_memory_store(tmp_path / "mem.db", key)
        assert store is not None
        assert store.table == "memories"

    def test_derive_key_deterministic(self, module):
        password = "correct-horse-battery-staple"
        salt = b"\x00" * 16
        key1 = module.derive_key(password, salt)
        key2 = module.derive_key(password, salt)
        assert key1 == key2

    def test_derive_key_different_salts(self, module):
        password = "same-password"
        salt1 = b"\x00" * 16
        salt2 = b"\x01" * 16
        key1 = module.derive_key(password, salt1)
        key2 = module.derive_key(password, salt2)
        assert key1 != key2

    def test_load_key_from_env(self, module, monkeypatch):
        key = module.generate_key()
        monkeypatch.setenv("STORE_ENCRYPTION_KEY", key.decode())
        loaded = module.load_key_from_env("STORE_ENCRYPTION_KEY")
        assert loaded == key

    def test_load_key_from_env_missing(self, module, monkeypatch):
        monkeypatch.delenv("STORE_ENCRYPTION_KEY", raising=False)
        assert module.load_key_from_env("STORE_ENCRYPTION_KEY") is None

    def test_field_spec_ddl_plain(self, module):
        f = module.FieldSpec("name", "TEXT")
        assert f.ddl() == "name TEXT"

    def test_field_spec_ddl_primary_key(self, module):
        f = module.FieldSpec("id", "TEXT", primary_key=True)
        assert "PRIMARY KEY" in f.ddl()
