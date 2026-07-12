"""300 Multiple Choices (PLAN.md §4 v3, node O18).

When a resource offers several representations and opts into ``multiple_choices``,
it returns 300 with the list of offered media types (from PRODUCES).
"""

from __future__ import annotations

from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route
from asgimachine.testing import assert_trace, make_client


class ChoiceResource(Resource):
    PRODUCES = ("application/json", "text/plain")

    def __init__(self, *, ambiguous: bool) -> None:
        self._ambiguous = ambiguous

    async def multiple_choices(self, ctx: Ctx) -> bool:
        return self._ambiguous

    async def represent(self, ctx: Ctx) -> object:
        return {"ok": True}


def _client(*, ambiguous: bool):
    resource = ChoiceResource(ambiguous=ambiguous)
    return make_client(build_app([resource_route("/c", resource)], debug=True))


def test_multiple_choices_returns_300_with_offers() -> None:
    resp = _client(ambiguous=True).get("/c", headers={"accept": "application/json"})
    assert resp.status_code == 300
    assert resp.json() == {"choices": ["application/json", "text/plain"]}
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4", "G7", "O18"])


def test_no_multiple_choices_returns_200() -> None:
    resp = _client(ambiguous=False).get("/c", headers={"accept": "application/json"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
