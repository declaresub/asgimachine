"""Webmachine-graph coverage additions: B11 (414 URI Too Long) and the N11
See-Other branch (303, the POST-Redirect-Get pattern).

Both are canonical webmachine nodes that ship correct defaults (never fire unless
the resource opts in), so they're additive — the canonical trace of a resource
that ignores them is unchanged.
"""

from __future__ import annotations

from asgimachine.resource import Ctx, Resource
from asgimachine.schema import Operation, ResourceDescription, generate_openapi
from asgimachine.substrate.starlette import build_app, resource_route
from asgimachine.testing import assert_trace, make_client


# --- B11 uri_too_long? -> 414 ----------------------------------------------


class LongUri(Resource):
    async def uri_too_long(self, ctx: Ctx) -> bool:
        return True

    async def represent(self, ctx: Ctx) -> object:
        return {"ok": True}


def test_uri_too_long_is_414() -> None:
    resp = make_client(build_app([resource_route("/r", LongUri())], debug=True)).get(
        "/r"
    )
    assert resp.status_code == 414
    # B11 fires right after B12, before B10.
    assert_trace(resp, ["B13", "B12", "B11"])


def test_uri_too_long_default_never_fires() -> None:
    class Normal(Resource):
        async def represent(self, ctx: Ctx) -> object:
            return {"ok": True}

    resp = make_client(build_app([resource_route("/r", Normal())], debug=True)).get(
        "/r"
    )
    assert resp.status_code == 200
    assert "B11" not in resp.headers["x-asgimachine-trace"]


def test_schema_surfaces_414_when_overridden() -> None:
    class Described(LongUri):
        def describe(self) -> ResourceDescription:
            return ResourceDescription(
                get=Operation(responses={200: {"type": "object"}})
            )

    doc = generate_openapi(title="T", version="1", routes=[("/r", Described())])
    assert "414" in doc["paths"]["/r"]["get"]["responses"]


# --- N11 see_other -> 303 (POST-Redirect-Get) ------------------------------


class PostRedirect(Resource):
    ALLOWED_METHODS = frozenset({"POST"})

    def __init__(self, *, create: bool) -> None:
        self._create = create
        self.processed = False

    async def post_is_create(self, ctx: Ctx) -> bool:
        return self._create

    async def create_path(self, ctx: Ctx) -> str:
        return "/things/1"

    async def apply(self, ctx: Ctx, body: object) -> object:
        self.processed = True
        return None

    async def process_post(self, ctx: Ctx) -> object:
        self.processed = True
        return {"done": True}

    async def see_other(self, ctx: Ctx) -> str | None:
        return "/things/1"


def _post(resource: Resource):
    app = build_app([resource_route("/things", resource)], debug=True)
    # follow_redirects=False so a 303 is observed here, not chased to its target.
    return make_client(app).post("/things", json={"x": 1}, follow_redirects=False)


def test_post_create_redirects_303_after_side_effects() -> None:
    resource = PostRedirect(create=True)
    resp = _post(resource)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/things/1"
    assert resp.content == b""
    assert resource.processed  # the create ran before the redirect (PRG)
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
            "P0",
            "N11a",
        ],
    )


def test_post_process_redirects_303() -> None:
    resource = PostRedirect(create=False)
    resp = _post(resource)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/things/1"
    assert resource.processed


def test_post_without_see_other_is_unchanged() -> None:
    class PlainCreate(Resource):
        ALLOWED_METHODS = frozenset({"POST"})

        async def post_is_create(self, ctx: Ctx) -> bool:
            return True

        async def create_path(self, ctx: Ctx) -> str:
            return "/things/1"

        async def apply(self, ctx: Ctx, body: object) -> object:
            return None

    resp = _post(PlainCreate())
    assert resp.status_code == 201
    assert resp.headers["location"] == "/things/1"
    assert "N11a" not in resp.headers["x-asgimachine-trace"]
