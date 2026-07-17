"""Rate limiting (429, RFC 6585) — node B13a.

A per-client quota, checked right after ``service_available`` and before any
method/auth/body work, so an over-limit request is shed at the cheapest point. The
callback returns ``True`` (within limit), ``False`` (429, no hint), or a
``Retry-After`` hint (int seconds / an HTTP-date). 429 postdates webmachine v3, so
B13a is additive: recorded only when it fires. Contrast B13 (503, service-wide).
"""

from __future__ import annotations

from datetime import UTC, datetime

from asgimachine.resource import Ctx, Resource, RetryHint
from asgimachine.schema import Operation, ResourceDescription, generate_openapi
from asgimachine.substrate.starlette import build_app, resource_route
from asgimachine.testing import assert_trace, make_client


class Limited(Resource):
    ALLOWED_METHODS = frozenset({"GET"})

    def __init__(self, verdict: bool | RetryHint) -> None:
        self._verdict = verdict

    async def within_rate_limit(self, ctx: Ctx) -> bool | RetryHint:
        return self._verdict

    async def is_authorized(self, ctx: Ctx) -> bool | str:
        return False  # would be a 401 — but B13a fires first when over limit

    async def represent(self, ctx: Ctx) -> object:
        return {"ok": True}


def _client(verdict: bool | RetryHint):
    return make_client(build_app([resource_route("/r", Limited(verdict))], debug=True))


def test_within_limit_passes() -> None:
    # True flows straight through; the (failing) auth check below then runs -> 401.
    resp = _client(True).get("/r")
    assert resp.status_code == 401
    assert "B13a" not in resp.headers["x-asgimachine-trace"]


def test_over_limit_is_429() -> None:
    resp = _client(False).get("/r")
    assert resp.status_code == 429
    assert "retry-after" not in resp.headers  # bare False -> no hint


def test_b13a_is_shed_before_auth() -> None:
    # The resource's is_authorized returns False, but B13a sits ahead of B8, so an
    # over-limit request is turned away as 429 before authentication is attempted.
    resp = _client(False).get("/r")
    assert_trace(resp, ["B13", "B13a"])


def test_int_hint_sets_retry_after() -> None:
    resp = _client(7).get("/r")
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "7"


def test_datetime_hint_sets_http_date() -> None:
    when = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
    resp = _client(when).get("/r")
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "Mon, 20 Jul 2026 12:00:00 GMT"


def test_default_resource_never_limits() -> None:
    class Plain(Resource):
        ALLOWED_METHODS = frozenset({"GET"})

        async def represent(self, ctx: Ctx) -> object:
            return {"ok": True}

    resp = make_client(build_app([resource_route("/r", Plain())], debug=True)).get("/r")
    assert resp.status_code == 200
    assert "B13a" not in resp.headers["x-asgimachine-trace"]


def test_schema_surfaces_429_when_overridden() -> None:
    class Described(Resource):
        ALLOWED_METHODS = frozenset({"GET"})

        async def within_rate_limit(self, ctx: Ctx) -> bool:
            return True

        async def represent(self, ctx: Ctx) -> object:
            return {"ok": True}

        def describe(self) -> ResourceDescription:
            return ResourceDescription(
                get=Operation(responses={200: {"type": "object"}})
            )

    doc = generate_openapi(title="T", version="1", routes=[("/r", Described())])
    assert "429" in doc["paths"]["/r"]["get"]["responses"]
