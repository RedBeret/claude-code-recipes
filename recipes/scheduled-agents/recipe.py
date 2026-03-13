"""
Scheduled Agents — Claude Code SDK Recipe

Pattern: run agent tasks on cron-like schedules using asyncio.
Define tasks in a config dict; the scheduler fires them at the right time.

Run:
    python recipe.py

Requires:
    ANTHROPIC_API_KEY environment variable
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

from claude_code_sdk import AssistantMessage, ClaudeCodeOptions, TextBlock, query

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5"


# ── Cron parsing ──────────────────────────────────────────────────────────────

CRON_ALIASES: dict[str, str] = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
}


def _parse_field(field_str: str, lo: int, hi: int) -> set[int]:
    """
    Parse one cron field into a set of matching integers.

    Supports: * (all), N (exact), N-M (range), */N (step), N-M/N (range+step),
    and comma-separated combinations.
    """
    values: set[int] = set()
    for part in field_str.split(","):
        part = part.strip()
        if "/" in part:
            range_part, step_str = part.rsplit("/", 1)
            step = int(step_str)
            if range_part == "*":
                rng = range(lo, hi + 1, step)
            elif "-" in range_part:
                a, b = range_part.split("-")
                rng = range(int(a), int(b) + 1, step)
            else:
                rng = range(int(range_part), hi + 1, step)
            values.update(rng)
        elif part == "*":
            values.update(range(lo, hi + 1))
        elif "-" in part:
            a, b = part.split("-")
            values.update(range(int(a), int(b) + 1))
        else:
            values.add(int(part))
    return values


def cron_matches(expr: str, dt: datetime) -> bool:
    """
    Return True if the cron expression matches the given datetime.

    Supports standard 5-field cron: minute hour day-of-month month day-of-week
    and @hourly / @daily / @weekly / @monthly aliases.
    """
    expr = CRON_ALIASES.get(expr, expr)
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression (need 5 fields): {expr!r}")

    minute_f, hour_f, dom_f, month_f, dow_f = parts

    minutes = _parse_field(minute_f, 0, 59)
    hours = _parse_field(hour_f, 0, 23)
    doms = _parse_field(dom_f, 1, 31)
    months = _parse_field(month_f, 1, 12)
    dows = _parse_field(dow_f, 0, 6)  # 0 = Sunday, 1 = Monday, ..., 6 = Saturday

    # Convert Python's weekday (Mon=0 .. Sun=6) to cron DOW (Sun=0, Mon=1 .. Sat=6)
    cron_dow = (dt.weekday() + 1) % 7

    return (
        dt.minute in minutes
        and dt.hour in hours
        and dt.day in doms
        and dt.month in months
        and cron_dow in dows
    )


def seconds_until_next_minute(now: datetime | None = None) -> float:
    """Return seconds until the start of the next minute."""
    now = now or datetime.now(tz=timezone.utc)
    return 60.0 - now.second - now.microsecond / 1_000_000


# ── Task definitions ──────────────────────────────────────────────────────────

AgentHook = Callable[[], Coroutine[Any, Any, None]]


@dataclass
class ScheduledTask:
    """
    A task that fires on a cron schedule and runs an agent prompt.

    Attributes:
        name:        Human-readable name for logging.
        schedule:    Cron expression ("*/5 * * * *") or alias ("@hourly").
        prompt:      Prompt sent to the agent.
        system:      System prompt for the agent (optional).
        model:       Override model for this task (optional).
        pre_hook:    Async function called before the agent query (optional).
        post_hook:   Async function called with the response text (optional).
        enabled:     Set to False to disable without removing from the list.
    """

    name: str
    schedule: str
    prompt: str
    system: str = "You are a concise assistant."
    model: str = DEFAULT_MODEL
    pre_hook: AgentHook | None = None
    post_hook: Callable[[str], Coroutine[Any, Any, None]] | None = None
    enabled: bool = True
    _last_run: float = field(default=0.0, init=False, repr=False)

    def matches(self, dt: datetime) -> bool:
        return self.enabled and cron_matches(self.schedule, dt)


# ── Runner ────────────────────────────────────────────────────────────────────


class AgentScheduler:
    """
    Runs ScheduledTasks on their cron schedules.

    The scheduler wakes up at the start of each minute, checks which tasks
    are due, and fires them concurrently. Each task runs an independent
    Claude query so they don't share context.

    Usage:
        scheduler = AgentScheduler(tasks=[...])
        await scheduler.run()
    """

    def __init__(
        self,
        tasks: list[ScheduledTask],
        tick_interval: float = 60.0,
    ) -> None:
        self.tasks = tasks
        self.tick_interval = tick_interval
        self._running = False
        self._run_count = 0

    async def run(self) -> None:
        """Start the scheduler loop. Runs until cancelled."""
        self._running = True
        logger.info("Scheduler started with %d task(s)", len(self.tasks))

        while self._running:
            now = datetime.now(tz=timezone.utc)
            due = [t for t in self.tasks if t.matches(now)]

            if due:
                logger.info(
                    "Tick %s: %d task(s) due: %s",
                    now.strftime("%H:%M"),
                    len(due),
                    [t.name for t in due],
                )
                await asyncio.gather(*[self._fire(task) for task in due])

            # Sleep until the top of the next minute
            sleep_for = seconds_until_next_minute()
            await asyncio.sleep(sleep_for)

    def stop(self) -> None:
        self._running = False

    async def run_now(self, task_name: str) -> str | None:
        """Manually fire a named task. Returns the response text."""
        task = next((t for t in self.tasks if t.name == task_name), None)
        if not task:
            logger.warning("Task not found: %s", task_name)
            return None
        return await self._fire(task)

    async def _fire(self, task: ScheduledTask) -> str:
        """Execute one scheduled task."""
        if task.pre_hook:
            await task.pre_hook()

        logger.info("[%s] Running...", task.name)
        start = time.monotonic()
        text_parts: list[str] = []

        try:
            options = ClaudeCodeOptions(
                model=task.model,
                system_prompt=task.system,
            )
            async for message in query(prompt=task.prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
        except Exception as exc:
            logger.error("[%s] Failed: %s", task.name, exc)
            return ""

        elapsed = time.monotonic() - start
        response = "\n".join(text_parts)
        task._last_run = time.time()
        self._run_count += 1

        logger.info("[%s] Done in %.1fs", task.name, elapsed)

        if task.post_hook:
            await task.post_hook(response)

        return response


# ── Helpers ───────────────────────────────────────────────────────────────────


def load_tasks_from_config(config: list[dict[str, Any]]) -> list[ScheduledTask]:
    """
    Build ScheduledTask objects from a list of dicts.

    Each dict should have: name, schedule, prompt.
    Optional: system, model, enabled.

    Example config:
        [
            {
                "name": "morning-briefing",
                "schedule": "0 8 * * 1-5",
                "prompt": "Summarize today's key priorities in 3 bullets.",
                "system": "You are a productivity coach.",
            }
        ]
    """
    tasks = []
    for item in config:
        tasks.append(
            ScheduledTask(
                name=item["name"],
                schedule=item["schedule"],
                prompt=item["prompt"],
                system=item.get("system", "You are a concise assistant."),
                model=item.get("model", DEFAULT_MODEL),
                enabled=item.get("enabled", True),
            )
        )
    return tasks


# ── Demo ──────────────────────────────────────────────────────────────────────

EXAMPLE_TASKS = [
    {
        "name": "hourly-health-check",
        "schedule": "@hourly",
        "prompt": "Reply with exactly one sentence: 'Agent is healthy at HH:MM.' using the current time.",
        "system": "You are a monitoring agent. Be brief.",
    },
    {
        "name": "daily-standup",
        "schedule": "30 9 * * 1-5",  # 09:30 on weekdays
        "prompt": "Generate a 3-point daily standup reminder for a software team.",
        "system": "You are a team assistant. Be concise and actionable.",
    },
    {
        "name": "every-5-min-demo",
        "schedule": "*/5 * * * *",
        "prompt": "Say: 'Five-minute task complete.'",
        "system": "You are a demo agent.",
    },
]


async def demo() -> None:
    """
    Demo: run the 'every-5-min-demo' task immediately (without waiting for cron).
    """
    tasks = load_tasks_from_config(EXAMPLE_TASKS)
    scheduler = AgentScheduler(tasks=tasks)

    print("Running 'every-5-min-demo' immediately (not waiting for cron)...")
    response = await scheduler.run_now("every-5-min-demo")
    print(f"Response: {response}\n")

    # Show which tasks would fire right now
    now = datetime.now(tz=timezone.utc)
    due = [t for t in tasks if cron_matches(t.schedule, now)]
    print(f"Tasks due right now ({now.strftime('%H:%M UTC')}): {[t.name for t in due] or '(none)'}")

    print("\nTo run the full scheduler (waits for minute boundaries):")
    print("  await scheduler.run()")


if __name__ == "__main__":
    asyncio.run(demo())
