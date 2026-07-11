"""Write-path conformance (PLAN.md §4 v2).

Method dispatch (PUT/POST/DELETE) through the graph: acceptors, 201+Location,
204/200, 409, and the body-validation nodes (B9 malformed 400, B6
valid_content_headers 501, B5 known_content_type 415, B4 valid_entity_length 413).
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
        malformed: bool = False,
        bad_content_headers: bool = False,
        too_large: bool = False,
    ) -> None:
        self._store = store
        self._conflict = conflict
        self._is_create = is_create
        self._delete_ok = delete_ok
        self._malformed = malformed
        self._bad_content_headers = bad_content_headers
        self._too_large = too_large

    ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "DELETE"})

    async def malformed_request(self, ctx: Ctx) -> bool:
        return self._malformed

    async def valid_content_headers(self, ctx: Ctx) -> bool:
        return not self._bad_content_headers

    async def valid_entity_length(self, ctx: Ctx) -> bool:
        return not self._too_large

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

    async def represent(self, ctx: Ctx) -> object:
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
        resp,
        [
            "B13",
            "B12",
            "B10",
            "B9",
            "B8",
            "B7",
            "B6",
            "B5",
            "B4",
            "C4",
            "G7",
            "O14",
            "O20",
        ],
    )


def test_put_wrong_content_type_is_415() -> None:
    resp = _client({}).put(
        "/notes", content=b"id=a", headers={"content-type": "text/plain"}
    )
    assert resp.status_code == 415
    assert_trace(resp, ["B13", "B12", "B10", "B9", "B8", "B7", "B6", "B5"])


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
        resp,
        [
            "B13",
            "B12",
            "B10",
            "B9",
            "B8",
            "B7",
            "B6",
            "B5",
            "B4",
            "C4",
            "G7",
            "N11",
            "O20",
        ],
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


# --- body-validation nodes (B9/B6/B4) --------------------------------------


def test_malformed_request_is_400() -> None:
    resp = _client({}, malformed=True).put("/notes", json={"id": "a", "content": "x"})
    assert resp.status_code == 400
    # B9 short-circuits before auth, per the canonical B-column order.
    assert_trace(resp, ["B13", "B12", "B10", "B9"])


def test_bad_content_headers_is_501() -> None:
    resp = _client({}, bad_content_headers=True).put(
        "/notes", json={"id": "a", "content": "x"}
    )
    assert resp.status_code == 501
    assert_trace(resp, ["B13", "B12", "B10", "B9", "B8", "B7", "B6"])


def test_entity_too_large_is_413() -> None:
    resp = _client({}, too_large=True).put("/notes", json={"id": "a", "content": "x"})
    assert resp.status_code == 413
    assert_trace(resp, ["B13", "B12", "B10", "B9", "B8", "B7", "B6", "B5", "B4"])


def test_bodyless_get_skips_body_validation() -> None:
    # A GET never traverses B9/B6/B5/B4 (the §2.4 pruning).
    resp = _client({"a": "x"}).get("/notes")
    assert resp.status_code == 200
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4", "G7", "O18"])
