"""Per-request database connection via ``Resource.lifespan`` (PLAN.md §5).

The canonical shape: acquire a pooled connection in the lifespan's *setup* half,
stash it on a typed ``Ctx``, and it is released after the graph walk on **every**
exit — success, a halt (404/401/…), a raised error, or a client disconnect.
Note there is **no** ``@asynccontextmanager`` on the override: it's a plain async
generator, and the core wraps it.

    uvicorn examples.connection:app --reload
    curl -s localhost:8000/widgets
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from starlette.applications import Starlette

from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route


# --- a stand-in for an async connection pool (asyncpg/databases-shaped) -------


@dataclass
class Connection:
    id: int
    closed: bool = False

    async def fetch_widgets(self) -> list[dict[str, object]]:
        return [{"id": 1, "name": "sprocket"}, {"id": 2, "name": "flange"}]


@dataclass
class Pool:
    """A minimal pool that hands out connections and takes them back. A real pool
    (e.g. ``asyncpg.create_pool()``) is itself an async context manager, so it
    drops straight into the ``async with`` below."""

    issued: int = 0
    live: int = 0  # currently checked-out connections (0 between requests)

    @asynccontextmanager
    async def acquire(self) -> AsyncGenerator[Connection]:
        self.issued += 1
        self.live += 1
        conn = Connection(self.issued)
        try:
            yield conn
        finally:
            conn.closed = True
            self.live -= 1


# --- the resource ----------------------------------------------------------


@dataclass(slots=True)
class WidgetsCtx(Ctx):
    conn: Connection | None = None


class WidgetsResource(Resource[WidgetsCtx]):
    context_class = WidgetsCtx
    ALLOWED_METHODS = frozenset({"GET", "HEAD"})

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def lifespan(self, ctx: WidgetsCtx) -> AsyncGenerator[None]:
        # Setup: check a connection out of the pool and stash it on ctx. The
        # `async with` releases it back to the pool after `yield`, on any exit.
        async with self._pool.acquire() as conn:
            ctx.conn = conn
            yield

    async def represent(self, ctx: WidgetsCtx) -> object:
        assert ctx.conn is not None  # lifespan opened it before the walk began
        return {
            "widgets": await ctx.conn.fetch_widgets(),
            "served_by_conn": ctx.conn.id,
        }


def make_app(pool: Pool | None = None, *, debug: bool = False) -> Starlette:
    pool = pool if pool is not None else Pool()
    return build_app([resource_route("/widgets", WidgetsResource(pool))], debug=debug)


app = make_app(debug=True)
