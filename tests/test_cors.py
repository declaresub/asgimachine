"""CORS is rented from Starlette middleware, not baked into the graph (§2.1).

Verifies the ``build_app`` middleware hook: a true preflight is answered by
CORSMiddleware before the graph runs (so no auth is required), and an actual
response is decorated with ``Access-Control-Allow-Origin`` on the way out.
"""

from __future__ import annotations

from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route
from asgimachine.testing import make_client

ORIGIN = "https://app.example"


class _Guarded(Resource):
    async def is_authorized(self, ctx: Ctx) -> bool | str:
        return "Bearer"  # the graph would 401 anything that reaches it

    async def to_json(self, ctx: Ctx) -> object:
        return {"ok": True}


def _client():
    app = build_app(
        [resource_route("/r", _Guarded())],
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=[ORIGIN],
                allow_methods=["GET"],
            ),
        ],
    )
    return make_client(app)


def test_preflight_answered_before_graph_without_auth() -> None:
    resp = _client().options(
        "/r",
        headers={
            "origin": ORIGIN,
            "access-control-request-method": "GET",
        },
    )
    # Middleware short-circuits the preflight: 200, not the graph's 401.
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ORIGIN


def test_actual_response_gets_allow_origin_header() -> None:
    # A real (non-preflight) request still runs the graph; here it 401s, but the
    # CORS header is stamped on the response regardless.
    resp = _client().get("/r", headers={"origin": ORIGIN})
    assert resp.status_code == 401
    assert resp.headers["access-control-allow-origin"] == ORIGIN
