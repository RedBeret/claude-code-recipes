"""
Signal Integration — Claude Code SDK Recipe

Pattern: a Signal bot that receives messages, queries Claude, and sends replies.
Shows the full loop: receive → allowlist check → Claude query → send reply.

Run:
    python recipe.py

Requires:
    ANTHROPIC_API_KEY environment variable
    signal-cli running as a daemon (see README for setup)
    SIGNAL_ACCOUNT — your phone number (e.g. +12065551234)
    SIGNAL_ALLOWLIST — comma-separated allowed numbers (e.g. +12065559999)
"""

import asyncio
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from claude_code_sdk import AssistantMessage, ClaudeCodeOptions, TextBlock, query

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-5"


# ── Config ────────────────────────────────────────────────────────────────────


@dataclass
class SignalConfig:
    """
    Configuration for the Signal bot.

    account:    Your Signal phone number (registered with signal-cli).
    allowlist:  Only respond to these numbers. Empty = respond to nobody.
    daemon_url: JSON-RPC URL for signal-cli daemon (default local port).
    poll_timeout: Seconds to wait for incoming messages per receive call.
    """

    account: str
    allowlist: list[str]
    daemon_url: str = "http://127.0.0.1:19756"
    poll_timeout: int = 5

    @classmethod
    def from_env(cls) -> "SignalConfig":
        account = os.environ.get("SIGNAL_ACCOUNT", "")
        allowlist_raw = os.environ.get("SIGNAL_ALLOWLIST", "")
        allowlist = [n.strip() for n in allowlist_raw.split(",") if n.strip()]
        return cls(account=account, allowlist=allowlist)


# ── Transport ─────────────────────────────────────────────────────────────────


@dataclass
class IncomingMessage:
    """A received Signal message."""

    sender: str
    text: str
    timestamp: int
    group_id: str | None = None


