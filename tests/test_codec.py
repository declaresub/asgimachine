"""Codec registry: encoding separated from resource logic, and injectable."""

from __future__ import annotations

from dataclasses import dataclass, field

from asgimachine.codec import Codec, JsonCodec
from asgimachine.core import run
from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route
from asgimachine.testing import make_client


@dataclass
class _FakeRequest:
    method: str = "GET"
    path: str = "/"
    headers: dict[str, str] = field(default_factory=dict[str, str])
    path_params: dict[str, str] = field(default_factory=dict[str, str])
    route: str | None = None

    async def body(self) -> bytes:
        return b""


class Greeter(Resource):
    PRODUCES = ("application/json", "text/plain")

    async def represent(self, ctx: Ctx) -> object:
        return {"hello": "world"}


class PlainCodec:
    """A trivial text/plain codec that renders a dict as key=value lines."""

    def encode(self, value: object) -> bytes:
        assert isinstance(value, dict)
        return "\n".join(f"{k}={v}" for k, v in value.items()).encode()

    def decode(self, raw: bytes) -> object:
        return raw.decode()


def _client():
    codecs = {"application/json": JsonCodec(), "text/plain": PlainCodec()}
    return make_client(build_app([resource_route("/g", Greeter(), codecs=codecs)]))


def test_default_codec_encodes_json() -> None:
    resp = _client().get("/g", headers={"accept": "application/json"})
    assert resp.status_code == 200
    assert resp.json() == {"hello": "world"}


def test_injected_codec_encodes_its_format() -> None:
    resp = _client().get("/g", headers={"accept": "text/plain"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/plain"
    assert resp.text == "hello=world"


def test_unoffered_media_type_is_406() -> None:
    resp = _client().get("/g", headers={"accept": "application/xml"})
    assert resp.status_code == 406


async def test_injected_codecs_are_copied_per_request() -> None:
    # An injected registry is shared by reference across every request; ctx must
    # get its own copy so one request mutating ctx.codecs can't corrupt others.
    injected: dict[str, Codec] = {"application/json": JsonCodec()}
    original_keys = set(injected)

    class Mutating(Resource):
        async def represent(self, ctx: Ctx) -> object:
            ctx.codecs["application/injected-mutation"] = JsonCodec()
            return {"ok": True}

    await run(Mutating(), _FakeRequest(), codecs=injected)
    # The caller's dict is untouched — ctx received a copy, not the same object.
    assert set(injected) == original_keys
