"""
Parallel Tasks — Claude Code SDK Recipe

Pattern: run multiple agent tasks concurrently using asyncio.gather
with a Semaphore to stay within API rate limits.

Run:
    python recipe.py

Requires:
    ANTHROPIC_API_KEY environment variable
"""

import asyncio
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar

from claude_code_sdk import AssistantMessage, ClaudeCodeOptions, TextBlock, query

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5"   # Haiku for parallel work — faster + cheaper

# Max concurrent Claude calls. Tune to your rate limit tier.
# Tier 1: keep at 2-3. Tier 2+: can raise to 5-10.
MAX_CONCURRENCY = 3

T = TypeVar("T")


# ── Task Definition ───────────────────────────────────────────────────────────

@dataclass
class Task:
    """A unit of work to execute concurrently."""
    name: str
    prompt: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskResult:
    """Result of a single task execution."""
    task: Task
    response: str | None
    error: str | None
    duration_s: float
    success: bool


# ── Semaphore-Guarded Runner ──────────────────────────────────────────────────

class ParallelRunner:
    """
    Execute multiple tasks concurrently, respecting a concurrency limit.

    Usage:
        runner = ParallelRunner(max_concurrency=3)
        results = await runner.run(tasks)
    """

    def __init__(
        self,
        max_concurrency: int = MAX_CONCURRENCY,
        model: str = DEFAULT_MODEL,
        system: str | None = None,
    ) -> None:
        self.max_concurrency = max_concurrency
        self.model = model
        self.system = system
        self._semaphore: asyncio.Semaphore | None = None

    async def _run_one(self, task: Task) -> TaskResult:
        """Execute a single task, acquiring the semaphore first."""
        assert self._semaphore is not None

        async with self._semaphore:
            logger.info("Starting: %s", task.name)
            start = time.perf_counter()

            try:
                options = ClaudeCodeOptions(model=self.model)
                if self.system:
                    options.system_prompt = self.system

                full_text = ""
                async for message in query(prompt=task.prompt, options=options):
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                full_text += block.text

                duration = time.perf_counter() - start
                logger.info("Done: %s (%.2fs)", task.name, duration)

                return TaskResult(
                    task=task,
                    response=full_text or "[empty response]",
                    error=None,
                    duration_s=duration,
                    success=True,
                )

            except Exception as exc:
                duration = time.perf_counter() - start
                logger.warning("Error in %s: %s", task.name, exc)
                return TaskResult(
                    task=task,
                    response=None,
                    error=str(exc),
                    duration_s=duration,
                    success=False,
                )

    async def run(self, tasks: list[Task]) -> list[TaskResult]:
        """
        Execute all tasks concurrently, up to max_concurrency at a time.

        Returns results in the same order as the input tasks.

        Args:
            tasks: List of Task objects to execute.

        Returns:
            List of TaskResult in the same order as tasks.
        """
        if not tasks:
            return []

        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        start_total = time.perf_counter()

        results = await asyncio.gather(
            *[self._run_one(task) for task in tasks],
        )

        total_duration = time.perf_counter() - start_total
        successes = sum(1 for r in results if r.success)
        logger.info(
            "Batch complete: %d/%d succeeded in %.2fs",
            successes, len(tasks), total_duration,
        )

        return list(results)


# ── Batch Processing ──────────────────────────────────────────────────────────

def chunk(items: list[T], size: int) -> list[list[T]]:
    """Split a list into chunks of at most `size` items."""
    return [items[i : i + size] for i in range(0, len(items), size)]


async def run_in_batches(
    tasks: list[Task],
    *,
    batch_size: int = 10,
    delay_between_batches: float = 2.0,
    model: str = DEFAULT_MODEL,
    max_concurrency: int = MAX_CONCURRENCY,
) -> list[TaskResult]:
    """
    Run tasks in batches with a delay between each batch.

    Use for very large task lists where you want extra pacing
    beyond what a semaphore alone provides.

    Args:
        tasks: All tasks to execute.
        batch_size: Tasks per batch.
        delay_between_batches: Seconds between batches.
        model: Claude model to use.
        max_concurrency: Max concurrent calls per batch.

    Returns:
        All results in original task order.
    """
    runner = ParallelRunner(max_concurrency=max_concurrency, model=model)
    all_results: list[TaskResult] = []

    batches = chunk(tasks, batch_size)
    for i, batch in enumerate(batches):
        logger.info("Batch %d/%d (%d tasks)", i + 1, len(batches), len(batch))
        results = await runner.run(batch)
        all_results.extend(results)

        if i < len(batches) - 1:
            logger.info("Waiting %.1fs before next batch…", delay_between_batches)
            await asyncio.sleep(delay_between_batches)

    return all_results


# ── Worktree Isolation (Git) ──────────────────────────────────────────────────

def create_worktree(base_repo: str, branch_name: str) -> str | None:
    """
    Create an isolated git worktree for a parallel task.

    Each concurrent task gets its own working tree — they can read/write files
    without stomping on each other.

    Args:
        base_repo: Path to the git repository root.
        branch_name: Name for the new branch in the worktree.

    Returns:
        Path to the new worktree directory, or None if creation failed.
    """
    worktree_path = tempfile.mkdtemp(prefix=f"worktree-{branch_name}-")
    result = subprocess.run(
        ["git", "-C", base_repo, "worktree", "add", "-b", branch_name, worktree_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("Failed to create worktree: %s", result.stderr.strip())
        return None
    return worktree_path


def remove_worktree(base_repo: str, worktree_path: str) -> None:
    """Remove a git worktree after use."""
    subprocess.run(
        ["git", "-C", base_repo, "worktree", "remove", "--force", worktree_path],
        capture_output=True,
    )


# ── Demo ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    """
    Run 5 independent queries concurrently (max 3 at once via semaphore).
    """
    print("=== Parallel Tasks Demo ===\n")
    print(f"Running 5 tasks with max_concurrency={MAX_CONCURRENCY}\n")

    tasks = [
        Task("python-summary",    "In one sentence, what is Python best known for?"),
        Task("rust-summary",      "In one sentence, what is Rust best known for?"),
        Task("typescript-summary","In one sentence, what is TypeScript best known for?"),
        Task("go-summary",        "In one sentence, what is Go best known for?"),
        Task("haskell-summary",   "In one sentence, what is Haskell best known for?"),
    ]

    runner = ParallelRunner(max_concurrency=MAX_CONCURRENCY, model=DEFAULT_MODEL)

    wall_start = time.perf_counter()
    results = await runner.run(tasks)
    wall_time = time.perf_counter() - wall_start

    print("\n--- Results ---")
    for r in results:
        status = "✓" if r.success else "✗"
        print(f"\n{status} {r.task.name} ({r.duration_s:.2f}s)")
        if r.success and r.response:
            print(f"  {r.response.strip()[:200]}")
        elif r.error:
            print(f"  ERROR: {r.error}")

    successes = sum(1 for r in results if r.success)
    sequential_est = sum(r.duration_s for r in results)
    print(f"\nSummary: {successes}/{len(results)} succeeded")
    print(f"Wall time: {wall_time:.2f}s  |  Sequential est: {sequential_est:.2f}s")
    if wall_time > 0:
        print(f"Speedup: {sequential_est / wall_time:.1f}x")


if __name__ == "__main__":
    asyncio.run(main())
