"""Failure kinds. The worker treats them differently."""


class RetryableError(Exception):
    """Waiting may fix it (rate limit, timeout, network). Retry with backoff."""


class FatalError(Exception):
    """Waiting will not fix it (bad key, bad request). Dead-letter at once."""


class MalformedOutputError(RetryableError):
    """The model answered, but not with valid JSON in our schema.
    Asking again often works, so this is retryable."""
