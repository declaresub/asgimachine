"""OpenAPI generation from resource describe() (PLAN.md §10, M5 acceptance).

Covers both the raw-dict-schema and the Pydantic-model paths (Pydantic optional),
path parameters, describe()-absent omission, and the app's /openapi.json route.
"""

from __future__ import annotations

from pydantic import BaseModel
from starlette.testclient import TestClient

from asgimachine.resource import Ctx, Resource
from asgimachine.schema import Operation, ResourceDescription, generate_openapi
from examples.notes_app import make_app


class Widget(BaseModel):
    name: str
    size: int


class WidgetResource(Resource):
    def describe(self) -> ResourceDescription:
        return ResourceDescription(
            get=Operation(summary="Get a widget", responses={200: Widget}),
        )

    async def represent(self, ctx: Ctx) -> object:
        return {}


class UndocumentedResource(Resource):
    async def represent(self, ctx: Ctx) -> object:
        return {}


def test_pydantic_model_becomes_json_schema() -> None:
    doc = generate_openapi(
        title="T", version="1", routes=[("/widget", WidgetResource())]
    )
    schema = doc["paths"]["/widget"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert set(schema["properties"]) == {"name", "size"}
    assert schema["properties"]["size"]["type"] == "integer"


def test_path_parameters_are_declared() -> None:
    doc = generate_openapi(
        title="T", version="1", routes=[("/widget/{id}", WidgetResource())]
    )
    params = doc["paths"]["/widget/{id}"]["get"]["parameters"]
    assert params == [
        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
    ]


def test_resource_without_describe_is_omitted() -> None:
    doc = generate_openapi(
        title="T", version="1", routes=[("/nope", UndocumentedResource())]
    )
    assert doc["paths"] == {}


def test_document_envelope() -> None:
    doc = generate_openapi(title="My API", version="9.9", routes=[])
    assert doc["openapi"] == "3.1.0"
    assert doc["info"] == {"title": "My API", "version": "9.9"}


# --- the dogfood app serves its own schema ---------------------------------


def test_openapi_endpoint_documents_graph_routes() -> None:
    doc = TestClient(make_app()).get("/openapi.json").json()
    assert doc["openapi"] == "3.1.0"
    assert set(doc["paths"]) == {"/health", "/notes", "/notes/{id}"}
    member = doc["paths"]["/notes/{id}"]
    assert set(member) == {"get", "put", "delete"}
    assert "requestBody" in member["put"]
    assert member["get"]["parameters"][0]["name"] == "id"


def test_command_routes_absent_from_schema() -> None:
    # The plain lane (/token, /openapi.json) is out of schema scope (graph only).
    doc = TestClient(make_app()).get("/openapi.json").json()
    assert "/token" not in doc["paths"]
    assert "/openapi.json" not in doc["paths"]


# --- auto-derived error surface (the graph is the schema) -------------------


class GuardedResource(Resource):
    ALLOWED_METHODS = frozenset({"GET", "PUT", "DELETE"})

    async def is_authorized(self, ctx: Ctx) -> bool | str:
        return True

    async def forbidden(self, ctx: Ctx) -> bool:
        return False

    async def resource_exists(self, ctx: Ctx) -> bool:
        return True

    async def generate_etag(self, ctx: Ctx) -> str | None:
        return '"e"'

    CONSUMES = ("application/json",)

    async def apply(self, ctx: Ctx, body: dict[str, object]) -> None:
        return None

    async def represent(self, ctx: Ctx) -> object:
        return {}

    def describe(self) -> ResourceDescription:
        return ResourceDescription(
            get=Operation(responses={200: {"type": "object"}}),
            put=Operation(responses={204: None}),
            delete=Operation(responses={204: None}),
        )


def _guarded() -> dict:
    doc = generate_openapi(
        title="T", version="1", routes=[("/g/{id}", GuardedResource())]
    )
    return doc["paths"]["/g/{id}"]


def test_error_surface_auto_derived_for_reads() -> None:
    # Declared only 200; 304/401/403/404/406/412 come from overridden callbacks.
    assert set(_guarded()["get"]["responses"]) == {
        "200",
        "304",
        "401",
        "403",
        "404",
        "406",
        "412",
    }


def test_write_error_surface_excludes_404() -> None:
    # PUT creates on a missing target, so no 404; body validation adds 400/415.
    assert set(_guarded()["put"]["responses"]) == {
        "204",
        "400",
        "401",
        "403",
        "412",
        "415",
    }


def test_delete_error_surface() -> None:
    assert set(_guarded()["delete"]["responses"]) == {"204", "401", "403", "404", "412"}


def test_methods_come_from_allowed_methods() -> None:
    # HEAD/OPTIONS are never documented; only ALLOWED_METHODS ∩ documentable.
    assert set(_guarded()) == {"get", "put", "delete"}


class PartialResource(Resource):
    ALLOWED_METHODS = frozenset({"GET", "DELETE"})

    async def delete_resource(self, ctx: Ctx) -> bool:
        return True

    async def represent(self, ctx: Ctx) -> object:
        return {}

    def describe(self) -> ResourceDescription:
        # DELETE is allowed but undeclared: still documented (default response).
        return ResourceDescription(get=Operation(responses={200: {"type": "object"}}))


def test_allowed_but_undeclared_method_is_documented() -> None:
    doc = generate_openapi(title="T", version="1", routes=[("/p", PartialResource())])
    delete = doc["paths"]["/p"]["delete"]
    assert set(delete["responses"]) == {"default"}


class StrictWriteResource(Resource):
    ALLOWED_METHODS = frozenset({"PUT"})

    async def valid_entity_length(self, ctx: Ctx) -> bool:
        return True

    async def valid_content_headers(self, ctx: Ctx) -> bool:
        return True

    async def is_conflict(self, ctx: Ctx) -> bool:
        return False

    CONSUMES = ("application/json",)

    async def apply(self, ctx: Ctx, body: dict[str, object]) -> None:
        return None

    async def represent(self, ctx: Ctx) -> object:
        return {}

    def describe(self) -> ResourceDescription:
        return ResourceDescription(put=Operation(responses={204: None}))


def test_write_validation_and_conflict_auto_errors() -> None:
    doc = generate_openapi(
        title="T", version="1", routes=[("/s", StrictWriteResource())]
    )
    assert set(doc["paths"]["/s"]["put"]["responses"]) == {
        "204",
        "400",
        "409",
        "413",
        "415",
        "501",
    }


# --- security ---------------------------------------------------------------


def test_security_schemes_and_document_default() -> None:
    doc = generate_openapi(
        title="T",
        version="1",
        routes=[("/w", WidgetResource())],
        security_schemes={"bearerAuth": {"type": "http", "scheme": "bearer"}},
        security=["bearerAuth"],
    )
    assert doc["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
    assert doc["security"] == [{"bearerAuth": []}]
    # An operation that doesn't declare security inherits the default (no key).
    assert "security" not in doc["paths"]["/w"]["get"]


class TypedResource(Resource):
    ALLOWED_METHODS = frozenset({"GET", "PUT"})
    CONSUMES = ("application/json",)

    async def represent(self, ctx: Ctx) -> Widget:
        return Widget(name="x", size=1)

    async def apply(self, ctx: Ctx, body: Widget) -> None:
        return None

    def describe(self) -> ResourceDescription:
        # No bodies declared — request derives from apply(body), 200 from represent.
        return ResourceDescription()


def test_models_derived_from_signatures() -> None:
    doc = generate_openapi(title="T", version="1", routes=[("/t", TypedResource())])
    op = doc["paths"]["/t"]
    get_schema = op["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert set(get_schema["properties"]) == {"name", "size"}
    put_schema = op["put"]["requestBody"]["content"]["application/json"]["schema"]
    assert set(put_schema["properties"]) == {"name", "size"}


def test_public_operation_overrides_security() -> None:
    class PublicResource(Resource):
        def describe(self) -> ResourceDescription:
            return ResourceDescription(get=Operation(security=[]))

        async def represent(self, ctx: Ctx) -> object:
            return {}

    doc = generate_openapi(
        title="T",
        version="1",
        routes=[("/p", PublicResource())],
        security=["bearerAuth"],
    )
    assert doc["paths"]["/p"]["get"]["security"] == []
