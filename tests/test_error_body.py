"""Error bodies (§4 v4, RFC 9457): 4xx/5xx halts carry a negotiated body.

On by default — every error is an RFC 9457 problem detail as
application/problem+json, negotiated over ERROR_PRODUCES separately from the main
representation, overridable via error_body / ERROR_PRODUCES.
"""

from __future__ import annotations

from typing import Any, ClassVar

from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route
from asgimachine.testing import make_client


class Missing(Resource):
    async def resource_exists(self, ctx: Ctx) -> bool:
        return False

    async def represent(self, ctx: Ctx) -> object:
        return {"ok": True}


def _client(resource: Resource) -> Any:
    return make_client(build_app([resource_route("/x", resource)]))


def test_404_is_rfc9457_problem_json() -> None:
    resp = _client(Missing()).get("/x")
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json() == {"type": "about:blank", "title": "Not Found", "status": 404}


def test_401_carries_a_problem_body_with_its_headers() -> None:
    class Guarded(Resource):
        async def is_authorized(self, ctx: Ctx) -> bool | str:
            return "Bearer"

        async def represent(self, ctx: Ctx) -> object:
            return {}

    resp = _client(Guarded()).get("/x")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"  # halt headers preserved
    assert resp.json()["status"] == 401


def test_head_error_has_headers_but_no_body() -> None:
    resp = _client(Missing()).head("/x")
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.content == b""


class _Exists(Missing):
    async def resource_exists(self, ctx: Ctx) -> bool:
        return True


def test_success_has_no_error_body() -> None:
    # The mechanism only touches 4xx/5xx; a 200 is unaffected.
    ok = _client(_Exists()).get("/x")
    assert ok.status_code == 200
    assert ok.json() == {"ok": True}


def test_406_error_body_negotiated_separately_from_main() -> None:
    # The main negotiation failed (Accept can't be satisfied), yet the 406 still
    # gets a problem+json body — error negotiation is independent (serve-anyway).
    resp = _client(_Exists()).get("/x", headers={"accept": "text/csv"})
    assert resp.status_code == 406
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["status"] == 406


def test_custom_error_body_and_detail() -> None:
    class Custom(Missing):
        async def error_body(self, ctx: Ctx, status: int, media_type: str) -> Any:
            return {"type": "about:blank", "status": status, "detail": "no such note"}

    resp = _client(Custom()).get("/x")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "no such note"


def test_error_body_none_is_empty() -> None:
    class Bare(Missing):
        async def error_body(self, ctx: Ctx, status: int, media_type: str) -> None:
            return None

    resp = _client(Bare()).get("/x")
    assert resp.status_code == 404
    assert resp.content == b""


def test_error_produces_negotiation() -> None:
    # A 401 halts at B8, before the main C4 negotiation — yet the error body is
    # still negotiated over ERROR_PRODUCES (independent of the main pass).
    class MultiError(Resource):
        ERROR_PRODUCES: ClassVar[tuple[str, ...]] = (
            "application/problem+json",
            "text/plain",
        )

        async def is_authorized(self, ctx: Ctx) -> bool | str:
            return "Bearer"

        async def error_body(self, ctx: Ctx, status: int, media_type: str) -> Any:
            if media_type == "text/plain":
                return f"error {status}"
            return {"type": "about:blank", "status": status}

        async def represent(self, ctx: Ctx) -> object:
            return {}

    client = _client(MultiError())
    plain = client.get("/x", headers={"accept": "text/plain"})
    assert plain.status_code == 401
    assert plain.headers["content-type"] == "text/plain"
    assert plain.text == "error 401"

    json_err = client.get("/x", headers={"accept": "application/problem+json"})
    assert json_err.status_code == 401
    assert json_err.headers["content-type"] == "application/problem+json"


def test_redirect_keeps_empty_body() -> None:
    class Moved(Resource):
        async def resource_exists(self, ctx: Ctx) -> bool:
            return False

        async def previously_existed(self, ctx: Ctx) -> bool:
            return True

        async def moved_permanently(self, ctx: Ctx) -> str | None:
            return "/new"

        async def represent(self, ctx: Ctx) -> object:
            return {}

    resp = _client(Moved()).get("/x", follow_redirects=False)
    assert resp.status_code == 301  # < 400: no error body
    assert resp.content == b""
