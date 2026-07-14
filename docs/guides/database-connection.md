# A per-request database connection

**Goal:** give a resource a database connection on `ctx` that is **acquired only
when first used** and **always released** — even on a `404`, an error, or a client
disconnect.

Two ideas do it: a typed [`Ctx`](../concepts/resources.md) with a lazy `conn()`
accessor, and the resource's [`lifespan`](../concepts/lifespan.md) holding an
`AsyncExitStack` that releases whatever was acquired.

```python
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack
from dataclasses import dataclass

from asgimachine.resource import Ctx, Resource


@dataclass(slots=True)
class DbCtx(Ctx):
    # Wired by the lifespan; call conn(), don't touch these.
    pool: "Pool | None" = None
    stack: "AsyncExitStack | None" = None
    _conn: "Connection | None" = None

    async def conn(self) -> "Connection":
        if self._conn is None:                       # acquire on first use...
            assert self.pool is not None and self.stack is not None
            self._conn = await self.stack.enter_async_context(self.pool.acquire())
        return self._conn                            # ...cached for the rest of the request


class Widgets(Resource[DbCtx]):
    context_class = DbCtx

    def __init__(self, pool: "Pool") -> None:
        self._pool = pool

    async def lifespan(self, ctx: DbCtx) -> AsyncGenerator[None]:
        async with AsyncExitStack() as stack:
            ctx.pool, ctx.stack = self._pool, stack
            yield
        # the stack closes here — releasing the connection *iff* conn() acquired one

    async def resource_exists(self, ctx: DbCtx) -> bool:
        row = await (await ctx.conn()).fetchrow("SELECT 1 FROM widget WHERE id=$1", 1)
        return row is not None

    async def represent(self, ctx: DbCtx) -> object:
        conn = await ctx.conn()                      # same connection resource_exists used
        return {"widgets": await conn.fetch("SELECT * FROM widget")}
```

## Acquire on first use

`ctx.conn()` acquires from the pool the first time it's called and caches the
result, so the whole request shares one connection no matter how many callbacks
call it. The payoff is that callbacks which *don't* call it never acquire:

- A request rejected at `is_authorized` (`401`), `service_available` (`503`), or a
  rate check never touches the pool — a flood costs zero connections.
- A `404` from `resource_exists` that answers without a query (say, from a cache)
  acquires nothing.

This is the *scope vs. acquisition* split: the framework opens the cheap per-request
scope (the lifespan) for every request; **you** decide when the expensive resource
inside it materializes.

## Always released

The lifespan wraps the whole walk, and the core closes it on **every** exit — a
normal response, a halt, a raised error, a client disconnect — exactly once, in a
cancellation-shielded, time-bounded teardown (see
[Per-request lifespan](../concepts/lifespan.md)). Because the connection was entered
into the `AsyncExitStack`, closing the stack releases it — and only if it was ever
acquired. You never write a `finally`.

!!! warning "Route release through the lifespan, not a callback"
    The guarantee comes from the `AsyncExitStack` closing inside the lifespan. If you
    instead acquire a connection in a callback and try to release it in that callback's
    own `finally`, you lose the shielded, rollback-aware teardown — a disconnect can
    interrupt it. Acquire via `ctx.conn()` (which enters the stack); let the lifespan
    close it.

## Rollback on error

The core feeds an in-flight exception *into* the lifespan when it tears down, and the
`AsyncExitStack` propagates it to everything entered into it. So to get a
transaction that rolls back on any error, enter it into the stack too:

```python
async def conn(self) -> "Connection":
    if self._conn is None:
        self._conn = await self.stack.enter_async_context(self.pool.acquire())
        await self.stack.enter_async_context(self._conn.transaction())  # commits, or
    return self._conn                                                   # rolls back on error
```

Now a handler that raises after a write leaves the row uncommitted — the same
machinery that guarantees release guarantees the rollback.

## Swap in a real pool

The snippets above are shaped for `asyncpg` — `pool.acquire()` is an async context
manager, `conn.fetch(...)` runs a query. Create the pool once, at the composition
root, and inject it:

```python
import asyncpg

pool = await asyncpg.create_pool(dsn)
app = build_app([resource_route("/widgets", Widgets(pool))])
```

Any pool with an `acquire()` async context manager fits — `psqlpy`, an SQLAlchemy
`AsyncEngine.connect()`, a Redis pool. To instrument query timings into the
[wide event](../concepts/observability.md), see the asyncpg recipe there.

??? example "A runnable stand-in pool"
    Paste this above the resource to run the example without a real database — it
    mirrors `asyncpg`'s `pool.acquire()` / `conn.fetch()` shape and counts checkouts,
    so you can watch the acquire/release:

    ```python
    from contextlib import asynccontextmanager


    class FakeConnection:
        async def fetch(self, q, *args):
            return [{"id": 1, "name": "cog"}]

        async def fetchrow(self, q, *args):
            return {"id": 1}


    class FakePool:
        def __init__(self) -> None:
            self.in_use = 0        # watch this: 1 during a query, 0 after

        @asynccontextmanager
        async def acquire(self):
            self.in_use += 1
            try:
                yield FakeConnection()
            finally:
                self.in_use -= 1
    ```
