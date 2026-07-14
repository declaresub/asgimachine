"""Resource.lifespan: guaranteed, cancellation-safe, streaming-aware teardown.

The core opens the lifespan before the walk and closes it on *every* exit path —
a normal response, a halt, a raised error, a client disconnect — deferring the
close past the walk for a streamed body. Overrides are plain async generators
(no @asynccontextmanager); the core owns the wrapping.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

import anyio
import pytest

from asgimachine import core
from asgimachine.core import run
from asgimachine.http import Status
from asgimachine.resource import Ctx, Resource


@dataclass
class FakeRequest:
    method: str = "GET"
    path: str = "/"
    headers: dict[str, str] = field(default_factory=dict[str, str])
    path_params: dict[str, str] = field(default_factory=dict[str, str])
    route: str | None = None

    async def body(self) -> bytes:
        return b""


@dataclass(slots=True)
class LifeCtx(Ctx):
    conn: object | None = None  # the per-request resource the lifespan stashes


class LifeResource(Resource[LifeCtx]):
    """Records open/close (and any in-flight exception) into a shared log."""

    context_class = LifeCtx
    ALLOWED_METHODS = frozenset({"GET", "HEAD"})

    def __init__(self, log: list[str], *, exists: bool = True) -> None:
        self._log = log
        self._exists = exists

    async def lifespan(self, ctx: LifeCtx) -> AsyncGenerator[None]:
        self._log.append("open")
        ctx.conn = object()
        try:
            yield
        except BaseException as exc:  # record then re-raise
            self._log.append(f"error:{type(exc).__name__}")
            raise
        finally:
            self._log.append("close")

    async def resource_exists(self, ctx: LifeCtx) -> bool:
        return self._exists

    async def represent(self, ctx: LifeCtx) -> object:
        self._log.append("represent")
        return {"ok": True}


async def test_lifespan_wraps_a_normal_request() -> None:
    log: list[str] = []
    response = await run(LifeResource(log), FakeRequest())
    assert response.status == int(Status.OK)
    # Opened before the walk, represent ran inside, closed after.
    assert log == ["open", "represent", "close"]


async def test_lifespan_closes_on_a_halt_path() -> None:
    log: list[str] = []
    response = await run(LifeResource(log, exists=False), FakeRequest())
    assert response.status == int(Status.NOT_FOUND)
    # The 404 halts before represent, but teardown still runs.
    assert log == ["open", "close"]


async def test_lifespan_closes_with_exception_then_reraises() -> None:
    log: list[str] = []

    class Boom(LifeResource):
        async def represent(self, ctx: LifeCtx) -> object:
            raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError, match="kaboom"):
        await run(Boom(log), FakeRequest())
    # The exception is fed into the lifespan (the rollback point), then teardown.
    assert log == ["open", "error:RuntimeError", "close"]


async def test_streaming_defers_teardown_until_the_body_drains() -> None:
    log: list[str] = []

    class Streamer(LifeResource):
        async def represent(self, ctx: LifeCtx) -> object:
            async def chunks() -> AsyncGenerator[bytes]:
                for i in range(3):
                    yield f"chunk{i}".encode()

            return chunks()

    response = await run(Streamer(log), FakeRequest())
    assert response.is_stream
    # Walk has returned, but the connection must stay open for the stream.
    assert log == ["open"]

    body = response.body
    assert not isinstance(body, (bytes, bytearray))
    received = [chunk async for chunk in body]
    assert received == [b"chunk0", b"chunk1", b"chunk2"]
    # Draining the body triggers teardown.
    assert log == ["open", "close"]


async def test_streaming_teardown_on_early_close() -> None:
    log: list[str] = []

    class Streamer(LifeResource):
        async def represent(self, ctx: LifeCtx) -> object:
            async def chunks() -> AsyncGenerator[bytes]:
                for i in range(100):
                    yield f"chunk{i}".encode()

            return chunks()

    response = await run(Streamer(log), FakeRequest())
    body = response.body
    assert not isinstance(body, (bytes, bytearray))
    # Consume one chunk, then close early (a client disconnect drops the stream).
    it = aiter(body)
    assert await anext(it) == b"chunk0"
    await body.aclose()  # type: ignore[union-attr]
    # Teardown still runs; the disconnect surfaces to the lifespan as an in-flight
    # GeneratorExit (a rollback signal) before it closes.
    assert log == ["open", "error:GeneratorExit", "close"]


async def test_streaming_teardown_when_never_iterated() -> None:
    # Regression: a client disconnect BEFORE the first chunk means the substrate
    # closes the body without ever iterating it. With an async-generator body the
    # finally never ran (the generator was never started) -> permanent leak. The
    # _ClosingStream wrapper releases on aclose regardless of iteration state.
    log: list[str] = []

    class Streamer(LifeResource):
        async def represent(self, ctx: LifeCtx) -> object:
            async def chunks() -> AsyncGenerator[bytes]:
                yield b"never reached"

            return chunks()

    response = await run(Streamer(log), FakeRequest())
    body = response.body
    assert not isinstance(body, (bytes, bytearray))
    assert log == ["open"]  # not yet iterated
    await body.aclose()  # type: ignore[union-attr]
    # Teardown ran despite the body never being iterated.
    assert log == ["open", "error:GeneratorExit", "close"]


async def test_streaming_teardown_is_idempotent() -> None:
    # Drain fully (releases once, clean), then the substrate's belt-and-suspenders
    # aclose must NOT release a second time.
    log: list[str] = []

    class Streamer(LifeResource):
        async def represent(self, ctx: LifeCtx) -> object:
            async def chunks() -> AsyncGenerator[bytes]:
                yield b"only"

            return chunks()

    response = await run(Streamer(log), FakeRequest())
    body = response.body
    assert not isinstance(body, (bytes, bytearray))
    assert [c async for c in body] == [b"only"]
    assert log == ["open", "close"]  # drained cleanly -> one release, no rollback
    await body.aclose()  # type: ignore[union-attr]
    assert log == ["open", "close"]  # still exactly one release


async def test_teardown_is_shielded_from_cancellation() -> None:
    log: list[str] = []

    class Slow(LifeResource):
        async def lifespan(self, ctx: LifeCtx) -> AsyncGenerator[None]:
            log.append("open")
            try:
                yield
            finally:
                # An *awaiting* teardown (e.g. a transaction rollback). Without
                # the core's shield, a cancellation here would skip the append.
                await anyio.sleep(0.01)
                log.append("close")

        async def represent(self, ctx: LifeCtx) -> object:
            await anyio.sleep(10)  # block in the walk so we can cancel mid-request
            return {}

    task = asyncio.ensure_future(run(Slow(log), FakeRequest()))
    await asyncio.sleep(0.02)  # let the walk reach the blocking represent
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The shielded teardown ran to completion despite the cancellation.
    assert log == ["open", "close"]


async def test_teardown_timeout_prevents_hang(monkeypatch) -> None:
    # A lifespan whose release blocks forever (rollback on a dead socket) must not
    # hang the request: the shield is bounded by _TEARDOWN_TIMEOUT_S.
    monkeypatch.setattr(core, "_TEARDOWN_TIMEOUT_S", 0.05)
    log: list[str] = []

    class Hanger(LifeResource):
        async def lifespan(self, ctx: LifeCtx) -> AsyncGenerator[None]:
            log.append("open")
            try:
                yield
            finally:
                log.append("release-start")
                await anyio.sleep(30)  # blocks well past the timeout
                log.append("release-done")  # unreachable — abandoned at timeout

    with anyio.fail_after(5):  # safety net; real completion is ~0.05s
        response = await run(Hanger(log), FakeRequest())
    assert response.status == int(Status.OK)
    # Release was attempted then abandoned at the timeout, not run to completion
    # ("represent" is logged by the base LifeResource.represent).
    assert log == ["open", "represent", "release-start"]
