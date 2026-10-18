"""Keep synchronous persistence off the network loop without abandoning writes."""

import asyncio
from contextvars import copy_context
from functools import partial


async def run_blocking(operation, *args, **kwargs):
    """Finish an already started operation before cancellation releases its lock."""
    future = asyncio.get_running_loop().run_in_executor(
        None, copy_context().run, partial(operation, *args, **kwargs),
    )
    cancelled = None
    try:
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError as error:
                cancelled = error
            except Exception:
                break
        return future.result()
    finally:
        if cancelled is not None:
            raise cancelled
