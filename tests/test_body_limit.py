"""Request-body size limit + Content-Length framing (PLAN.md §6, review #6).

The limit is a resource declaration (``MAX_BODY_BYTES``): B4 ``valid_entity_length``
rejects a declared Content-Length over it (413), and the substrate bounds the
*actual* read at the same value (the backstop for a chunked or lying
Content-Length). A read whose length disagrees with Content-Length is a framing
error (400). The command lane sets its own cap via ``command_route``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

import pytest

from asgimachine.command import Command, json_response
from asgimachine.http import BodyMalformed, BodyTooLarge, HttpRequest, HttpResponse
from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import (
    _StarletteRequest,
    build_app,
    command_route,
    resource_route,
)
from asgimachine.testing import make_client


# --- unit: _StarletteRequest.body() bounds and framing ----------------------
# A minimal stand-in for Starlette's Request exposing only what body() reads.


class _FakeRequest:
    def __init__(self, headers: Mapping[str, str], chunks: list[bytes]) -> None:
        self.headers = headers
        self._chunks = chunks

    async def stream(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


def _req(
    headers: Mapping[str, str], chunks: list[bytes], cap: int
) -> _StarletteRequest:
    return _StarletteRequest(_FakeRequest(headers, chunks), cap)  # type: ignore[arg-type]


async def test_body_reads_within_cap() -> None:
    req = _req({"content-length": "5"}, [b"he", b"llo"], cap=1000)
    assert await req.body() == b"hello"


async def test_body_is_cached_across_calls() -> None:
    req = _req({"content-length": "5"}, [b"hello"], cap=1000)
    assert await req.body() == b"hello"
    assert await req.body() == b"hello"  # second call: cache, not a re-read


async def test_declared_length_over_cap_fast_rejects() -> None:
    # Content-Length over the cap is rejected before reading a single chunk.
    req = _req({"content-length": "2000"}, [b"x" * 2000], cap=1000)
    with pytest.raises(BodyTooLarge):
        await req.body()


async def test_stream_over_cap_rejects_without_content_length() -> None:
    # No Content-Length (chunked): the running total trips the cap.
    req = _req({}, [b"a" * 600, b"b" * 600], cap=1000)
    with pytest.raises(BodyTooLarge):
        await req.body()


async def test_length_disagreement_is_malformed() -> None:
    # Bytes read != declared Content-Length -> a framing error (400).
    req = _req({"content-length": "100"}, [b"short"], cap=1000)
    with pytest.raises(BodyMalformed):
        await req.body()


# --- graph lane: MAX_BODY_BYTES drives B4 -----------------------------------


class TinyResource(Resource):
    ALLOWED_METHODS = frozenset({"PUT"})
    CONSUMES = ("application/json",)
    MAX_BODY_BYTES = 50

    async def apply(self, ctx: Ctx, body: object) -> object:
        return {"ok": True}


def _tiny_client():
    return make_client(build_app([resource_route("/t", TinyResource())], debug=True))


def test_honest_content_length_over_limit_is_413() -> None:
    body = b'{"x":"' + b"a" * 200 + b'"}'  # httpx sets a correct, over-cap CL
    resp = _tiny_client().put(
        "/t", content=body, headers={"content-type": "application/json"}
    )
    assert resp.status_code == 413
    # The graph made the call at B4 (valid_entity_length), not the substrate.
    assert resp.headers["x-asgimachine-trace"].split(",")[-1] == "B4"


def test_chunked_body_over_cap_is_413() -> None:
    # A generator body -> chunked, no Content-Length -> B4 passes, the substrate
    # read cap (the backstop) trips instead.
    def gen():
        yield b"a" * 30
        yield b"b" * 30  # 60 > 50

    resp = _tiny_client().put(
        "/t", content=gen(), headers={"content-type": "application/json"}
    )
    assert resp.status_code == 413


def test_body_within_limit_is_accepted() -> None:
    resp = _tiny_client().put(
        "/t", content=b'{"x":1}', headers={"content-type": "application/json"}
    )
    assert resp.status_code == 200


# --- command lane: cap set on the route -------------------------------------


class EchoCommand(Command):
    async def handle(self, request: HttpRequest) -> HttpResponse:
        return json_response({"len": len(await request.body())})


def _cmd_client():
    return make_client(
        build_app([command_route("/c", EchoCommand(), max_body_bytes=50)])
    )


def test_command_body_over_cap_is_413() -> None:
    assert _cmd_client().post("/c", content=b"x" * 200).status_code == 413


def test_command_small_body_ok() -> None:
    resp = _cmd_client().post("/c", content=b"hello")
    assert resp.status_code == 200
    assert resp.json() == {"len": 5}
