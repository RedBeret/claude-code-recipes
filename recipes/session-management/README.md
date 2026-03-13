# Recipe: Session Management

Save agent session IDs to disk so long-running tasks can be interrupted and
resumed exactly where they left off.

## The Problem

Claude Code SDK agents are stateless by default. If your script crashes, the
machine restarts, or you interrupt a long task, you lose all context. For
multi-step tasks — "analyze this codebase and write a report" — losing context
means starting over.

The solution is to persist the `session_id` returned by each `query()` call and
pass it back via `options.resume` to continue the conversation.

## How It Works

```
First run:
    query(prompt=task, options=ClaudeCodeOptions(...))
    → ResultMessage.session_id = "abc123..."
    → Save {session_id, task, timestamp} to sessions.json

Interrupted / follow-up:
    query(prompt=follow_up, options=ClaudeCodeOptions(resume="abc123..."))
    → Claude continues with full prior context
```

## Key Concepts

### session_id

The SDK returns a `session_id` on `ResultMessage`. This is a UUID that
identifies the conversation on Anthropic's servers. Pass it back via
`options.resume` to continue where you left off.

```python
from claude_code_sdk import ClaudeCodeOptions, ResultMessage, query

# Start
async for msg in query(prompt="Start a long task...", options=opts):
    if isinstance(msg, ResultMessage) and msg.session_id:
        session_id = msg.session_id  # save this

# Resume (later, or after a crash)
opts = ClaudeCodeOptions(resume=session_id)
async for msg in query(prompt="Continue...", options=opts):
    ...
```

### SessionStore

A thin JSON wrapper that saves sessions to disk with metadata:

```python
store = SessionStore("sessions.json")

# Save after a run
store.save(SavedSession(session_id="abc...", task="Write a report"))

# Find active sessions
active = store.list_active()

# Mark done when complete
store.mark_completed("abc...", summary="Report written to report.md")
```

### ResumableAgent

A higher-level wrapper that handles the session ID lifecycle:

```python
agent = ResumableAgent()

# First run — returns (session_id, response)
sid, response = await agent.run("Analyze my Python files and list issues")
# session_id is auto-saved to sessions.json

# Later, resume with a follow-up
sid, response = await agent.resume(sid, "Fix the top 3 issues you found")
```

## When to Use This

- **Long analysis tasks**: "Analyze 50 files and write a report" — may time out
- **Multi-step workflows**: "Step 1: gather data. Step 2: analyze. Step 3: report"
- **User-interactive loops**: store sessions between script invocations
- **Retry on failure**: if your script crashes, load the session and resume

## When NOT to Use This

- Short, atomic queries that complete in one shot
- Tasks where you want Claude to start fresh each time
- When context window limits matter (resuming a very long session can be expensive)

## Pitfalls

**Sessions expire**: Anthropic retains session context for a limited time (typically
hours to days). Don't assume you can resume a session from last week.

**session_id rotation**: After resuming, the new `ResultMessage.session_id` may differ
from the one you resumed from. Always update your stored ID with the latest one.

**No session_id returned**: If Claude returns no `session_id`, the response came from
a context that doesn't support resumption (e.g., cached short responses). Handle this
gracefully.

## Usage

```bash
# First run — starts a task and saves session ID
python recipe.py

# Second run — resumes the saved session
python recipe.py
```
