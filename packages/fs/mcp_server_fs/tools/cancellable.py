"""Keep MCP cancellation separate from an already-started file operation."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial, wraps
from typing import Any

import anyio
from fastmcp import FastMCP


def register_cancellable_tool(mcp: FastMCP, fn: Callable[..., Any]) -> Callable[..., Any]:
    """Register the synchronous tool without shielding its MCP request.

    The worker thread may finish its file operation after cancellation. Abandon
    only the request's wait, so the MCP responder does not send a second result.
    """

    @wraps(fn)
    async def invoke(*args: Any, **kwargs: Any) -> Any:
        return await anyio.to_thread.run_sync(partial(fn, *args, **kwargs), abandon_on_cancel=True)

    mcp.tool()(invoke)
    # Preserve the existing direct synchronous Python entrypoints.
    return fn
