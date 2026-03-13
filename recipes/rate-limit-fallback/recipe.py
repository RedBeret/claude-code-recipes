"""
Rate Limit Fallback — Claude Code SDK Recipe

Pattern: catch rate limit errors, retry with exponential backoff,
fall back to a cheaper model when the primary is saturated.

Run:
    python recipe.py

Requires:
    ANTHROPIC_API_KEY environment variable
"""

import asyncio
import logging
from dataclasses import dataclass, field

from claude_code_sdk import AssistantMessage, ClaudeCodeOptions, TextBlock, query

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

# Fallback chain: try each model in order, move down on rate limit
MODEL_CHAIN = [
    "claude-opus-4-5",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
]

DEFAULT_MODEL = "claude-sonnet-4-5"

# Retry settings
MAX_RETRIES = 4
BASE_DELAY = 1.0   # seconds
MAX_DELAY = 60.0   # seconds


# ── Rate Limit Detection ───────────────────────────────────────────────────────

def is_rate_limit_error(exc: Exception) -> bool:
    """Return True if this exception is a rate limit or overload error."""
    msg = str(exc).lower()
    rate_limit_signals = [
        "rate limit",
        "rate_limit",
        "too many requests",
        "overloaded",
        "529",
        "429",
    ]
    return any(signal in msg for signal in rate_limit_signals)


def is_retryable_error(exc: Exception) -> bool:
    """Return True for transient errors that warrant a retry."""
    msg = str(exc).lower()
    retryable_signals = [
        "rate limit",
        "rate_limit",
        "too many requests",
        "overloaded",
        "timeout",
        "connection",
        "503",
        "529",
        "429",
    ]
    return any(signal in msg for signal in retryable_signals)


# ── Retry Logic ───────────────────────────────────────────────────────────────

@dataclass
class RetryState:
    """Tracks retry attempts and delay state for one request."""
    attempt: int = 0
    current_model_index: int = 0
    delays: list[float] = field(default_factory=list)

    @property
    def current_model(self) -> str:
        """Current model in the fallback chain."""
        idx = min(self.current_model_index, len(MODEL_CHAIN) - 1)
        return MODEL_CHAIN[idx]

    def next_delay(self) -> float:
        """Exponential backoff: 1s, 2s, 4s, 8s … capped at MAX_DELAY."""
        delay = min(BASE_DELAY * (2 ** self.attempt), MAX_DELAY)
        self.delays.append(delay)
        return delay

    def advance_model(self) -> bool:
        """Try the next model in the chain. Returns False if no more models."""
        if self.current_model_index < len(MODEL_CHAIN) - 1:
            self.current_model_index += 1
            logger.warning(
                "Rate limited on %s — falling back to %s",
                MODEL_CHAIN[self.current_model_index - 1],
                self.current_model,
            )
            return True
        return False


async def query_with_fallback(
    prompt: str,
    *,
    system: str | None = None,
    preferred_model: str = DEFAULT_MODEL,
    max_retries: int = MAX_RETRIES,
) -> str:
    """
    Send a query to Claude with automatic rate limit handling.

    On rate limit:
    1. Wait with exponential backoff.
    2. Retry the same model up to max_retries times.
    3. If still failing, fall back to the next cheaper model in MODEL_CHAIN.
    4. If all models exhausted, raise the last exception.

    Args:
        prompt: The user message to send.
        system: Optional system prompt.
        preferred_model: Starting model. Falls back down MODEL_CHAIN if needed.
        max_retries: Max attempts per model before giving up or falling back.

    Returns:
        The assistant's text response.

    Raises:
        RuntimeError: If all models are exhausted without a successful response.
    """
    # Build model chain starting from the preferred model
    try:
        start_index = MODEL_CHAIN.index(preferred_model)
    except ValueError:
        start_index = 0

    state = RetryState(current_model_index=start_index)
    last_exc: Exception | None = None

    while state.attempt <= max_retries:
        model = state.current_model
        logger.info("Attempt %d — model: %s", state.attempt + 1, model)

        try:
            options = ClaudeCodeOptions(model=model)
            if system:
                options.system_prompt = system

            # Collect the full response from the streaming SDK
            full_text = ""
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            full_text += block.text

            if full_text:
                if state.attempt > 0 or state.current_model_index > start_index:
                    logger.info(
                        "Success after %d attempts on model %s",
                        state.attempt + 1,
                        model,
                    )
                return full_text

        except Exception as exc:
            last_exc = exc
            logger.warning("Error on attempt %d: %s", state.attempt + 1, exc)

            if is_rate_limit_error(exc):
                # Try advancing to the next model first
                if state.current_model_index >= start_index + 1:
                    # Already on fallback — just retry with backoff
                    pass
                elif state.advance_model():
                    # Moved to next model — reset attempt count for new model
                    state.attempt = 0
                    continue

            if not is_retryable_error(exc):
                raise

        # Exponential backoff before next retry
        delay = state.next_delay()
        logger.info("Waiting %.1fs before retry …", delay)
        await asyncio.sleep(delay)
        state.attempt += 1

    # All retries on all models exhausted
    if last_exc:
        raise RuntimeError(
            f"All {max_retries} retries exhausted across {len(MODEL_CHAIN)} models"
        ) from last_exc
    raise RuntimeError("No response received from any model")


# ── Demo ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    """Demonstrate the rate limit fallback pattern."""
    print("=== Rate Limit Fallback Demo ===\n")

    # 1. Normal request
    print("1. Normal request (no errors expected):")
    try:
        response = await query_with_fallback(
            "What is 2 + 2? Answer in one word.",
            preferred_model=DEFAULT_MODEL,
        )
        print(f"   Response: {response.strip()}\n")
    except Exception as exc:
        print(f"   Error: {exc}\n")

    # 2. Show the fallback chain
    print("2. Model fallback chain configured:")
    for i, model in enumerate(MODEL_CHAIN):
        marker = "→ " if model == DEFAULT_MODEL else "  "
        print(f"   {marker}{i + 1}. {model}")
    print()

    # 3. Show retry state mechanics
    print("3. Retry state mechanics (dry-run, no API call):")
    state = RetryState(current_model_index=0)
    for i in range(3):
        delay = state.next_delay()
        print(f"   Attempt {i + 1}: model={state.current_model}, backoff={delay:.1f}s")
        state.attempt += 1
    advanced = state.advance_model()
    print(f"   Fallback triggered: {advanced}, new model: {state.current_model}")
    print()

    print("Done. Adapt query_with_fallback() for your agent's task function.")


if __name__ == "__main__":
    asyncio.run(main())
