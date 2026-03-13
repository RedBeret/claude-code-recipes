# claude-code-recipes

![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
[![CI](https://github.com/RedBeret/claude-code-recipes/actions/workflows/ci.yml/badge.svg)](https://github.com/RedBeret/claude-code-recipes/actions/workflows/ci.yml)

Tested, production-ready patterns for building with [Claude Code SDK](https://github.com/anthropics/claude-code-sdk-python).

Claude Code SDK is the right foundation for AI agents — but the common patterns
(rate limit handling, conversation memory, MCP integration, parallelism) require
boilerplate that isn't obvious from the docs. These recipes distill what works.

## Recipes

| Recipe | Pattern | Use When |
|--------|---------|----------|
| [rate-limit-fallback](recipes/rate-limit-fallback/) | Retry with backoff, model fallback | Production agents that can't afford downtime |
| [conversation-memory](recipes/conversation-memory/) | SQLite-backed message history | Stateful assistants, support bots |
| [mcp-servers](recipes/mcp-servers/) | HTTP and stdio MCP configuration | Agents that need external tools (GitHub, browser) |
| [parallel-tasks](recipes/parallel-tasks/) | Concurrent tasks with rate limit guard | Batch processing, repo analysis pipelines |

More recipes coming in v0.2.0: session management, scheduled agents, Signal integration, encrypted stores.

## Prerequisites

```bash
pip install claude-code-sdk anthropic
```

You need a valid Claude API key in your environment:

```bash
export ANTHROPIC_API_KEY=your-key-here
```

Most recipes work with the default model (`claude-sonnet-4-5`). Adjust
`DEFAULT_MODEL` at the top of each `recipe.py` to match your tier.

## Quick Start

Each recipe is a standalone Python file. Clone the repo and run:

```bash
git clone https://github.com/RedBeret/claude-code-recipes.git
cd claude-code-recipes
pip install -e ".[dev]"

# Run a recipe
python recipes/rate-limit-fallback/recipe.py
```

## Structure

```
claude-code-recipes/
├── recipes/
│   ├── rate-limit-fallback/
│   │   ├── recipe.py      # standalone implementation
│   │   └── README.md      # pattern explanation
│   ├── conversation-memory/
│   │   ├── recipe.py
│   │   └── README.md
│   ├── mcp-servers/
│   │   ├── recipe.py
│   │   └── README.md
│   └── parallel-tasks/
│       ├── recipe.py
│       └── README.md
└── tests/
    └── test_imports.py    # verifies all recipes import cleanly
```

## Philosophy

- **Standalone.** Each recipe runs with `python recipe.py`. No shared library code.
- **Real.** These patterns come from a production agent system. Not toy examples.
- **Minimal deps.** Only `claude-code-sdk` and `anthropic`. stdlib for everything else.
- **Copy-paste ready.** Grab a recipe, drop it in your project, adapt as needed.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
