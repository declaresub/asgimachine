"""Precondition-required (428, RFC 6585) — node W1.

A resource that demands optimistic concurrency rejects an *unconditional*
PUT/PATCH/DELETE with 428 (the lost-update guard). A write that carries an
``If-Match`` / ``If-Unmodified-Since`` flows on to the normal precondition path;
a safe method is never affected.
"""

from __future__ import annotations

from typing import ClassVar

from asgimachine.resource import Ctx, Resource
from asgimachine.schema import Operation, ResourceDescription, generate_openapi
from asgimachine.substrate.starlette import build_app, resource_route
from asgimachine.testing import assert_trace, make_client

FIXED_ETAG = '"v1"'  # strong — If-Match uses strong comparison


class Guarded(Resource):
    ALLOWED_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE"})
    CONSUMES: ClassVar[tuple[str, ...]] = ("application/json",)

    async def require_conditional_write(self, ctx: Ctx) -> bool:
        return True

    async def generate_etag(self, ctx: Ctx) -> str | None:
        return FIXED_ETAG

    async def apply(self, ctx: Ctx, body: object) -> object:
        return None

    async def delete_resource(self, ctx: Ctx) -> bool:
        return True

    async def represent(self, ctx: Ctx) -> object:
        return {"ok": True}


def _client():
    return make_client(build_app([resource_route("/r", Guarded())], debug=True))


def test_unconditional_put_is_428() -> None:
    resp = _client().put("/r", json={"x": 1})
    assert resp.status_code == 428
    assert_trace(
        resp,
        ["B13", "B12", "B10", "B9", "B8", "B7", "B6", "B5", "B4", "C4", "G7", "W1"],
    )


def test_unconditional_delete_is_428() -> None:
    resp = _client().request("DELETE", "/r")
    assert resp.status_code == 428


def test_conditional_put_passes_w1() -> None:
    # A matching If-Match flows through: 428 does not fire, the write succeeds.
    resp = _client().put("/r", json={"x": 1}, headers={"If-Match": FIXED_ETAG})
    assert resp.status_code == 204
    assert "W1" not in resp.headers["x-asgimachine-trace"]


def test_if_unmodified_since_also_satisfies() -> None:
    resp = _client().put(
        "/r",
        json={"x": 1},
        headers={"If-Unmodified-Since": "Wed, 01 Jan 2025 00:00:00 GMT"},
    )
    # Precondition present -> not 428. (It's in the past vs no last_modified, which
    # is unverifiable and treated as passing, so the write proceeds.)
    assert resp.status_code == 204


def test_safe_method_is_unaffected() -> None:
    assert _client().get("/r").status_code == 200


def test_default_resource_never_requires_it() -> None:
    class Plain(Resource):
        ALLOWED_METHODS = frozenset({"GET", "PUT"})
        CONSUMES: ClassVar[tuple[str, ...]] = ("application/json",)

        async def apply(self, ctx: Ctx, body: object) -> object:
            return None

    client = make_client(build_app([resource_route("/r", Plain())]))
    assert client.put("/r", json={"x": 1}).status_code == 204  # no 428


def test_schema_surfaces_428_when_required() -> None:
    class Described(Guarded):
        def describe(self) -> ResourceDescription:
            return ResourceDescription(
                put=Operation(
                    request={"type": "object"}, responses={200: {"type": "object"}}
                )
            )

    doc = generate_openapi(title="T", version="1", routes=[("/r", Described())])
    assert "428" in doc["paths"]["/r"]["put"]["responses"]
