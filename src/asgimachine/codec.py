"""Transport codecs — the encoding concern, separated from resource logic.

A producer/acceptor conflates three jobs: build/consume the domain value, name
its model, and encode/decode the bytes. The last is a media-type-keyed transport
concern that's identical across resources, so it lives here instead. A resource
declares which media types it offers/accepts (``PRODUCES``/``CONSUMES``) and works
in domain values; the codec turns values into bytes and back.

Codecs are model-agnostic: ``decode`` yields the structured intermediate
(``json.loads``), and the *semantic* parse (structured -> a typed model) is a
separate, Pydantic-optional step in the core. The default registry is JSON;
inject others at the composition root.
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

from .http import serialize


@runtime_checkable
class Codec(Protocol):
    """Encodes a representation value to bytes and decodes a body to structure."""

    def encode(self, value: object) -> bytes: ...

    def decode(self, raw: bytes) -> object: ...


class JsonCodec:
    """``application/json`` codec. Encode reuses :func:`http.serialize` (Pydantic
    ``model_dump_json`` or ``json.dumps``); decode is a syntactic parse."""

    def encode(self, value: object) -> bytes:
        return serialize(value)

    def decode(self, raw: bytes) -> object:
        return json.loads(raw)


# The default registry — JSON only. Pass a superset to the composition root to
# support other media types.
DEFAULT_CODECS: dict[str, Codec] = {"application/json": JsonCodec()}
