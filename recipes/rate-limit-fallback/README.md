# Recipe: Rate Limit Fallback

**Pattern:** Catch rate limit errors → retry with exponential backoff → fall back to a cheaper model.

## The Problem

Claude's API returns `429 Too Many Requests` or `529 Overloaded` when you exceed
rate limits. In production agents, a single unhandled rate limit error can crash
a pipeline or leave a user hanging. You need a strategy that:

1. Retries transiently — most rate limits clear within seconds
2. Backs off exponentially — don't hammer a saturated API
3. Falls back gracefully — if one tier is saturated, use another

## The Pattern

```python
MODEL_CHAIN = [
    "claude-opus-4-5",     # try first
    "claude-sonnet-4-5",   # fallback 1
    "claude-haiku-4-5",    # fallback 2 (fastest, cheapest)
]

async def query_with_fallback(prompt, preferred_model="claude-sonnet-4-5"):
    for model in chain_from(preferred_model):
        for attempt in range(MAX_RETRIES):
            try:
                return await call_claude(prompt, model=model)
            except RateLimitError:
                await asyncio.sleep(backoff(attempt))
    raise RuntimeError("All models exhausted")
```

## How Rate Limits Are Detected

The SDK doesn't export a specific `RateLimitError` type — errors come through as
generic exceptions. Detect them by string matching on the message:

```python
def is_rate_limit_error(exc):
    msg = str(exc).lower()
    return any(s in msg for s in ["rate limit", "overloaded", "429", "529"])
```

This is brittle but necessary given the current SDK surface. Check
[anthropic-sdk-python releases](https://github.com/anthropics/anthropic-sdk-python)
for typed exceptions if they've been added since this recipe was written.

## Backoff Strategy

Exponential backoff doubles the wait on each retry:

| Attempt | Delay |
|---------|-------|
| 1 | 1s |
| 2 | 2s |
| 3 | 4s |
| 4 | 8s |
| 5 | 16s |

Cap at 60s to avoid very long waits. For interactive applications, consider
showing a progress indicator instead of blocking silently.

## When to Use This

- Any production agent that runs unattended
- Batch processing pipelines that run many queries
- Agents that respond to external triggers (webhooks, messages)

## When NOT to Use This

- Interactive tools where a 60s delay would be unacceptable — fail fast and ask the user to retry
- Development/testing — you want to see rate limits immediately
- Cost-sensitive pipelines where silently switching to Opus would be expensive

## Pitfalls

**Don't retry non-transient errors.** A `401 Unauthorized` will never succeed
on retry — fail immediately. Only retry timeouts, 429s, 529s, and connection errors.

**Don't retry too aggressively.** If you have 50 concurrent requests all retrying
with 1s delays, you'll immediately hit the rate limit again. Use a shared semaphore
(see the [parallel-tasks](../parallel-tasks/) recipe) to limit concurrency.

**Log everything.** Rate limit events are important operational signals. Log the
model, attempt number, and delay so you can tune the chain.

## Running the Recipe

```bash
export ANTHROPIC_API_KEY=your-key
python recipe.py
```

Expected output:
```
=== Rate Limit Fallback Demo ===

1. Normal request (no errors expected):
   Response: Four

2. Model fallback chain configured:
  1. claude-opus-4-5
  → 2. claude-sonnet-4-5
  3. claude-haiku-4-5

3. Retry state mechanics (dry-run, no API call):
   Attempt 1: model=claude-opus-4-5, backoff=1.0s
   Attempt 2: model=claude-opus-4-5, backoff=2.0s
   Attempt 3: model=claude-opus-4-5, backoff=4.0s
   Fallback triggered: True, new model: claude-sonnet-4-5
```
