"""Serve-anyway negotiation (§4 v4, RFC 9110 §12.1): a resource may disregard an
unsatisfiable Accept and serve its default representation instead of 406.

Node C4a, gated by the IGNORE_UNACCEPTABLE declaration (read via the thin
ignore_unacceptable callback). Default False preserves the hard 406.
"""

from __future__ import annotations

from typing import ClassVar

from asgimachine.resource import Ctx, Resource
from asgimachine.schema import Operation, ResourceDescription, generate_openapi
from asgimachine.substrate.starlette import build_app, resource_route
from asgimachine.testing import assert_trace, make_client


class Strict(Resource):
    async def represent(self, ctx: Ctx) -> object:
        return {"ok": True}


class Lenient(Resource):
    IGNORE_UNACCEPTABLE: ClassVar[bool] = True

    async def represent(self, ctx: Ctx) -> object:
        return {"ok": True}


_UNACCEPTABLE = {"accept": "text/csv"}  # not in the default PRODUCES (json)


def test_unsatisfiable_accept_is_406_by_default() -> None:
    resp = make_client(build_app([resource_route("/s", Strict())], debug=True)).get(
        "/s", headers=_UNACCEPTABLE
    )
    assert resp.status_code == 406
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4"])


def test_ignore_unacceptable_serves_default() -> None:
    resp = make_client(build_app([resource_route("/l", Lenient())], debug=True)).get(
        "/l", headers=_UNACCEPTABLE
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"  # PRODUCES[0]
    assert resp.json() == {"ok": True}
    # C4a fires (in place of a 406 at C4).
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4a", "G7", "O18"])


def test_satisfiable_accept_still_negotiates_normally() -> None:
    # The escape only triggers on a mismatch; a matching Accept is unaffected.
    resp = make_client(build_app([resource_route("/l", Lenient())])).get(
        "/l", headers={"accept": "application/json"}
    )
    assert resp.status_code == 200


def test_per_request_callback_override() -> None:
    # The declaration is the default; overriding the callback allows per-request
    # variation (e.g. ignore for API clients only).
    class ByHeader(Resource):
        async def ignore_unacceptable(self, ctx: Ctx) -> bool:
            return ctx.request.headers.get("x-api-client") == "1"

        async def represent(self, ctx: Ctx) -> object:
            return {"ok": True}

    client = make_client(build_app([resource_route("/h", ByHeader())]))
    assert client.get("/h", headers=_UNACCEPTABLE).status_code == 406
    assert (
        client.get("/h", headers={**_UNACCEPTABLE, "x-api-client": "1"}).status_code
        == 200
    )


# --- schema: 406 drops out when the resource declares IGNORE_UNACCEPTABLE ----


def _error_statuses(resource: Resource) -> set[str]:
    class Described(type(resource)):  # type: ignore[misc]
        def describe(self) -> ResourceDescription:
            return ResourceDescription(
                get=Operation(responses={200: {"type": "object"}})
            )

    doc = generate_openapi(title="T", version="1", routes=[("/x", Described())])
    return set(doc["paths"]["/x"]["get"]["responses"])


def test_schema_includes_406_by_default() -> None:
    assert "406" in _error_statuses(Strict())


def test_schema_drops_406_when_ignoring() -> None:
    assert "406" not in _error_statuses(Lenient())
