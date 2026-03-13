# Recipe: MCP Servers

**Pattern:** Configure HTTP and stdio MCP servers so agents can use external tools.

## What Are MCP Servers?

Model Context Protocol (MCP) servers expose tools that Claude can call during
a conversation. Instead of writing custom function calling code, you attach
one or more MCP servers and Claude knows how to use them.

Two transport types:
- **HTTP** — server runs independently, communicate via HTTP (e.g., GitHub's hosted MCP)
- **stdio** — server is a child process, communicate via stdin/stdout (e.g., Playwright, filesystem)

## Available Servers

| Server | Type | Tools | Install |
|--------|------|-------|---------|
| GitHub | HTTP | search repos, read files, manage issues/PRs | no install — hosted |
| Playwright | stdio | navigate, click, screenshot, extract text | `npm install -g @playwright/mcp` |
| Filesystem | stdio | read/write files, list dirs | `npm install -g @modelcontextprotocol/server-filesystem` |
| Fetch | stdio | HTTP requests | `npm install -g @modelcontextprotocol/server-fetch` |

More at [modelcontextprotocol.io](https://modelcontextprotocol.io).

## HTTP Server Setup

```python
from claude_code_sdk import MCPServerHTTP

github = MCPServerHTTP(
    url="https://api.githubcopilot.com/mcp/",
    headers={
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Content-Type": "application/json",
    },
)

async for message in query(
    prompt="Find the top Python ML repos",
    options={"model": "claude-sonnet-4-5", "mcp_servers": [github]},
):
    ...
```

## stdio Server Setup

```python
from claude_code_sdk import MCPServerStdio

playwright = MCPServerStdio(
    command="npx",
    args=["@playwright/mcp", "--headless"],
)

async for message in query(
    prompt="Take a screenshot of example.com",
    options={"model": "claude-sonnet-4-5", "mcp_servers": [playwright]},
):
    ...
```

The SDK launches the server process and manages the stdin/stdout pipe.
The process is terminated when the query completes.

## Multiple Servers

Attach multiple servers in one call:

```python
options = {
    "model": "claude-sonnet-4-5",
    "mcp_servers": [github, playwright, filesystem],
}
```

Claude sees all tools from all servers. Use `allowed_tools` to limit scope.

## Tool Filtering

Restrict which tools the agent can call. This limits blast radius and prevents
the model from doing things you didn't intend.

```python
options = {
    "model": "claude-sonnet-4-5",
    "mcp_servers": [github],
    # Only allow reading — no writing or creating
    "allowed_tools": [
        "github__search_repositories",
        "github__get_file_contents",
        "github__list_issues",
    ],
}
```

Tool names follow the pattern `{server_name}__{tool_name}`.
List available tools by calling the server directly or checking its docs.

## Auth Token Best Practices

**Never hardcode tokens.** Resolve them at runtime:

```python
# Option 1: environment variable
token = os.environ.get("GITHUB_TOKEN")

# Option 2: macOS Keychain
import subprocess
result = subprocess.run(
    ["security", "find-generic-password", "-s", "github-token", "-w"],
    capture_output=True, text=True
)
token = result.stdout.strip() if result.returncode == 0 else None

# Option 3: gh CLI keyring (if gh is authenticated)
result = subprocess.run(
    ["gh", "auth", "token"],
    capture_output=True, text=True
)
token = result.stdout.strip()
```

## Custom stdio Servers

Build your own MCP server in Python or any language:

```python
# Simple Python MCP server skeleton
# See: https://github.com/modelcontextprotocol/python-sdk

from mcp.server import Server
from mcp.server.stdio import stdio_server

server = Server("my-tools")

@server.tool()
async def my_tool(arg: str) -> str:
    """Does something useful."""
    return f"Result: {arg}"

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
```

Run it as:
```python
custom = MCPServerStdio(command="python", args=["my_server.py"])
```

## Pitfalls

**stdio servers add latency.** Each query launches a new process. For
high-frequency calls, consider keeping the process alive (the SDK may do
this automatically — check the version you're using).

**HTTP servers can be slow or unavailable.** Add timeouts and fallback
behavior for production agents.

**Tool names change.** MCP servers are versioned. Pin the version in your
npm install and re-test when upgrading.

**Not all servers need all permissions.** Principle of least privilege:
give the agent only the tools it needs for the specific task.

## Running the Recipe

```bash
# Baseline + config patterns (no extra setup needed):
python recipe.py

# With GitHub tools:
export GITHUB_TOKEN=$(gh auth token)
python recipe.py
```
