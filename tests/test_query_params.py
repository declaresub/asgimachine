"""Query-string access via ctx.request.query_params."""

from __future__ import annotations

from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route
from asgimachine.testing import make_client


class Search(Resource):
    async def represent(self, ctx: Ctx) -> object:
        q = ctx.request.query_params
        return {"q": q.get("q"), "limit": q.get("limit", "10")}


def _client():
    return make_client(build_app([resource_route("/search", Search())]))


def test_reads_query_arguments() -> None:
    assert _client().get("/search?q=cats&limit=5").json() == {"q": "cats", "limit": "5"}


def test_absent_query_is_empty() -> None:
    # No query string -> an empty mapping, so .get() falls back to the default.
    assert _client().get("/search").json() == {"q": None, "limit": "10"}


def test_repeated_key_yields_last_value() -> None:
    assert _client().get("/search?q=a&q=b").json()["q"] == "b"
