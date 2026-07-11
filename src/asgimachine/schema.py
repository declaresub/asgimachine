"""OpenAPI 3.1 generation from resource declarations (PLAN.md §10).

The least-designed piece (§14) — this is an honest first cut. Resources opt in by
implementing :meth:`Resource.describe`; :func:`generate_openapi` walks the
``(path, resource)`` pairs and emits an OpenAPI document. Pydantic is optional: a
model exposing ``model_json_schema()`` is converted, and a raw JSON-Schema
``dict`` is used as-is. A route without a ``describe()`` is simply absent from the
schema — acceptable during incremental adoption (§10).

Known limits of this cut: no security schemes, examples, or response-header
declarations; a model's ``$defs`` are inlined per-operation rather than deduped
into ``components``; the command lane is out of scope (graph routes only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .resource import Resource

# A model is either a class exposing ``model_json_schema()`` (e.g. a Pydantic
# BaseModel) or a raw JSON Schema dict. ``None`` means "no body".
Model = type | dict[str, Any]


@dataclass(frozen=True, slots=True)
class Operation:
    """One HTTP method's typed surface on a resource."""

    summary: str | None = None
    request: Model | None = None
    responses: Mapping[int, Model | None] | None = None


@dataclass(frozen=True, slots=True)
class ResourceDescription:
    """A resource's operations, one per HTTP method it handles."""

    get: Operation | None = None
    post: Operation | None = None
    put: Operation | None = None
    patch: Operation | None = None
    delete: Operation | None = None

    def operations(self) -> list[tuple[str, Operation]]:
        pairs: list[tuple[str, Operation]] = []
        for method in ("get", "post", "put", "patch", "delete"):
            op: Operation | None = getattr(self, method)
            if op is not None:
                pairs.append((method, op))
        return pairs


_PARAM_RE = re.compile(r"\{([^}:]+)(?::[^}]+)?\}")


def _schema_for(model: Model | None) -> dict[str, Any] | None:
    if model is None:
        return None
    if isinstance(model, dict):
        return model
    model_json_schema = getattr(model, "model_json_schema", None)
    if callable(model_json_schema):
        result = model_json_schema()
        return (
            cast("dict[str, Any]", result)
            if isinstance(result, dict)
            else {"type": "object"}
        )
    return {"type": "object"}


def _content(model: Model | None) -> dict[str, Any]:
    schema = _schema_for(model)
    if schema is None:
        return {}
    return {"content": {"application/json": {"schema": schema}}}


def _response(status: int, model: Model | None) -> dict[str, Any]:
    try:
        description = HTTPStatus(status).phrase
    except ValueError:
        description = ""
    return {"description": description, **_content(model)}


def _parameters(path: str) -> list[dict[str, Any]]:
    return [
        {"name": name, "in": "path", "required": True, "schema": {"type": "string"}}
        for name in _PARAM_RE.findall(path)
    ]


def _operation(op: Operation, params: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if op.summary is not None:
        result["summary"] = op.summary
    if params:
        result["parameters"] = params
    if op.request is not None:
        result["requestBody"] = {"required": True, **_content(op.request)}
    responses = op.responses or {}
    result["responses"] = {
        str(status): _response(status, model) for status, model in responses.items()
    }
    return result


def generate_openapi(
    *, title: str, version: str, routes: Sequence[tuple[str, Resource]]
) -> dict[str, Any]:
    """Emit an OpenAPI 3.1 document for the resources that declare a ``describe()``."""

    paths: dict[str, Any] = {}
    for path, resource in routes:
        description = resource.describe()
        if description is None:
            continue
        params = _parameters(path)
        path_item = {
            method: _operation(op, params) for method, op in description.operations()
        }
        if path_item:
            paths[path] = path_item
    return {
        "openapi": "3.1.0",
        "info": {"title": title, "version": version},
        "paths": paths,
    }
