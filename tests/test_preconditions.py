"""Conditional-request preconditions — 412 (PLAN.md §4 v2, M2 slice 3).

If-Match / If-Unmodified-Since fail with 412; If-None-Match yields 412 on writes
(304 on reads is covered in test_conformance_v0/test_trace).
"""

from __future__ import annotations

from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route
from asgimachine.testing import assert_trace, make_client

from tests.conftest import Toggles

_STRONG = '"v1"'

# --- read-side preconditions (If-Match, If-Unmodified-Since) ----------------


def test_if_match_matching_proceeds(client_for) -> None:
    resp = client_for(Toggles(etag=_STRONG)).get("/r", headers={"if-match": _STRONG})
    assert resp.status_code == 200


def test_if_match_star_proceeds(client_for) -> None:
    resp = client_for(Toggles()).get("/r", headers={"if-match": "*"})
    assert resp.status_code == 200


def test_if_match_mismatch_is_412(client_for) -> None:
    resp = client_for(Toggles(etag=_STRONG)).get("/r", headers={"if-match": '"other"'})
    assert resp.status_code == 412
    assert resp.headers["etag"] == _STRONG
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4", "G7", "G11"])


def test_if_match_weak_client_tag_is_412(client_for) -> None:
    # RFC 9110 §13.1.1: If-Match uses strong comparison — a weak client validator
    # never matches, even when the opaque tag is identical.
    resp = client_for(Toggles(etag=_STRONG)).get("/r", headers={"if-match": 'W/"v1"'})
    assert resp.status_code == 412


def test_if_match_weak_resource_validator_is_412(client_for) -> None:
    # A weak *resource* validator also can never satisfy If-Match.
    resp = client_for(Toggles(etag='W/"v1"')).get("/r", headers={"if-match": '"v1"'})
    assert resp.status_code == 412


def test_if_unmodified_since_stale_is_412(client_for) -> None:
    # Last-Modified is 2026-01-01; a past If-Unmodified-Since means it changed.
    resp = client_for(Toggles()).get(
        "/r", headers={"if-unmodified-since": "Sun, 01 Jan 2020 00:00:00 GMT"}
    )
    assert resp.status_code == 412
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4", "G7", "H12"])


def test_if_unmodified_since_fresh_proceeds(client_for) -> None:
    resp = client_for(Toggles()).get(
        "/r", headers={"if-unmodified-since": "Wed, 02 Jan 2030 00:00:00 GMT"}
    )
    assert resp.status_code == 200


def test_if_unmodified_since_ignored_when_if_match_present(client_for) -> None:
    # RFC 9110 §13.1.4: If-Match present -> If-Unmodified-Since MUST be ignored.
    # If-Match matches (proceed); the stale If-Unmodified-Since must not 412.
    resp = client_for(Toggles(etag=_STRONG)).get(
        "/r",
        headers={
            "if-match": _STRONG,
            "if-unmodified-since": "Sun, 01 Jan 2020 00:00:00 GMT",
        },
    )
    assert resp.status_code == 200


def test_if_unmodified_since_unverifiable_proceeds(client_for) -> None:
    # No Last-Modified -> the precondition is unverifiable -> proceed, not 412.
    resp = client_for(Toggles(last_modified=None)).get(
        "/r", headers={"if-unmodified-since": "Sun, 01 Jan 2020 00:00:00 GMT"}
    )
    assert resp.status_code == 200


# --- write-side If-None-Match -> 412 ---------------------------------------


class WriteResource(Resource):
    ALLOWED_METHODS = frozenset({"GET", "PUT"})

    async def generate_etag(self, ctx: Ctx) -> str | None:
        return 'W/"w1"'

    CONSUMES = ("application/json",)

    async def apply(self, ctx: Ctx, body: dict[str, object]) -> None:
        return None

    async def represent(self, ctx: Ctx) -> object:
        return {}


def _write_client():
    return make_client(build_app([resource_route("/w", WriteResource())], debug=True))


def test_if_none_match_on_write_is_412() -> None:
    resp = _write_client().put("/w", json={"x": 1}, headers={"if-none-match": 'W/"w1"'})
    assert resp.status_code == 412
    assert_trace(
        resp,
        ["B13", "B12", "B10", "B9", "B8", "B7", "B6", "B5", "B4", "C4", "G7", "K13"],
    )


def test_if_none_match_star_on_write_is_412() -> None:
    resp = _write_client().put("/w", json={"x": 1}, headers={"if-none-match": "*"})
    assert resp.status_code == 412


# --- If-None-Match: * when the resource declares no ETag (regression) --------
# '*' tests existence, not the presence of an ETag string; a resource using the
# default generate_etag (-> None) must still honor it.


class NoEtagWrite(Resource):
    """A write resource that does not implement generate_etag (etag -> None)."""

    ALLOWED_METHODS = frozenset({"GET", "PUT"})
    CONSUMES = ("application/json",)

    async def apply(self, ctx: Ctx, body: dict[str, object]) -> None:
        return None

    async def represent(self, ctx: Ctx) -> object:
        return {}


def test_if_none_match_star_no_etag_on_write_is_412() -> None:
    # PUT If-None-Match: * is "create only if absent"; the target exists, so it
    # MUST 412 rather than silently overwriting — even with no ETag defined.
    client = make_client(build_app([resource_route("/n", NoEtagWrite())], debug=True))
    resp = client.put("/n", json={"x": 1}, headers={"if-none-match": "*"})
    assert resp.status_code == 412


def test_if_none_match_star_no_etag_on_read_is_304(client_for) -> None:
    # GET If-None-Match: * on an existing, ETag-less resource -> 304, not 200.
    resp = client_for(Toggles(etag=None)).get("/r", headers={"if-none-match": "*"})
    assert resp.status_code == 304
