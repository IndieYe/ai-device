"""Validated, per-client concurrency defaults (no unlimited sentinel)."""

DEFAULT_CONCURRENCY = 8


def positive_limit(value: int) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("concurrency limits must be positive integers")
    return value
