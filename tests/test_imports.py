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
