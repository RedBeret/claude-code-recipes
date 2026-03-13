# Recipe: Signal Integration

A complete Signal bot with a Claude backend. Receives messages, runs them
through Claude, and replies — with an allowlist for security.

## The Problem

Signal is end-to-end encrypted and works offline, making it a better choice
for a personal AI assistant than SMS or Slack. This recipe shows the full
loop: poll for messages → check allowlist → query Claude → send reply.

## Prerequisites

**signal-cli** must be installed and your account registered:

```bash
# macOS
brew install signal-cli

# Or download from https://github.com/AsamK/signal-cli/releases

# Register your number (one-time):
signal-cli -a +12065551234 register
signal-cli -a +12065551234 verify 123456  # verification code from SMS

# Start the daemon (recommended):
signal-cli -a +12065551234 daemon --http
```

## How It Works

```
Poll (every 5s):
  signal-cli receive → parse messages
  → check sender in allowlist
  → query Claude SDK
  → signal-cli send reply
```

Two transport modes:
- **Daemon** (preferred): connects to a running `signal-cli --http` daemon via JSON-RPC. Faster, no subprocess overhead.
- **CLI** (fallback): invokes `signal-cli` directly for each receive/send. Slower but works without a daemon.

The transport auto-falls back from daemon to CLI if the daemon isn't available.

## Key Concepts

### Allowlist

Only phone numbers in `config.allowlist` get responses. Anyone else is
silently ignored. This prevents the bot from responding to strangers.

```python
config = SignalConfig(
    account="+12065551234",
    allowlist=["+12065559999"],  # only respond to this number
)
```

### SignalBot

```python
bot = SignalBot(
    config=config,
    model="claude-sonnet-4-5",
    system_prompt="You are a helpful assistant. Keep replies under 3 sentences.",
)

# Run continuously
await bot.run(poll_interval=5.0)

# Or poll once (useful in a scheduler)
processed = await bot.run_once()
```

### Sending notifications (no loop)

Send a one-off message from other code:

```python
from recipe import SignalConfig, send_notification

config = SignalConfig.from_env()
send_notification(config, recipient="+12065559999", message="Task complete!")
```

### Combining with AgentScheduler

```python
async def morning_brief_hook(response: str) -> None:
    send_notification(config, recipient="+12065559999", message=response)

task = ScheduledTask(
    name="morning-briefing",
    schedule="30 9 * * 1-5",
    prompt="Give me a 3-point morning briefing.",
    post_hook=morning_brief_hook,
)
```

## Environment Variables

```bash
export SIGNAL_ACCOUNT="+12065551234"
export SIGNAL_ALLOWLIST="+12065559999,+12065558888"
export ANTHROPIC_API_KEY="your-key"
```

## Running as a Service

**macOS launchd** (`~/Library/LaunchAgents/com.signal-bot.plist`):
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "...">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.signal-bot</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/path/to/recipe.py</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>SIGNAL_ACCOUNT</key><string>+12065551234</string>
    <key>SIGNAL_ALLOWLIST</key><string>+12065559999</string>
    <key>ANTHROPIC_API_KEY</key><string>your-key</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
```

## Pitfalls

**Never put your real phone number in code.** Use environment variables. The
signal-cli account number is personally identifying information.

**Message deduplication**: The bot tracks handled `sender:timestamp` pairs in
memory. Restart clears this, so brief duplicates are possible. For
production, persist handled IDs in SQLite.

**Rate limits**: Claude SDK rate limits apply per API key. If you get many
messages at once, they'll queue up. The bot processes them sequentially.

**Long responses**: Signal has a ~2000 character message limit. The transport
truncates at 1600 characters. Tell Claude to keep replies concise in the
system prompt.

## Usage

```bash
export SIGNAL_ACCOUNT="+12065551234"
export SIGNAL_ALLOWLIST="+12065559999"
python recipe.py
```
