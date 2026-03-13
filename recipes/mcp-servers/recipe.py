"""
MCP Servers — Claude Code SDK Recipe

Pattern: configure HTTP and stdio MCP servers so agents can use
external tools like GitHub, Playwright, filesystems, and custom servers.

Run:
    python recipe.py

Requires:
    ANTHROPIC_API_KEY environment variable
    For live GitHub example: GITHUB_TOKEN environment variable
"""

import asyncio
import logging
import os
from typing import Any

from claude_code_sdk import AssistantMessage, ClaudeCodeOptions, TextBlock, query
from claude_code_sdk.types import McpHttpServerConfig, McpStdioServerConfig

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-5"


# ── MCP Server Configurations ─────────────────────────────────────────────────

def github_mcp_server(token: str | None = None) -> McpHttpServerConfig:
    """
    GitHub MCP server — HTTP transport.

    Provides tools: search_repositories, get_file_contents, list_issues, etc.

    Args:
        token: GitHub personal access token. Falls back to GITHUB_TOKEN env var.

    Returns:
        McpHttpServerConfig dict ready for use in ClaudeCodeOptions.mcp_servers.
    """
    resolved_token = token or os.environ.get("GITHUB_TOKEN") or ""
    if not resolved_token:
        logger.warning("No GITHUB_TOKEN found — GitHub MCP server will be unauthenticated")

    return McpHttpServerConfig(
        type="http",
        url="https://api.githubcopilot.com/mcp/",
        headers={
            "Authorization": f"Bearer {resolved_token}",
            "Content-Type": "application/json",
        },
    )


def playwright_mcp_server() -> McpStdioServerConfig:
    """
    Playwright MCP server — stdio transport.

    Provides tools: browser_navigate, browser_click, browser_snapshot, etc.

    Requirements: npm install -g @playwright/mcp

    Returns:
        McpStdioServerConfig dict ready for use in ClaudeCodeOptions.mcp_servers.
    """
    return McpStdioServerConfig(
        command="npx",
        args=["@playwright/mcp", "--headless"],
    )


def filesystem_mcp_server(allowed_dirs: list[str] | None = None) -> McpStdioServerConfig:
    """
    Filesystem MCP server — stdio transport.

    Provides tools: read_file, write_file, list_directory, search_files.

    Requirements: npm install -g @modelcontextprotocol/server-filesystem

    Args:
        allowed_dirs: Directories the agent may access. Defaults to cwd.
    """
    dirs = allowed_dirs or [os.getcwd()]
    return McpStdioServerConfig(
        command="npx",
        args=["@modelcontextprotocol/server-filesystem", *dirs],
    )


def custom_stdio_server(
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> McpStdioServerConfig:
    """
    Generic stdio MCP server builder.

    Args:
        command: Executable to run (e.g., "python", "node").
        args: Arguments to the command.
        env: Environment variables for the server process.
    """
    config: McpStdioServerConfig = {"command": command}
    if args:
        config["args"] = args
    if env:
        config["env"] = env
    return config


# ── Building Options ──────────────────────────────────────────────────────────

def build_options(
    servers: dict[str, McpHttpServerConfig | McpStdioServerConfig],
    *,
    model: str = DEFAULT_MODEL,
    system: str | None = None,
    allowed_tools: list[str] | None = None,
) -> ClaudeCodeOptions:
    """
    Build ClaudeCodeOptions with MCP servers configured.

    Args:
        servers: Dict mapping server name → config (name used in tool calls).
        model: Claude model to use.
        system: Optional system prompt.
        allowed_tools: Restrict which tools the model may call.
                       Format: ["server_name__tool_name"].

    Returns:
        ClaudeCodeOptions ready to pass to query().

    Example:
        opts = build_options(
            {"github": github_mcp_server()},
            allowed_tools=["github__search_repositories"],
        )
        async for msg in query(prompt="...", options=opts):
            ...
    """
    options = ClaudeCodeOptions(model=model, mcp_servers=servers)
    if system:
        options.system_prompt = system
    if allowed_tools:
        options.allowed_tools = allowed_tools
    return options


# ── Demo ──────────────────────────────────────────────────────────────────────

async def demo_no_mcp() -> None:
    """Baseline — plain query with no MCP servers."""
    print("1. Baseline query (no MCP servers):")
    full_text = ""
    async for message in query(
        prompt="List 3 common uses of MCP servers in one sentence each.",
        options=ClaudeCodeOptions(model=DEFAULT_MODEL),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    full_text += block.text

    if full_text:
        for line in full_text.strip().split("\n")[:5]:
            print(f"   {line}")
    print()


async def demo_github_mcp() -> None:
    """Show GitHub MCP server usage (requires GITHUB_TOKEN)."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("2. GitHub MCP demo: skipped (set GITHUB_TOKEN to run)")
        print()
        return

    print("2. GitHub MCP query (listing top Python repos):")
    options = build_options(
        {"github": github_mcp_server(token)},
        system="Use only the provided GitHub tools. Be concise.",
        allowed_tools=["github__search_repositories"],
    )

    full_text = ""
    try:
        async for message in query(
            prompt="Find the top 3 most-starred Python repositories on GitHub.",
            options=options,
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        full_text += block.text

        if full_text:
            print(f"   {full_text.strip()[:300]}")
    except Exception as exc:
        print(f"   Error: {exc}")
    print()


async def demo_config_patterns() -> None:
    """Show common configuration patterns without making API calls."""
    print("3. Configuration patterns (dry-run — no API calls):")

    # Pattern A: GitHub + filesystem, tool-restricted
    opts_a = build_options(
        {
            "github": github_mcp_server(),
            "fs": filesystem_mcp_server(["/tmp"]),
        },
        model="claude-sonnet-4-5",
        system="You are a code review assistant.",
        allowed_tools=[
            "github__get_file_contents",
            "github__list_pull_requests",
            "fs__read_file",
        ],
    )
    print(f"   Pattern A — {len(opts_a.mcp_servers)} servers, {len(opts_a.allowed_tools)} allowed tools")

    # Pattern B: Playwright for browser automation
    opts_b = build_options(
        {"playwright": playwright_mcp_server()},
        model="claude-sonnet-4-5",
        system="You are a web automation agent.",
    )
    print(f"   Pattern B — {len(opts_b.mcp_servers)} server, model: {opts_b.model}")

    # Pattern C: Custom stdio server
    opts_c = build_options(
        {"my-tools": custom_stdio_server("python", ["-m", "my_mcp_server"])},
    )
    server_config = opts_c.mcp_servers["my-tools"]
    print(f"   Pattern C — custom stdio: {server_config['command']} {server_config.get('args', [])}")
    print()


async def main() -> None:
    """Demonstrate MCP server configuration patterns."""
    print("=== MCP Servers Demo ===\n")

    await demo_no_mcp()
    await demo_github_mcp()
    await demo_config_patterns()

    print("Done. Pass mcp_servers={name: config} in ClaudeCodeOptions.")


if __name__ == "__main__":
    asyncio.run(main())
