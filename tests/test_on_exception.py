"""The on_exception catch-all for unexpected exceptions.

By default an ``Exception`` raised during the walk propagates (to Starlette's
ServerErrorMiddleware). An app-level handler (or a per-resource override) may
instead report the error and return, so the graph owns the 500 — with the
negotiated problem+json body and the trace header. A ``BaseException`` (client
disconnect / cancellation) always propagates, untouched.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from asgimachine.http import HaltResponse, HttpResponse, Status
from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route

PROBLEM = "application/problem+json"


class Boom(Resource):
    async def represent(self, ctx: Ctx) -> object:
        raise RuntimeError("boom")


def _client(resource: Resource, *, on_exception=None, raise_server_exceptions=True):
    app = build_app(
        [resource_route("/r", resource)], debug=True, on_exception=on_exception
    )
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


# --- default: propagate ------------------------------------------------------


def test_default_propagates_the_exception() -> None:
    # No handler: the exception propagates out of run() to Starlette.
    with pytest.raises(RuntimeError, match="boom"):
        _client(Boom()).get("/r")


def test_default_yields_starlette_500_when_client_swallows() -> None:
    # With the client not re-raising, Starlette's generic 500 is what's produced —
    # notably NOT the graph's problem+json (the graph never owned this response).
    resp = _client(Boom(), raise_server_exceptions=False).get("/r")
    assert resp.status_code == 500
    assert resp.headers["content-type"] != PROBLEM


# --- app-level handler owns the 500 -----------------------------------------


def test_handler_returning_none_yields_graph_owned_500() -> None:
    seen: list[Exception] = []

    async def handler(ctx: Ctx, exc: Exception) -> None:
        seen.append(exc)  # report / enrich here
        return None  # -> the standard negotiated 500

    resp = _client(Boom(), on_exception=handler).get("/r")
    assert resp.status_code == 500
    assert resp.headers["content-type"] == PROBLEM
    assert resp.json() == {
        "type": "about:blank",
        "title": "Internal Server Error",
        "status": 500,
    }
    # Ran inside the walk with ctx; got the real exception; trace header present.
    assert isinstance(seen[0], RuntimeError)
    assert resp.headers.get("x-asgimachine-trace")


def test_handler_may_return_a_custom_response() -> None:
    async def handler(ctx: Ctx, exc: Exception) -> HttpResponse:
        return HttpResponse(
            status=int(Status.SERVICE_UNAVAILABLE), headers={"Retry-After": "5"}
        )

    resp = _client(Boom(), on_exception=handler).get("/r")
    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "5"
    # An empty error response still gets the negotiated problem+json body.
    assert resp.headers["content-type"] == PROBLEM


def test_handler_may_reraise_to_propagate() -> None:
    async def handler(ctx: Ctx, exc: Exception) -> None:
        raise exc

    with pytest.raises(RuntimeError, match="boom"):
        _client(Boom(), on_exception=handler).get("/r")


def test_handler_may_raise_haltresponse_for_full_control() -> None:
    async def handler(ctx: Ctx, exc: Exception) -> None:
        raise HaltResponse(HttpResponse(status=502))

    resp = _client(Boom(), on_exception=handler).get("/r")
    assert resp.status_code == 502


# --- resource override precedence -------------------------------------------


def test_resource_override_beats_app_default() -> None:
    class OwnHandler(Boom):
        async def on_exception(self, ctx: Ctx, exc: Exception) -> HttpResponse:
            return HttpResponse(status=418)  # distinct from the app default

    async def app_default(ctx: Ctx, exc: Exception) -> None:
        return None  # would be a 500

    resp = _client(OwnHandler(), on_exception=app_default).get("/r")
    assert resp.status_code == 418


# --- BaseException is never handled -----------------------------------------


class _Cancel(BaseException):
    """Stands in for CancelledError etc. — must never be caught by on_exception."""


def test_base_exception_is_not_handled() -> None:
    handled: list[Exception] = []

    class BaseBoom(Resource):
        async def represent(self, ctx: Ctx) -> object:
            raise _Cancel

    async def handler(ctx: Ctx, exc: Exception) -> None:
        handled.append(exc)
        return None

    with pytest.raises(_Cancel):
        _client(BaseBoom(), on_exception=handler).get("/r")
    assert handled == []  # the catch-all never saw it


# --- rollback: a handled 500 still tears down with the exception -------------


def test_handled_500_still_rolls_back_the_lifespan() -> None:
    class TxResource(Resource):
        def __init__(self) -> None:
            self.torn_down_with: BaseException | None = None

        async def lifespan(self, ctx: Ctx):
            try:
                yield
            except Exception as exc:
                self.torn_down_with = exc
                raise

        async def represent(self, ctx: Ctx) -> object:
            raise RuntimeError("boom")

    async def handler(ctx: Ctx, exc: Exception) -> None:
        return None

    resource = TxResource()
    resp = _client(resource, on_exception=handler).get("/r")
    assert resp.status_code == 500
    # Even though we returned a 500 (not raised), teardown saw the exception, so a
    # transaction would have rolled back rather than committed.
    assert isinstance(resource.torn_down_with, RuntimeError)
