# Recipe: Scheduled Agents

Run Claude agent tasks on cron schedules using pure asyncio — no external
scheduler required.

## The Problem

You want agents to run automatically: a morning briefing at 9am, a health
check every hour, a weekly digest on Fridays. Options like APScheduler add
dependencies. System cron can't easily run async Python. This recipe shows
how to build a lightweight scheduler directly with asyncio that wakes at
minute boundaries and fires tasks whose cron expressions match.

## How It Works

```
Every minute:
  1. Wake at the top of the minute
  2. Check which tasks' cron expressions match now()
  3. Fire all matching tasks concurrently with asyncio.gather()
  4. Each task runs an independent Claude query
  5. Sleep until the next minute
```

## Key Concepts

### Cron expressions

Standard 5-field cron: `minute hour day-of-month month day-of-week`

```python
"0 8 * * 1-5"   # 8am on weekdays
"*/15 * * * *"  # every 15 minutes
"0 0 * * 0"     # midnight on Sundays
"@daily"        # alias for "0 0 * * *"
"@hourly"       # alias for "0 * * * *"
```

### ScheduledTask

```python
task = ScheduledTask(
    name="morning-briefing",
    schedule="30 9 * * 1-5",   # 9:30am weekdays
    prompt="List today's three highest priorities.",
    system="You are a productivity coach.",
    model="claude-haiku-4-5",   # use cheaper model for scheduled tasks
    post_hook=send_to_signal,   # async callable invoked with response text
)
```

### AgentScheduler

```python
scheduler = AgentScheduler(tasks=[task1, task2, task3])
await scheduler.run()   # runs forever; cancel to stop
```

Fire a task manually (for testing):
```python
response = await scheduler.run_now("morning-briefing")
```

### Config-driven setup

Load tasks from a config file instead of hardcoding:

```python
config = json.loads(Path("schedule.json").read_text())
tasks = load_tasks_from_config(config)
scheduler = AgentScheduler(tasks=tasks)
```

## Adding a post_hook

The most useful pattern: after the agent responds, send it somewhere.

```python
async def notify(response: str) -> None:
    # Send to Signal, Slack, email, write to file, etc.
    print(f"[Notification] {response[:200]}")

task = ScheduledTask(
    name="daily-report",
    schedule="0 18 * * 1-5",
    prompt="Write a 3-sentence end-of-day summary.",
    post_hook=notify,
)
```

## Running the Scheduler as a Service

**macOS launchd** (`~/Library/LaunchAgents/com.myapp.scheduler.plist`):
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "...">
<plist version="1.0">
<dict>
  <key>Label</key>    <string>com.myapp.scheduler</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/path/to/recipe.py</string>
  </array>
  <key>RunAtLoad</key> <true/>
  <key>KeepAlive</key> <true/>
</dict>
</plist>
```

Load with: `launchctl load ~/Library/LaunchAgents/com.myapp.scheduler.plist`

**Linux systemd** (`/etc/systemd/system/agent-scheduler.service`):
```ini
[Unit]
Description=Agent Scheduler

[Service]
ExecStart=/usr/bin/python3 /path/to/recipe.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Pitfalls

**Missed ticks**: If the scheduler is down when a task was due, it won't
catch up. For critical tasks, check `task._last_run` at startup and run
missed tasks.

**Rate limits**: Running many tasks at the same minute can hit rate limits.
Use `max_concurrency` or stagger schedules by a few minutes.

**Cost awareness**: Each task run is a full Claude query. Use cheaper models
(`claude-haiku-4-5`) for high-frequency tasks and the `enabled=False` flag
to disable during development.

## Usage

```bash
python recipe.py
```
