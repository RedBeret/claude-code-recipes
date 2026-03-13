# Recipe: Conversation Memory

**Pattern:** Store message history in SQLite, inject recent context into each query.

## The Problem

Claude Code SDK agents are stateless by default. Each call to `query()` starts
fresh — the model has no memory of what was said before. For assistants, support
bots, or any agent that needs context across turns, you have to manage state yourself.

## The Pattern

```
User message
    ↓
Store in SQLite
    ↓
Fetch last N messages
    ↓
Inject as context in system prompt
    ↓
Call query()
    ↓
Store assistant response
    ↓
Return to user
```

## Core Components

### ConversationStore

SQLite-backed storage with two tables:
- `sessions` — one row per conversation (session_id, created_at, updated_at)
- `messages` — all messages (session_id, role, content, timestamp)

WAL mode enabled for safe concurrent reads.

```python
store = ConversationStore("~/.myapp/conversations.db")
store.add_message("session-123", "user", "Hello")
store.add_message("session-123", "assistant", "Hi there!")
history = store.get_recent("session-123", limit=10)
```

### Context Injection

The simplest injection method is appending history to the system prompt:

```python
def build_system_prompt(base_system, history):
    history_block = "\n".join(
        f"{m.role.capitalize()}: {m.content}" for m in history
    )
    return f"{base_system}\n\n## History\n{history_block}"
```

**Alternative:** Pass history as a messages array if the SDK supports it.
Append the current user message as the final element. This is more token-efficient
for long histories because the model can reference them directly.

### Context Window Budgeting

Decide how many messages to inject. This is a trade-off:

| Window | Pros | Cons |
|--------|------|------|
| 5-10 | Cheap, fast | Loses long-term context |
| 20-50 | Good continuity | More tokens per call |
| All | Perfect recall | Can exceed model context limit |

A good starting point: `limit=10`. Increase if users complain about forgetting.
Truncate long individual messages (>500 chars) to further reduce token usage.

## Session Management

Use meaningful session IDs:
- **Per-user:** `f"user-{user_id}"` — one ongoing conversation per user
- **Per-topic:** `f"user-{user_id}-topic-{topic}"` — separate threads
- **Per-day:** `f"user-{user_id}-{date}"` — daily reset

```python
agent = ConversationalAgent(
    session_id="user-42",
    db_path="~/.myapp/chats.db",
)
```

## ConversationalAgent

The `ConversationalAgent` class wraps store + query call into a clean `chat()` interface:

```python
agent = ConversationalAgent(
    session_id="user-42",
    db_path="~/.myapp/chats.db",
    model="claude-sonnet-4-5",
    base_system="You are a helpful assistant.",
    context_window=10,
)

response = await agent.chat("My name is Morgan.")
response = await agent.chat("What's my name?")  # → "Morgan"
```

## Pitfalls

**Forgetting to store the assistant response.** If you only store user messages,
the model sees a one-sided conversation. Always store both sides.

**Context window overflow.** Each injected message costs tokens. If you inject
100 messages and each is 200 tokens, that's 20k tokens before your prompt.
Keep `context_window` small and truncate long messages.

**No session isolation.** Two concurrent users with the same `session_id`
will see each other's history. Make session IDs user-specific.

**Database growth.** Old conversations accumulate. Add a periodic cleanup job:
```python
conn.execute(
    "DELETE FROM messages WHERE timestamp < ?",
    (time.time() - 30 * 86400,)  # older than 30 days
)
```

## Running the Recipe

```bash
export ANTHROPIC_API_KEY=your-key
python recipe.py
```

Expected output (abbreviated):
```
=== Conversation Memory Demo ===

Turn 1 — User: My name is Morgan. Remember it.
         Assistant: Got it! I'll remember that your name is Morgan.

Turn 2 — User: What is 15 multiplied by 7?
         Assistant: 15 × 7 = 105

Turn 3 — User: What's my name? And double the result from the last question.
         Assistant: Your name is Morgan. Double 105 is 210.
```

The model correctly remembers "Morgan" and "105" from prior turns because we
injected the history into the system prompt.
