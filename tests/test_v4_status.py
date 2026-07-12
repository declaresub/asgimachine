"""§4 v4 (httpdd) RFC-completeness slice: 451 legally-restricted, 308 permanent.

Both are additive graph nodes with correct-by-default callbacks: is_legally_
restricted (B7a) -> 451 (RFC 7725), permanent_redirect (K5a) -> 308 (RFC 7538).
"""

from __future__ import annotations

from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route
from asgimachine.testing import assert_trace, make_client


class LegallyRestricted(Resource):
    async def is_legally_restricted(self, ctx: Ctx) -> bool:
        return True

    async def represent(self, ctx: Ctx) -> object:
        return {}


def test_legally_restricted_is_451() -> None:
    client = make_client(
        build_app([resource_route("/x", LegallyRestricted())], debug=True)
    )
    resp = client.get("/x")
    assert resp.status_code == 451
    # 451 fires at B7a, right after forbidden (B7) passes.
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "B7a"])


def test_legally_restricted_default_passes() -> None:
    # A resource that doesn't override it is unaffected (B7a records True).
    class Plain(Resource):
        async def represent(self, ctx: Ctx) -> object:
            return {"ok": True}

    resp = make_client(build_app([resource_route("/p", Plain())])).get("/p")
    assert resp.status_code == 200


class PermanentlyMoved(Resource):
    async def resource_exists(self, ctx: Ctx) -> bool:
        return False

    async def previously_existed(self, ctx: Ctx) -> bool:
        return True

    async def permanent_redirect(self, ctx: Ctx) -> str | None:
        return "/new-home"

    async def represent(self, ctx: Ctx) -> object:
        return {}


def test_permanent_redirect_is_308_with_location() -> None:
    client = make_client(
        build_app([resource_route("/old", PermanentlyMoved())], debug=True)
    )
    resp = client.get("/old", follow_redirects=False)
    assert resp.status_code == 308  # method-preserving, unlike 301
    assert resp.headers["location"] == "/new-home"
    # B7a isn't in the trace here — it only records when it fires (see the 451
    # test); K5a records on the redirect halt.
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4", "G7", "K7", "K5a"])


class BothRedirects(Resource):
    async def resource_exists(self, ctx: Ctx) -> bool:
        return False

    async def previously_existed(self, ctx: Ctx) -> bool:
        return True

    async def moved_permanently(self, ctx: Ctx) -> str | None:
        return "/via-301"

    async def permanent_redirect(self, ctx: Ctx) -> str | None:
        return "/via-308"

    async def represent(self, ctx: Ctx) -> object:
        return {}


def test_301_still_takes_precedence_when_both_set() -> None:
    # 301 is checked first, so an existing moved_permanently resource is unchanged.
    client = make_client(build_app([resource_route("/b", BothRedirects())]))
    resp = client.get("/b", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/via-301"
