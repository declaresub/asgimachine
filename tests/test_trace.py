"""Trace assertions (PLAN.md §9, §11 tier 3).

Assert the exact node path for representative requests to verify graph wiring and
catch accidental short-circuits. Until the M1 debug header + ``assert_trace``
helper land, we drive the walk with a ``Ctx`` we own and read ``ctx.trace``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC

import pytest

from asgimachine import Ctx, HttpResponse, Resource
from asgimachine import core
from asgimachine.http import HaltResponse


@dataclass(slots=True)
class FakeRequest:
    method: str
    path: str = "/r"
    _headers: dict[str, str] = field(default_factory=dict)

    @property
    def headers(self) -> dict[str, str]:
        # Case-insensitive lookups, matching the HttpRequest protocol contract.
        return {k.lower(): v for k, v in self._headers.items()}

    async def body(self) -> bytes:
        return b""


class TracingResource(Resource):
    async def allowed_methods(self, ctx: Ctx) -> list[str]:
        return ["GET", "HEAD"]

    async def generate_etag(self, ctx: Ctx) -> str | None:
        return '"t"'

    async def last_modified(self, ctx: Ctx) -> datetime | None:
        return datetime(2026, 1, 1, tzinfo=UTC)

    async def to_json(self, ctx: Ctx) -> object:
        return {"ok": True}


async def _walk(request: FakeRequest) -> tuple[HttpResponse, list[str]]:
    """Drive the walk with our own Ctx so we can read the trace it accumulates."""

    ctx = Ctx(request=request)
    try:
        response = await core._walk(TracingResource(), ctx)
    except HaltResponse as halt:
        response = halt.response
    return response, ctx.trace.nodes


async def test_full_get_trace_path() -> None:
    response, nodes = await _walk(FakeRequest(method="GET"))
    assert response.status == 200
    assert nodes == ["B13", "B12", "B10", "B8", "B7", "C4", "G7", "O18"]


async def test_options_trace_stops_at_b3() -> None:
    response, nodes = await _walk(FakeRequest(method="OPTIONS"))
    assert response.status == 200
    assert nodes == ["B13", "B12", "B10", "B8", "B7", "B3"]


async def test_304_trace_reaches_conditional_node() -> None:
    response, nodes = await _walk(
        FakeRequest(method="GET", _headers={"If-None-Match": '"t"'}),
    )
    assert response.status == 304
    assert nodes[-1] == "K13"


@pytest.mark.parametrize(
    ("method", "expected_status", "expected_nodes"),
    [
        ("DELETE", 405, ["B13", "B12", "B10"]),
        ("BREW", 501, ["B13", "B12"]),
    ],
)
async def test_short_circuit_traces(method, expected_status, expected_nodes) -> None:
    response, nodes = await _walk(FakeRequest(method=method))
    assert response.status == expected_status
    assert nodes == expected_nodes
