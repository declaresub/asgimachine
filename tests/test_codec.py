"""Codec registry: encoding separated from resource logic, and injectable."""

from __future__ import annotations

from asgimachine.codec import JsonCodec
from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route
from asgimachine.testing import make_client


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
