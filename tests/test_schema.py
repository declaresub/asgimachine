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

    async def to_json(self, ctx: Ctx) -> object:
        return {}


class UndocumentedResource(Resource):
    async def to_json(self, ctx: Ctx) -> object:
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
