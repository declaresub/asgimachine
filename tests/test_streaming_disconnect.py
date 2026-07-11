"""Client-disconnect handling for streams (PLAN.md §8).

Disconnect cancellation is rented from Starlette (an anyio task group). These
tests pin the guarantees asgimachine owns: an infinite producer is *stopped* on
client disconnect and its ``finally`` cleanup runs, and ``guard_sse`` never
swallows the cancellation into an error frame.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, MutableMapping
from typing import Any

import anyio
import pytest
from anyio.lowlevel import checkpoint

from asgimachine.resource import Ctx, Resource
from asgimachine.streaming import guard_sse, sse_event
from asgimachine.substrate.starlette import build_app, resource_route


class InfiniteStream(Resource):
    """Streams forever until cancelled; records that cleanup ran."""

    def __init__(self) -> None:
        self.produced = 0
        self.cleaned_up = False

    ALLOWED_METHODS = frozenset({"GET"})

    async def content_types_provided(self, ctx: Ctx):
        return [("text/event-stream", self.to_events)]

    async def to_events(self, ctx: Ctx) -> AsyncIterator[bytes]:
        return self._events()

    async def _events(self) -> AsyncIterator[bytes]:
        try:
            while True:
                self.produced += 1
                yield sse_event({"n": self.produced}, event="tick")
                await checkpoint()  # a cancellation point between events
        finally:
            self.cleaned_up = True


_SCOPE = {
    "type": "http",
    "asgi": {"version": "3.0"},  # spec_version defaults to 2.0 -> task-group path
    "http_version": "1.1",
    "method": "GET",
    "path": "/s",
    "raw_path": b"/s",
    "query_string": b"",
    "headers": [(b"accept", b"text/event-stream")],
    "client": ("test", 1234),
    "server": ("test", 80),
    "scheme": "http",
    "root_path": "",
}


async def test_disconnect_stops_producer_and_runs_cleanup() -> None:
    resource = InfiniteStream()
    app = build_app([resource_route("/s", resource)])
    trigger = anyio.Event()
    chunks = 0

    async def receive() -> MutableMapping[str, Any]:
        await trigger.wait()
        return {"type": "http.disconnect"}

    async def send(message: MutableMapping[str, Any]) -> None:
        nonlocal chunks
        if message["type"] == "http.response.body" and message.get("body"):
            chunks += 1
            if chunks >= 3:  # let a few events through, then "disconnect"
                trigger.set()

    # If disconnect were not honored, this infinite stream would hang forever.
    with anyio.fail_after(5):
        await app(_SCOPE, receive, send)

    assert resource.cleaned_up is True
    assert 0 < chunks < 10_000  # bounded: the producer stopped


async def test_guard_sse_does_not_swallow_cancellation() -> None:
    async def source() -> AsyncIterator[bytes]:
        yield b"a"
        raise asyncio.CancelledError

    gen = guard_sse(source())
    assert await gen.__anext__() == b"a"
    with pytest.raises(asyncio.CancelledError):
        await gen.__anext__()
