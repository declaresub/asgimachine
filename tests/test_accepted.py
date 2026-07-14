"""Async request-reply hand-off: accepted() -> 202 + Location (node O20a).

A write handler that enqueues work it can't finish inside the request budget
returns a monitor URL; the graph responds 202 Accepted + Location instead of a
completed 200/201/204. Covers POST (action + create) and PUT; DELETE keeps its
own 202 via delete_completed.
"""

from __future__ import annotations

from asgimachine.resource import Ctx, Resource
from asgimachine.schema import Operation, ResourceDescription, generate_openapi
from asgimachine.substrate.starlette import build_app, resource_route
from asgimachine.testing import assert_trace, make_client

MONITOR = "/jobs/abc123"


class Enqueue(Resource):
    """A POST-action resource that hands off to a background job."""

    ALLOWED_METHODS = frozenset({"POST", "PUT"})
    CONSUMES = ("application/json",)

    def __init__(self, *, is_create: bool = False, body: object = None) -> None:
        self._is_create = is_create
        self._body = body
        self.ran = False

    async def post_is_create(self, ctx: Ctx) -> bool:
        return self._is_create

    async def create_path(self, ctx: Ctx) -> str:
        return "/things/new"

    async def process_post(self, ctx: Ctx) -> object:
        self.ran = True  # the enqueue
        return self._body

    async def apply(self, ctx: Ctx, body: object) -> object:
        self.ran = True
        return self._body

    async def accepted(self, ctx: Ctx) -> str | None:
        return MONITOR


def _client(resource: Resource):
    return make_client(build_app([resource_route("/r", resource)], debug=True))


def test_post_action_accepted_is_202() -> None:
    resource = Enqueue()
    resp = _client(resource).post("/r", json={"x": 1})
    assert resp.status_code == 202
    assert resp.headers["location"] == MONITOR
    assert resource.ran  # the handler enqueued before the 202
    assert resp.content == b""  # no body when the handler returns None
    # POST-action uses process_post (no _apply), so no P0 body-parse node.
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
            "O20a",
        ],
    )


def test_post_create_accepted_overrides_created_location() -> None:
    # The monitor Location replaces the would-be created-resource Location.
    resp = _client(Enqueue(is_create=True)).post("/r", json={"x": 1})
    assert resp.status_code == 202
    assert resp.headers["location"] == MONITOR


def test_put_accepted_is_202() -> None:
    resp = _client(Enqueue()).put("/r", json={"x": 1})
    assert resp.status_code == 202
    assert resp.headers["location"] == MONITOR


def test_accepted_may_carry_a_status_body() -> None:
    resp = _client(Enqueue(body={"status": "pending"})).post("/r", json={"x": 1})
    assert resp.status_code == 202
    assert resp.json() == {"status": "pending"}
    assert resp.headers["content-type"] == "application/json"


def test_default_never_accepts() -> None:
    class Plain(Resource):
        ALLOWED_METHODS = frozenset({"POST"})

        async def process_post(self, ctx: Ctx) -> object:
            return {"done": True}

    resp = _client(Plain()).post("/r")
    assert resp.status_code == 200  # no accepted() -> normal framing
    assert "O20a" not in resp.headers["x-asgimachine-trace"]


def test_see_other_takes_precedence_on_post() -> None:
    # A resource that (mis)configures both: on POST, see_other (303) is checked
    # first, so 303 wins over 202.
    class Both(Enqueue):
        async def see_other(self, ctx: Ctx) -> str | None:
            return "/result/1"

    resp = make_client(build_app([resource_route("/r", Both())])).post(
        "/r", json={"x": 1}, follow_redirects=False
    )
    assert resp.status_code == 303


def test_schema_surfaces_202_when_accepted() -> None:
    class Described(Enqueue):
        def describe(self) -> ResourceDescription:
            return ResourceDescription(
                post=Operation(responses={200: {"type": "object"}})
            )

    doc = generate_openapi(title="T", version="1", routes=[("/r", Described())])
    assert "202" in doc["paths"]["/r"]["post"]["responses"]
