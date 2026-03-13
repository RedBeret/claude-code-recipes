# claude-code-recipes

![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
[![CI](https://github.com/RedBeret/claude-code-recipes/actions/workflows/ci.yml/badge.svg)](https://github.com/RedBeret/claude-code-recipes/actions/workflows/ci.yml)

Tested, production-ready patterns for building with [Claude Code SDK](https://github.com/anthropics/claude-code-sdk-python).

Claude Code SDK is the right foundation for AI agents — but the common patterns
(rate limit handling, conversation memory, MCP integration, parallelism) require
boilerplate that isn't obvious from the docs. These recipes distill what works.

## Recipes

### Core Patterns

| Recipe | Pattern | Use When |
|--------|---------|----------|
| [rate-limit-fallback](recipes/rate-limit-fallback/) | Retry with backoff, model fallback chain | Production agents that can't afford downtime |
| [conversation-memory](recipes/conversation-memory/) | SQLite-backed message history injection | Stateful assistants, support bots, chat apps |
| [mcp-servers](recipes/mcp-servers/) | HTTP and stdio MCP server configuration | Agents that need external tools (GitHub, browser) |
| [parallel-tasks](recipes/parallel-tasks/) | Concurrent tasks with rate limit guard | Batch processing, repo analysis pipelines |

### Advanced Patterns

| Recipe | Pattern | Use When |
|--------|---------|----------|
| [session-management](recipes/session-management/) | Save/resume agent sessions across restarts | Long-running tasks, multi-step workflows |
| [scheduled-agents](recipes/scheduled-agents/) | Cron-scheduled agent tasks via asyncio | Morning briefings, hourly health checks, periodic reports |
| [signal-integration](recipes/signal-integration/) | Signal bot with Claude backend | Private, encrypted AI assistant on your phone |
| [encrypted-stores](recipes/encrypted-stores/) | Fernet field-level encryption for SQLite | Agent data with sensitive content, API keys, personal info |

## Prerequisites

```bash
pip install claude-code-sdk anthropic cryptography
```

You need a valid Claude API key in your environment:

```bash
export ANTHROPIC_API_KEY=your-key-here
```

Most recipes work with `claude-sonnet-4-5`. Adjust `DEFAULT_MODEL` at the top
of each `recipe.py` to match your API tier. Scheduled and Signal recipes use
`claude-haiku-4-5` by default for cost efficiency.

## Quick Start

Each recipe is a standalone Python file. Clone the repo and run:

```bash
git clone https://github.com/RedBeret/claude-code-recipes.git
cd claude-code-recipes
pip install -e ".[dev]"

# Run any recipe
python recipes/rate-limit-fallback/recipe.py
python recipes/conversation-memory/recipe.py
python recipes/session-management/recipe.py
python recipes/encrypted-stores/recipe.py  # no API key needed
```

## Structure

```
claude-code-recipes/
├── recipes/
│   ├── rate-limit-fallback/     # Core: retry + model fallback
│   │   ├── recipe.py
│   │   └── README.md
│   ├── conversation-memory/     # Core: SQLite conversation history
│   │   ├── recipe.py
│   │   └── README.md
│   ├── mcp-servers/             # Core: MCP server configuration
│   │   ├── recipe.py
│   │   └── README.md
│   ├── parallel-tasks/          # Core: concurrent execution
│   │   ├── recipe.py
│   │   └── README.md
│   ├── session-management/      # Advanced: save/resume sessions
│   │   ├── recipe.py
│   │   └── README.md
│   ├── scheduled-agents/        # Advanced: cron scheduling
│   │   ├── recipe.py
│   │   └── README.md
│   ├── signal-integration/      # Advanced: Signal bot
│   │   ├── recipe.py
│   │   └── README.md
│   └── encrypted-stores/        # Advanced: field-level encryption
│       ├── recipe.py
│       └── README.md
└── tests/
    └── test_imports.py          # verifies all recipes import cleanly
```

## Philosophy

- **Standalone.** Each recipe runs with `python recipe.py`. No shared library code between recipes.
- **Real.** These patterns come from a production agent system. Not toy examples.
- **Minimal deps.** `claude-code-sdk`, `anthropic`, `cryptography` (encrypted-stores only). stdlib for everything else.
- **Copy-paste ready.** Grab a recipe, drop it in your project, adapt as needed.

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

All tests run without making API calls. They verify imports, data structures,
and logic that doesn't require a live Claude connection.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
