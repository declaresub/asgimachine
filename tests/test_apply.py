"""Close-the-loop request handling: parse, don't validate (PLAN.md §10, §6).

A typed ``body`` param on apply() makes the core decode + parse the request into
that model (400 on a bad body) and hand it in — the handler receives a typed
value, never a loose dict, and is total over it.
"""

from __future__ import annotations

from pydantic import BaseModel

from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route
from asgimachine.testing import assert_trace, make_client


class Widget(BaseModel):
    name: str
    qty: int


class WidgetResource(Resource):
    ALLOWED_METHODS = frozenset({"PUT"})
    CONSUMES = ("application/json",)

    def __init__(self) -> None:
        self.saved: Widget | None = None

    async def apply(self, ctx: Ctx, body: Widget) -> object:
        # body is a Widget — parsed and validated by the core.
        self.saved = body
        return {"name": body.name, "qty": body.qty}


def _client(resource: WidgetResource):
    return make_client(build_app([resource_route("/w", resource)], debug=True))


def test_valid_body_is_parsed_into_the_model() -> None:
    resource = WidgetResource()
    resp = _client(resource).put("/w", json={"name": "cog", "qty": 3})
    assert resp.status_code == 200
    assert resp.json() == {"name": "cog", "qty": 3}
    # The handler received a typed model, not a dict.
    assert isinstance(resource.saved, Widget)
    assert resource.saved.qty == 3
    assert_trace(
        resp,
        [
            "B13",
            "B12",
            "B10",
            "B9",
            "B8",
            "B7",
            "B6",
            "B5",
            "B4",
            "C4",
            "G7",
            "O14",
            "P0",
            "O20",
        ],
    )


def test_wrong_type_is_400() -> None:
    resp = _client(WidgetResource()).put("/w", json={"name": "cog", "qty": "lots"})
    assert resp.status_code == 400
    # The parse fails at P0, after the write-path conflict check (O14).
    assert_trace(
        resp,
        [
            "B13",
            "B12",
            "B10",
            "B9",
            "B8",
            "B7",
            "B6",
            "B5",
            "B4",
            "C4",
            "G7",
            "O14",
            "P0",
        ],
    )


def test_missing_field_is_400() -> None:
    resp = _client(WidgetResource()).put("/w", json={"name": "cog"})
    assert resp.status_code == 400


def test_malformed_json_is_400() -> None:
    resp = _client(WidgetResource()).put(
        "/w", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert resp.status_code == 400


class _RecursionCodec:
    """A codec whose decode raises RecursionError — the deterministic stand-in for
    deeply nested JSON (``json.loads`` on ~500k-deep input raises RecursionError,
    verified). RecursionError is not a ValueError, so before the fix it escaped the
    parse boundary as a 500 instead of a 400."""

    def encode(self, value: object) -> bytes:
        return b"{}"

    def decode(self, raw: bytes) -> object:
        raise RecursionError("maximum recursion depth exceeded")


def test_recursion_error_on_decode_is_400() -> None:
    client = make_client(
        build_app(
            [
                resource_route(
                    "/w",
                    WidgetResource(),
                    codecs={"application/json": _RecursionCodec()},
                )
            ],
            debug=True,
        )
    )
    resp = client.put("/w", json={"name": "cog", "qty": 3})
    assert resp.status_code == 400  # a malformed (too-deep) body, not a 500
