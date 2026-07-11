"""Immutable feed / outbox example (PLAN.md §4 v3; REST in Practice).

An append-only event log paginated into fixed-size pages — the endpoint class
that most rewards the whole exercise. **Archived** pages (full, never to change
again) get a stable ETag and ``Cache-Control: …, immutable``, so a CDN can hold
them forever. The **head** page (still filling) is ``no-cache`` and revalidates
via conditional GET — a 304 whenever nothing new has arrived.

    uvicorn examples.feed:app --reload
    curl -i localhost:8000/feed/0     # archived -> immutable
    curl -i localhost:8000/feed/2     # head -> no-cache, revalidate
"""

from __future__ import annotations

from dataclasses import dataclass, field

from starlette.applications import Starlette

from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route

PAGE_SIZE = 3
IMMUTABLE = "public, max-age=31536000, immutable"


@dataclass(slots=True)
class Outbox:
    """An append-only event log, wired into the resource at the root."""

    events: list[str] = field(default_factory=list[str])

    def append(self, event: str) -> None:
        self.events.append(event)

    @property
    def head_page(self) -> int:
        # The index of the page still being filled (grows every PAGE_SIZE events).
        return len(self.events) // PAGE_SIZE

    def page(self, index: int) -> list[str]:
        start = index * PAGE_SIZE
        return self.events[start : start + PAGE_SIZE]


class FeedResource(Resource):
    def __init__(self, outbox: Outbox) -> None:
        self._outbox = outbox

    async def allowed_methods(self, ctx: Ctx) -> list[str]:
        return ["GET", "HEAD"]

    async def resource_exists(self, ctx: Ctx) -> bool:
        page = _parse_page(ctx.request.path_params.get("page"))
        if page is None or not (0 <= page <= self._outbox.head_page):
            return False
        ctx.extra["page"] = page
        ctx.entity = self._outbox.page(page)
        return True

    def _archived(self, ctx: Ctx) -> bool:
        # A page below the head page is full and immutable.
        return ctx.extra["page"] < self._outbox.head_page

    async def generate_etag(self, ctx: Ctx) -> str | None:
        page = ctx.extra["page"]
        if self._archived(ctx):
            return f'"feed-{page}"'  # immutable content -> the id is the validator
        return f'"feed-{page}-{len(ctx.entity)}"'  # head page grows

    async def cache_control(self, ctx: Ctx) -> str | None:
        return IMMUTABLE if self._archived(ctx) else "no-cache"

    async def to_json(self, ctx: Ctx) -> object:
        return {
            "page": ctx.extra["page"],
            "archived": self._archived(ctx),
            "events": ctx.entity,
        }


def _parse_page(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def seed_outbox() -> Outbox:
    outbox = Outbox()
    for i in range(7):  # 7 events -> pages 0,1 archived (full), page 2 is the head
        outbox.append(f"event-{i}")
    return outbox


def make_app(outbox: Outbox | None = None, *, debug: bool = False) -> Starlette:
    outbox = outbox if outbox is not None else seed_outbox()
    return build_app(
        [resource_route("/feed/{page}", FeedResource(outbox))], debug=debug
    )


app = make_app()
