import random

MAX_RETRIES = 6
BASE_WAIT_SECONDS = 15
MAX_WAIT_SECONDS = 180
RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


def should_retry(status_code: int) -> bool:
    return status_code in RETRYABLE_STATUS_CODES


def wait_seconds(attempt: int, response) -> float:
    """Exponential backoff with jitter; honors Retry-After if Groq sends one.
    Groq's rate limit is a rolling hourly window shared across all 11 channels,
    so a short fixed wait makes every channel retry at the same instant and
    collide again. Spreading retries over minutes (with jitter so channels
    don't all wake up together) gives each segment a real chance of landing
    in a freed-up slot instead of being dropped after one quick attempt."""
    retry_after = getattr(response, "headers", {}).get("Retry-After") if response is not None else None
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    backoff = min(BASE_WAIT_SECONDS * (2 ** attempt), MAX_WAIT_SECONDS)
    return backoff + random.uniform(0, backoff * 0.3)
