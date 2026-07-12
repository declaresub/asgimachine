"""OpenAPI 3.1 generation from resource declarations (PLAN.md §10).

The generator leans on what the framework uniquely knows — the decision graph is
itself a schema of outcomes:

- **Methods** come from ``Resource.ALLOWED_METHODS`` (static since it's resource
  shape, not behavior). ``describe()`` no longer repeats the method list.
- **The error/status surface is auto-derived** from which callbacks a subclass
  overrides: override ``is_authorized`` and you get 401; ``forbidden`` → 403;
  ``resource_exists`` → 404; ``content_types_accepted`` → 415/400; ``generate_etag``
  → 304/412; and so on, method-aware. So ``describe()`` declares only the
  *success* bodies and the generator fills in the errors.
- **Security** is declared: pass ``security_schemes`` + a document-level
  ``security`` default; a per-operation ``Operation.security`` overrides it
  (``[]`` marks a public operation).

Pydantic is optional (a model with ``model_json_schema()`` is converted, a raw
JSON-Schema dict is used as-is). A route without a ``describe()`` is absent from
the schema — acceptable during incremental adoption (§10).

Known limits (first cut): the auto-error mapping is a documented heuristic, not a
proof; no examples or response-header declarations; model ``$defs`` are inlined
per-operation rather than deduped into ``components``; the command lane is out of
scope (graph routes only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast, get_type_hints

from .resource import Resource

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# A model is either a class exposing ``model_json_schema()`` (e.g. a Pydantic
# BaseModel) or a raw JSON Schema dict. ``None`` means "no body".
Model = type | dict[str, Any]

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
_EXISTENCE_METHODS = frozenset({"GET", "HEAD", "DELETE", "PATCH"})
_READ_METHODS = frozenset({"GET", "HEAD"})
_DOCUMENTABLE = ("get", "post", "put", "patch", "delete")


@dataclass(frozen=True, slots=True)
class Operation:
    """One HTTP method's declared surface. Errors are auto-derived; declare the
    success bodies. ``security``: ``None`` inherits the document default, ``[]``
    marks the operation public, ``["name"]`` requires those schemes."""

    summary: str | None = None
    request: Model | None = None
    responses: Mapping[int, Model | None] | None = None
    security: Sequence[str] | None = None


@dataclass(frozen=True, slots=True)
class ResourceDescription:
    """A resource's per-method declarations (bodies + summaries). Methods absent
    here but present in ``ALLOWED_METHODS`` are still documented (auto-errors
    only)."""

    get: Operation | None = None
    post: Operation | None = None
    put: Operation | None = None
    patch: Operation | None = None
    delete: Operation | None = None


_PARAM_RE = re.compile(r"\{([^}:]+)(?::[^}]+)?\}")


def _overrides(resource: Resource, name: str) -> bool:
    return getattr(type(resource), name) is not getattr(Resource, name)


def _auto_error_statuses(resource: Resource, method: str) -> set[int]:
    """The candidate error statuses for ``method``, from overridden callbacks."""

    statuses: set[int] = set()
    if _overrides(resource, "is_authorized"):
        statuses.add(401)
    if _overrides(resource, "forbidden"):
        statuses.add(403)
    if method in _EXISTENCE_METHODS and _overrides(resource, "resource_exists"):
        statuses.add(404)
    if method in _READ_METHODS:
        statuses.add(406)  # content negotiation always runs for a representation
    if method in _BODY_METHODS:
        if resource.CONSUMES:
            # A parsed body -> 415 (unknown Content-Type) + 400 (parse failure).
            statuses.add(415)
            statuses.add(400)
        if _overrides(resource, "malformed_request"):
            statuses.add(400)
        if _overrides(resource, "valid_entity_length"):
            statuses.add(413)
        if _overrides(resource, "valid_content_headers"):
            statuses.add(501)
    if method in _BODY_METHODS and _overrides(resource, "is_conflict"):
        statuses.add(409)
    if _overrides(resource, "generate_etag") or _overrides(resource, "last_modified"):
        statuses.add(412)
        if method in _READ_METHODS:
            statuses.add(304)
    return statuses


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


def _security_for(declared: Operation | None) -> list[dict[str, list[str]]] | None:
    names = declared.security if declared is not None else None
    if names is None:
        return None  # inherit the document-level default (omit from the operation)
    return [{name: []} for name in names]


def _model_hint(func: object, key: str) -> type | None:
    """A Pydantic-model annotation on ``func``'s ``key`` param/return, or None.

    Only real model types (exposing ``model_json_schema``) are derived; loose
    annotations (``object``/``dict``/``None``) fall back to what ``describe()``
    declares."""

    try:
        hint = get_type_hints(func).get(key)
    except Exception:  # noqa: BLE001 — unresolvable annotation -> no derivation
        return None
    if isinstance(hint, type) and hasattr(hint, "model_json_schema"):
        return hint
    return None


def _operation(
    method: str,
    declared: Operation | None,
    resource: Resource,
    params: list[dict[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if declared is not None and declared.summary is not None:
        result["summary"] = declared.summary
    if params:
        result["parameters"] = params

    # requestBody: declared, else derived from apply()'s typed body param.
    request = declared.request if declared is not None else None
    if request is None and method.upper() in _BODY_METHODS:
        request = _model_hint(type(resource).apply, "body")
    if request is not None:
        result["requestBody"] = {"required": True, **_content(request)}

    responses: dict[str, Any] = {}
    declared_responses = (declared.responses if declared is not None else None) or {}
    for status, model in declared_responses.items():
        responses[str(status)] = _response(status, model)
    # 200 for a read: declared, else derived from represent()'s return type.
    if method.upper() in _READ_METHODS and "200" not in responses:
        derived = _model_hint(type(resource).represent, "return")
        if derived is not None:
            responses["200"] = _response(200, derived)
    for status in sorted(_auto_error_statuses(resource, method.upper())):
        responses.setdefault(str(status), _response(status, None))
    if not responses:
        # A method in ALLOWED_METHODS with nothing declared or derived: keep the
        # document valid (OpenAPI requires a non-empty responses object).
        responses["default"] = {"description": ""}
    result["responses"] = responses

    security = _security_for(declared)
    if security is not None:
        result["security"] = security
    return result


def generate_openapi(
    *,
    title: str,
    version: str,
    routes: Sequence[tuple[str, Resource]],
    security_schemes: Mapping[str, dict[str, Any]] | None = None,
    security: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Emit an OpenAPI 3.1 document for the resources that declare a ``describe()``.

    ``security_schemes`` populates ``components.securitySchemes``; ``security`` is
    the document-level default requirement (scheme names), overridable per
    operation via ``Operation.security``.
    """

    paths: dict[str, Any] = {}
    for path, resource in routes:
        description = resource.describe()
        if description is None:
            continue
        params = _parameters(path)
        declared_ops: dict[str, Operation | None] = {
            "get": description.get,
            "post": description.post,
            "put": description.put,
            "patch": description.patch,
            "delete": description.delete,
        }
        path_item: dict[str, Any] = {}
        for method in _DOCUMENTABLE:
            if method.upper() not in resource.ALLOWED_METHODS:
                continue
            path_item[method] = _operation(
                method, declared_ops[method], resource, params
            )
        if path_item:
            paths[path] = path_item

    document: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {"title": title, "version": version},
        "paths": paths,
    }
    if security is not None:
        document["security"] = [{name: []} for name in security]
    if security_schemes is not None:
        document["components"] = {"securitySchemes": dict(security_schemes)}
    return document
