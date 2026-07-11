"""Write-path conformance — slice 1 (PLAN.md §4 v2).

Method dispatch (PUT/POST/DELETE) through the graph: acceptors, 201+Location,
204/200, 409, and 415 on an unmatched request Content-Type.
"""

from __future__ import annotations

import json

from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route
from asgimachine.testing import assert_trace, make_client


class NotesResource(Resource):
    """A tiny in-memory collection wired with a dict store (no DI)."""

    def __init__(
        self,
        store: dict[str, str],
        *,
        conflict: bool = False,
        is_create: bool = True,
        delete_ok: bool = True,
    ) -> None:
        self._store = store
        self._conflict = conflict
        self._is_create = is_create
        self._delete_ok = delete_ok

    async def allowed_methods(self, ctx: Ctx) -> list[str]:
        return ["GET", "HEAD", "POST", "PUT", "DELETE"]

    async def content_types_accepted(self, ctx: Ctx):
        return [("application/json", self._store_note)]

    async def _store_note(self, ctx: Ctx) -> None:
        data = json.loads(await ctx.request.body())
        self._store[data["id"]] = data["content"]
        return None  # -> 204 (PUT) / 201 no body (POST create)

    async def is_conflict(self, ctx: Ctx) -> bool:
        return self._conflict

    async def post_is_create(self, ctx: Ctx) -> bool:
        return self._is_create

    async def create_path(self, ctx: Ctx) -> str:
        return "/notes/new"

    async def process_post(self, ctx: Ctx) -> object:
        return {"processed": True}

    async def delete_resource(self, ctx: Ctx) -> bool:
        return self._delete_ok

    async def to_json(self, ctx: Ctx) -> object:
        return {"notes": self._store}


def _client(store: dict[str, str], **kw: bool):
    app = build_app([resource_route("/notes", NotesResource(store, **kw))], debug=True)
    return make_client(app)


def test_put_updates_and_returns_204() -> None:
    store: dict[str, str] = {}
    resp = _client(store).put("/notes", json={"id": "a", "content": "hello"})
    assert resp.status_code == 204
    assert resp.content == b""
    assert store == {"a": "hello"}
    assert_trace(
        resp, ["B13", "B12", "B10", "B8", "B7", "B5", "C4", "G7", "O14", "O20"]
    )


def test_put_wrong_content_type_is_415() -> None:
    resp = _client({}).put(
        "/notes", content=b"id=a", headers={"content-type": "text/plain"}
    )
    assert resp.status_code == 415
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "B5"])


def test_put_conflict_is_409() -> None:
    resp = _client({}, conflict=True).put("/notes", json={"id": "a", "content": "x"})
    assert resp.status_code == 409
    assert resp.request.method == "PUT"


def test_post_create_returns_201_with_location() -> None:
    store: dict[str, str] = {}
    resp = _client(store).post("/notes", json={"id": "b", "content": "world"})
    assert resp.status_code == 201
    assert resp.headers["location"] == "/notes/new"
    assert store == {"b": "world"}
    assert_trace(
        resp, ["B13", "B12", "B10", "B8", "B7", "B5", "C4", "G7", "N11", "O20"]
    )


def test_post_process_returns_200_with_body() -> None:
    resp = _client({}, is_create=False).post("/notes", json={"any": "thing"})
    assert resp.status_code == 200
    assert resp.json() == {"processed": True}


def test_delete_returns_204() -> None:
    resp = _client({"a": "x"}).request("DELETE", "/notes")
    assert resp.status_code == 204
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4", "G7", "M20"])


def test_delete_failure_is_500() -> None:
    resp = _client({}, delete_ok=False).request("DELETE", "/notes")
    assert resp.status_code == 500
