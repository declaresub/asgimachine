"""The streaming/SSE example (PLAN.md §8) — the M3 proof target.

A ``POST`` whose *envelope* is fully governed by the graph — auth, JSON body
validation (malformed -> 400), and negotiation to ``text/event-stream`` — which
then hands an untouched async generator to the substrate for the open-ended part.
A mid-stream failure surfaces as an SSE ``error`` frame (post-commit boundary),
not a 500.

    uvicorn examples.events:app --reload
    curl -N -XPOST localhost:8000/events -H 'authorization: Bearer u1' \
         -H 'content-type: application/json' -d '{"count": 3}'
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from asgimachine.resource import Ctx, Resource
from asgimachine.streaming import guard_sse, sse_event
from asgimachine.substrate.starlette import build_app, resource_route


class EventsResource(Resource):
    """POST a small JSON command, receive a server-sent event stream."""

    ALLOWED_METHODS = frozenset({"POST"})
    PRODUCES = ("text/event-stream",)  # negotiation to SSE is part of the envelope

    async def is_authorized(self, ctx: Ctx) -> bool | str:
        auth = ctx.request.headers.get("authorization", "")
        if auth.startswith("Bearer ") and auth.removeprefix("Bearer ").strip():
            return True
        return "Bearer"  # -> 401 WWW-Authenticate: Bearer

    async def malformed_request(self, ctx: Ctx) -> bool:
        # B9 -> 400. Parse the command body up front; stash it for the stream.
        try:
            ctx.extra["command"] = json.loads(await ctx.request.body())
        except ValueError, UnicodeDecodeError:
            return True
        return not isinstance(ctx.extra["command"], dict)

    async def process_post(self, ctx: Ctx) -> AsyncIterator[bytes]:
        # The graph has finished the envelope; hand off the untouched generator.
        return guard_sse(self._events(ctx))

    async def represent(self, ctx: Ctx) -> AsyncIterator[bytes]:
        return guard_sse(self._events(ctx))

    async def _events(self, ctx: Ctx) -> AsyncIterator[bytes]:
        count = int(ctx.extra["command"].get("count", 3))
        for n in range(count):
            yield sse_event({"n": n}, event="tick", event_id=str(n))
        yield sse_event("done", event="complete")


def make_app() -> object:
    return build_app([resource_route("/events", EventsResource())])


app = make_app()
