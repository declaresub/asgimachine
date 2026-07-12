"""Substrate-independent HTTP value objects and protocols.

This module (and ``core``/``resource``) must never import Starlette. It defines
the narrow seam the decision graph talks to: an :class:`HttpRequest` protocol it
reads from, and an :class:`HttpResponse` value object it returns. See PLAN.md
§2.6 and §6.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Protocol, runtime_checkable


def serialize(value: object) -> bytes:
    """Turn a value into response bytes (PLAN.md §6), shared by both lanes.

    ``None`` -> empty; bytes/str pass through; objects with ``model_dump_json``
    (Pydantic) use it (no hard dependency); everything else via ``json.dumps``.
    """

    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode()
    model_dump_json = getattr(value, "model_dump_json", None)
    if callable(model_dump_json):
        result = model_dump_json()  # Pydantic returns str
        return result.encode() if isinstance(result, str) else str(result).encode()
    return json.dumps(value).encode()


class Status(IntEnum):
    """HTTP status codes the v0 graph can select. Extended in later phases."""

    OK = 200
    CREATED = 201
    ACCEPTED = 202
    NO_CONTENT = 204
    MULTIPLE_CHOICES = 300
    MOVED_PERMANENTLY = 301
    TEMPORARY_REDIRECT = 307
    NOT_MODIFIED = 304
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    METHOD_NOT_ALLOWED = 405
    NOT_ACCEPTABLE = 406
    CONFLICT = 409
    GONE = 410
    PRECONDITION_FAILED = 412
    REQUEST_ENTITY_TOO_LARGE = 413
    UNSUPPORTED_MEDIA_TYPE = 415
    INTERNAL_SERVER_ERROR = 500
    NOT_IMPLEMENTED = 501
    SERVICE_UNAVAILABLE = 503


@runtime_checkable
class HttpRequest(Protocol):
    """The read-only view of a request the core depends on.

    Case-insensitive header access is required of ``headers`` (per RFC 9110);
    the Starlette adapter satisfies this. Kept minimal on purpose — the core
    only reads what the v0 graph needs.
    """

    @property
    def method(self) -> str: ...

    @property
    def path(self) -> str: ...

    @property
    def headers(self) -> Mapping[str, str]: ...

    @property
    def path_params(self) -> Mapping[str, str]: ...

    async def body(self) -> bytes: ...


# A response body is either buffered bytes or a live async byte stream (§8).
Body = bytes | AsyncIterator[bytes]


@dataclass(slots=True)
class HttpResponse:
    """What the core walk returns; the substrate turns it into a wire response."""

    status: int
    headers: dict[str, str] = field(default_factory=dict[str, str])
    body: Body = b""

    @property
    def is_stream(self) -> bool:
        return not isinstance(self.body, (bytes, bytearray))


class HaltResponse(Exception):
    """Short-circuit the walk with an explicit response (PLAN.md §6).

    Any callback or node may raise this to terminate the graph immediately.
    """

    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        super().__init__(f"halt {response.status}")
