"""The G7-false branch — create / redirect / gone (PLAN.md §4 v2, M2 slice 4).

When resource_exists is False: PUT creates (201), If-Match -> 412, and
previously_existed routes to 301/307/410; otherwise 404.
"""

from __future__ import annotations


from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route
from asgimachine.testing import assert_trace, make_client


class MissingResource(Resource):
    """A resource that does not yet exist; configurable prior-existence/redirects."""

    def __init__(
        self,
        store: dict[str, str],
        *,
        conflict: bool = False,
        prior: bool = False,
        moved_perm: str | None = None,
        moved_temp: str | None = None,
    ) -> None:
        self._store = store
        self._conflict = conflict
        self._prior = prior
        self._moved_perm = moved_perm
        self._moved_temp = moved_temp

    ALLOWED_METHODS = frozenset({"GET", "PUT", "DELETE"})

    async def resource_exists(self, ctx: Ctx) -> bool:
        return False

    CONSUMES = ("application/json",)

    async def apply(self, ctx: Ctx, body: dict[str, str]) -> None:
        self._store[body["id"]] = body["content"]
        return None

    async def is_conflict(self, ctx: Ctx) -> bool:
        return self._conflict

    async def previously_existed(self, ctx: Ctx) -> bool:
        return self._prior

    async def moved_permanently(self, ctx: Ctx) -> str | None:
        return self._moved_perm

    async def moved_temporarily(self, ctx: Ctx) -> str | None:
        return self._moved_temp

    async def represent(self, ctx: Ctx) -> object:
        return {}


def _client(resource: MissingResource):
    return make_client(build_app([resource_route("/m", resource)], debug=True))


def test_put_to_missing_creates_201() -> None:
    store: dict[str, str] = {}
    resp = _client(MissingResource(store)).put(
        "/m", json={"id": "a", "content": "made"}
    )
    assert resp.status_code == 201
    assert store == {"a": "made"}
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
            "I7",
            "P3",
            "P0",
            "O20",
        ],
    )


def test_put_create_conflict_is_409() -> None:
    resp = _client(MissingResource({}, conflict=True)).put(
        "/m", json={"id": "a", "content": "x"}
    )
    assert resp.status_code == 409


def test_if_match_on_missing_is_412() -> None:
    resp = _client(MissingResource({})).put(
        "/m", json={"id": "a", "content": "x"}, headers={"if-match": '"e"'}
    )
    assert resp.status_code == 412
    assert resp.request.method == "PUT"


def test_get_missing_is_404() -> None:
    resp = _client(MissingResource({})).get("/m")
    assert resp.status_code == 404
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4", "G7", "K7", "L7"])


def test_previously_existed_moved_permanently_301() -> None:
    resp = _client(MissingResource({}, prior=True, moved_perm="/new")).get(
        "/m", follow_redirects=False
    )
    assert resp.status_code == 301
    assert resp.headers["location"] == "/new"
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4", "G7", "K7", "K5"])


def test_previously_existed_moved_temporarily_307() -> None:
    resp = _client(MissingResource({}, prior=True, moved_temp="/elsewhere")).get(
        "/m", follow_redirects=False
    )
    assert resp.status_code == 307
    assert resp.headers["location"] == "/elsewhere"


def test_previously_existed_gone_410() -> None:
    resp = _client(MissingResource({}, prior=True)).get("/m")
    assert resp.status_code == 410
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4", "G7", "K7", "M5"])
