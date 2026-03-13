# Recipe: Parallel Tasks

**Pattern:** Run multiple agent tasks concurrently using `asyncio.gather` with a `Semaphore` to stay within API rate limits.

## The Problem

Running tasks sequentially is slow. If you have 20 independent prompts to process,
a sequential loop takes 20x the latency of a single call. `asyncio.gather` can
run them all at once — but firing 20 concurrent API calls will hit rate limits.

The solution: a `Semaphore` that caps concurrency at a safe level, queuing the
rest until a slot opens.

## The Pattern

```python
semaphore = asyncio.Semaphore(3)  # max 3 concurrent calls

async def run_one(prompt):
    async with semaphore:
        result = []
        async for msg in query(prompt=prompt, options={"model": MODEL}):
            ...
        return result

results = await asyncio.gather(
    *[run_one(p) for p in all_prompts]
)
```

This gives you:
- **Concurrency:** tasks run in parallel, up to `max_concurrency` at a time
- **Rate limit safety:** the semaphore prevents flooding
- **Order preservation:** `asyncio.gather` returns results in input order
- **Error isolation:** errors in one task don't cancel others

## Choosing max_concurrency

Rate limit tiers vary by account. Start conservative:

| Tier | Recommended max_concurrency |
|------|-----------------------------|
| Free / Tier 1 | 2 |
| Tier 2 | 5 |
| Tier 3+ | 10+ |

Check your limits at [console.anthropic.com](https://console.anthropic.com).
Monitor for `429` errors — if you see them, reduce concurrency.

**Use cheaper models for parallel work.** Haiku is 20x cheaper than Sonnet and
faster. If the task doesn't require deep reasoning, Haiku is the right choice:

```python
runner = ParallelRunner(model="claude-haiku-4-5", max_concurrency=5)
```

## Task Definition

The `Task` dataclass is a clean way to define work units:

```python
@dataclass
class Task:
    name: str        # for logging and result lookup
    prompt: str      # what to ask Claude
    metadata: dict   # pass-through data for your use

tasks = [
    Task("summarize-doc-1", f"Summarize: {doc1_text}"),
    Task("summarize-doc-2", f"Summarize: {doc2_text}"),
    Task("summarize-doc-3", f"Summarize: {doc3_text}"),
]
results = await runner.run(tasks)
```

## Batch Processing for Large Lists

For very large task lists (100+), add batching with delays between batches:

```python
results = await run_in_batches(
    tasks,
    batch_size=10,
    delay_between_batches=2.0,  # breathe between batches
    max_concurrency=3,
)
```

The delay gives the API time to replenish rate limit tokens between batches.

## Git Worktree Isolation

When parallel tasks need to write files (e.g., code generation, file editing),
use git worktrees to give each task its own isolated copy of the repository:

```python
worktrees = []
for i, task in enumerate(tasks):
    wt = create_worktree(
        base_repo="/path/to/repo",
        branch_name=f"task-{i}-{task.name}",
    )
    worktrees.append(wt)

# Each task runs in its own worktree — no filesystem conflicts

# Cleanup after
for wt in worktrees:
    remove_worktree(base_repo, wt)
```

Without worktrees, two concurrent tasks writing to the same file will corrupt each other's changes.

## Error Handling

Errors in individual tasks don't cancel others. Each `TaskResult` has:
- `success: bool` — did it work?
- `response: str | None` — the text if successful
- `error: str | None` — the exception message if failed
- `duration_s: float` — how long it took

```python
for result in results:
    if not result.success:
        logger.error("Task %s failed: %s", result.task.name, result.error)
    else:
        process(result.response)
```

## Measuring Speedup

The demo prints a speedup estimate:

```
Wall time: 3.2s (sequential would be ~12.8s)
Speedup: 4.0x
```

With `max_concurrency=3` and 5 tasks, you expect roughly 2x speedup (tasks run
in two groups: 3 + 2). More tasks with higher concurrency will show better speedups.

## Pitfalls

**Don't use `asyncio.create_task` without a semaphore.** `gather` alone fires
all tasks immediately. The semaphore is what provides pacing.

**Watch memory usage for large batches.** If each task generates a large response,
100 concurrent tasks with 10k-token responses means ~1GB of strings in memory.
Process and discard results as you go, or use smaller batches.

**Shared state is dangerous.** Tasks share the same process and memory space.
If they all write to a shared dict without a lock, you'll get race conditions.
Either use `asyncio.Lock` or design tasks to have no shared mutable state.

## Running the Recipe

```bash
export ANTHROPIC_API_KEY=your-key
python recipe.py
```

Expected output (abbreviated):
```
Running 5 tasks with max_concurrency=3

INFO Starting: python-summary
INFO Starting: rust-summary
INFO Starting: typescript-summary
INFO Done: rust-summary (1.23s)
INFO Starting: go-summary
...

--- Results ---

✓ python-summary (1.45s)
  Python is best known for its readable syntax and versatility in data science.

✓ rust-summary (1.23s)
  Rust is best known for memory safety without garbage collection.

✓ typescript-summary (1.67s)
  TypeScript is best known for adding static typing to JavaScript.

...

Summary: 5/5 succeeded
Wall time: 2.8s (sequential would be ~7.5s)
Speedup: 2.7x
```