class SignalTransport:
    """
    Send and receive Signal messages via signal-cli.

    Two modes:
    - daemon (preferred): signal-cli running as a JSON-RPC daemon
    - cli (fallback): invoke signal-cli directly each time
    """

    def __init__(self, config: SignalConfig) -> None:
        self.config = config
        self._use_daemon = True

    def send(self, recipient: str, message: str) -> bool:
        """Send a message. Returns True on success."""
        if len(message) > 1600:
            message = message[:1597] + "..."

        if self._use_daemon:
            return self._send_daemon(recipient, message)
        return self._send_cli(recipient, message)

    def receive(self) -> list[IncomingMessage]:
        """Poll for incoming messages."""
        if self._use_daemon:
            return self._receive_daemon()
        return self._receive_cli()

    def _send_daemon(self, recipient: str, message: str) -> bool:
        payload = {
            "jsonrpc": "2.0",
            "method": "send",
            "params": {
                "account": self.config.account,
                "message": message,
                "recipient": [recipient],
            },
            "id": int(time.time() * 1000),
        }
        try:
            import urllib.request

            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                f"{self.config.daemon_url}/api/v1/rpc",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                return "error" not in result
        except Exception as exc:
            logger.warning("Daemon send failed: %s — trying CLI", exc)
            self._use_daemon = False
            return self._send_cli(recipient, message)

    def _send_cli(self, recipient: str, message: str) -> bool:
        try:
            result = subprocess.run(
                [
                    "signal-cli",
                    "-a", self.config.account,
                    "send",
                    "-m", message,
                    recipient,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode == 0
        except Exception as exc:
            logger.error("CLI send failed: %s", exc)
            return False

    def _receive_daemon(self) -> list[IncomingMessage]:
        payload = {
            "jsonrpc": "2.0",
            "method": "receive",
            "params": {
                "account": self.config.account,
                "timeout": self.config.poll_timeout,
            },
            "id": int(time.time() * 1000),
        }
        try:
            import urllib.request

            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                f"{self.config.daemon_url}/api/v1/rpc",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.config.poll_timeout + 5) as resp:
                result = json.loads(resp.read())
                return self._parse_daemon_messages(result.get("result", []))
        except Exception as exc:
            logger.warning("Daemon receive failed: %s — trying CLI", exc)
            self._use_daemon = False
            return self._receive_cli()

    def _receive_cli(self) -> list[IncomingMessage]:
        try:
            result = subprocess.run(
                [
                    "signal-cli",
                    "--output=json",
                    "-a", self.config.account,
                    "receive",
                    "-t", str(self.config.poll_timeout),
                ],
                capture_output=True,
                text=True,
                timeout=self.config.poll_timeout + 10,
            )
            messages = []
            for line in result.stdout.strip().splitlines():
                if line.strip():
                    try:
                        messages.extend(self._parse_cli_message(json.loads(line)))
                    except json.JSONDecodeError:
                        pass
            return messages
        except Exception as exc:
            logger.error("CLI receive failed: %s", exc)
            return []

    def _parse_daemon_messages(self, results: list[dict]) -> list[IncomingMessage]:
        messages = []
        for item in results:
            envelope = item.get("envelope", {})
            data_msg = envelope.get("dataMessage", {})
            text = data_msg.get("message", "")
            sender = envelope.get("sourceNumber", "")
            if text and sender:
                messages.append(
                    IncomingMessage(
                        sender=sender,
                        text=text,
                        timestamp=int(time.time() * 1000),
                        group_id=data_msg.get("groupInfo", {}).get("groupId"),
                    )
                )
        return messages

    def _parse_cli_message(self, data: dict) -> list[IncomingMessage]:
        envelope = data.get("envelope", {})
        data_msg = envelope.get("dataMessage", {})
        text = data_msg.get("message", "")
        sender = envelope.get("sourceNumber", "")
        if text and sender:
            return [
                IncomingMessage(
                    sender=sender,
                    text=text,
                    timestamp=int(time.time() * 1000),
                )
            ]
        return []


# ── Bot ───────────────────────────────────────────────────────────────────────


class SignalBot:
    """
    A Signal bot powered by Claude.

    Receives messages, checks the sender against the allowlist,
    queries Claude, and replies.
    """

    def __init__(
        self,
        config: SignalConfig,
        model: str = DEFAULT_MODEL,
        system_prompt: str = "You are a helpful assistant responding via Signal. Keep replies concise.",
    ) -> None:
        self.config = config
        self.model = model
        self.system_prompt = system_prompt
        self.transport = SignalTransport(config)
        self._handled: set[str] = set()
        self._running = False

    def is_allowed(self, sender: str) -> bool:
        """Check if a sender is in the allowlist."""
        return sender in self.config.allowlist

    async def handle_message(self, msg: IncomingMessage) -> str:
        """Process one incoming message. Returns the response text."""
        logger.info("Message from %s: %s", msg.sender[:6] + "...", msg.text[:60])

        options = ClaudeCodeOptions(
            model=self.model,
            system_prompt=self.system_prompt,
        )

        text_parts: list[str] = []
        async for message in query(prompt=msg.text, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)

        response = "\n".join(text_parts)
        return response

    async def run_once(self) -> list[IncomingMessage]:
        """
        Poll for messages, process each allowed one, and reply.
        Returns the list of processed messages.
        """
        messages = self.transport.receive()
        processed = []

        for msg in messages:
            # Deduplicate
            dedup_key = f"{msg.sender}:{msg.timestamp}"
            if dedup_key in self._handled:
                continue
            self._handled.add(dedup_key)

            if not self.is_allowed(msg.sender):
                logger.info("Ignored message from unlisted sender: %s...", msg.sender[:6])
                continue

            try:
                response = await self.handle_message(msg)
                self.transport.send(msg.sender, response)
                processed.append(msg)
                logger.info("Replied to %s...", msg.sender[:6])
            except Exception as exc:
                logger.error("Failed to handle message from %s: %s", msg.sender[:6], exc)

        return processed

    async def run(self, poll_interval: float = 5.0) -> None:
        """Poll for messages in a loop. Runs until cancelled."""
        self._running = True
        logger.info("Signal bot started. Polling every %.0fs.", poll_interval)
        logger.info("Allowlist: %d number(s)", len(self.config.allowlist))

        while self._running:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Poll error: %s", exc)

            await asyncio.sleep(poll_interval)

    def stop(self) -> None:
        self._running = False


# ── Utilities ─────────────────────────────────────────────────────────────────


def send_notification(config: SignalConfig, recipient: str, message: str) -> bool:
    """
    Send a one-off notification without running the full bot loop.

    Useful for sending alerts from scheduled tasks or other code.
    """
    transport = SignalTransport(config)
    return transport.send(recipient, message)


# ── Demo ──────────────────────────────────────────────────────────────────────


async def demo() -> None:
    """
    Demo: show the bot configuration and explain how to run.
    Does NOT make Signal calls (requires signal-cli setup).
    """
    config = SignalConfig.from_env()

    print("Signal Bot Configuration:")
    print(f"  Account:   {config.account or '(not set — set SIGNAL_ACCOUNT)'}")
    print(f"  Allowlist: {len(config.allowlist)} number(s)")
    print(f"  Daemon URL: {config.daemon_url}")
    print()

    bot = SignalBot(
        config=config,
        system_prompt="You are a concise assistant. Reply in 1-3 sentences.",
    )

    if not config.account:
        print("Set SIGNAL_ACCOUNT and SIGNAL_ALLOWLIST to run the bot.")
        print()
        print("Example:")
        print("  export SIGNAL_ACCOUNT='+12065551234'")
        print("  export SIGNAL_ALLOWLIST='+12065559999'")
        print("  python recipe.py")
        return

    print("Starting bot (Ctrl+C to stop)...")
    try:
        await bot.run(poll_interval=5.0)
    except KeyboardInterrupt:
        bot.stop()
        print("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(demo())
