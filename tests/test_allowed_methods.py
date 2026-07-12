"""allowed_methods: a thin callback defaulting to the ALLOWED_METHODS declaration
(PLAN.md §2.7). Override it only for per-request variation; the schema still reads
the declaration."""

from __future__ import annotations

from asgimachine.resource import Ctx, Resource
from asgimachine.schema import Operation, ResourceDescription, generate_openapi
from asgimachine.substrate.starlette import build_app, resource_route
from asgimachine.testing import make_client


class Toggled(Resource):
    ALLOWED_METHODS = frozenset({"GET", "HEAD", "DELETE"})

    def __init__(self, *, allow_delete: bool) -> None:
        self._allow_delete = allow_delete

    async def allowed_methods(self, ctx: Ctx) -> frozenset[str]:
        methods = {"GET", "HEAD"}
        if self._allow_delete:
            methods.add("DELETE")
        return frozenset(methods)

    async def delete_resource(self, ctx: Ctx) -> bool:
        return True

    async def represent(self, ctx: Ctx) -> object:
        return {}

    def describe(self) -> ResourceDescription:
        return ResourceDescription(get=Operation(summary="read"))


def _client(*, allow_delete: bool):
    return make_client(
        build_app([resource_route("/t", Toggled(allow_delete=allow_delete))])
    )


def test_callback_override_drives_405() -> None:
    # The runtime callback, not the declaration, decides 405.
    assert _client(allow_delete=True).request("DELETE", "/t").status_code == 204
    assert _client(allow_delete=False).request("DELETE", "/t").status_code == 405


def test_schema_documents_the_declaration() -> None:
    # The schema reads ALLOWED_METHODS, so DELETE is documented regardless.
    doc = generate_openapi(
        title="T", version="1", routes=[("/t", Toggled(allow_delete=False))]
    )
    assert set(doc["paths"]["/t"]) == {"get", "delete"}
