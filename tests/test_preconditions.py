"""Conditional-request preconditions — 412 (PLAN.md §4 v2, M2 slice 3).

If-Match / If-Unmodified-Since fail with 412; If-None-Match yields 412 on writes
(304 on reads is covered in test_conformance_v0/test_trace).
"""

from __future__ import annotations

from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route
from asgimachine.testing import assert_trace, make_client

from tests.conftest import FIXED_ETAG, Toggles

# --- read-side preconditions (If-Match, If-Unmodified-Since) ----------------


def test_if_match_matching_proceeds(client_for) -> None:
    resp = client_for(Toggles()).get("/r", headers={"if-match": FIXED_ETAG})
    assert resp.status_code == 200


def test_if_match_star_proceeds(client_for) -> None:
    resp = client_for(Toggles()).get("/r", headers={"if-match": "*"})
    assert resp.status_code == 200


def test_if_match_mismatch_is_412(client_for) -> None:
    resp = client_for(Toggles()).get("/r", headers={"if-match": '"other"'})
    assert resp.status_code == 412
    assert resp.headers["etag"] == FIXED_ETAG
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4", "G7", "G11"])


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


# --- write-side If-None-Match -> 412 ---------------------------------------


class WriteResource(Resource):
    async def allowed_methods(self, ctx: Ctx) -> list[str]:
        return ["GET", "PUT"]

    async def generate_etag(self, ctx: Ctx) -> str | None:
        return 'W/"w1"'

    async def content_types_accepted(self, ctx: Ctx):
        return [("application/json", self._accept)]

    async def _accept(self, ctx: Ctx) -> None:
        return None

    async def to_json(self, ctx: Ctx) -> object:
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
