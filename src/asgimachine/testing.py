"""Test helpers (PLAN.md §11).

``make_client`` gives the tier-1 seam: ``make_client(build_app([...]))`` over the
substrate adapter, with fakes constructor-injected into resources.
``assert_trace`` (tier 3) pins the exact node path a request walked, reading the
``X-Asgimachine-Trace`` header the core emits in debug mode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.testclient import TestClient

from .trace import TRACE_HEADER

if TYPE_CHECKING:
    import httpx
    from starlette.applications import Starlette


def make_client(app: Starlette) -> TestClient:
    """A Starlette ``TestClient`` over an asgimachine app."""

    return TestClient(app)


def assert_trace(response: httpx.Response, expected_nodes: list[str]) -> None:
    """Assert the request walked exactly ``expected_nodes`` (PLAN.md §9, §11).

    Requires the app to run in debug mode (``build_app(..., debug=True)``), which
    is what emits the ``X-Asgimachine-Trace`` header.
    """

    raw: str | None = response.headers.get(TRACE_HEADER)
    if raw is None:
        msg = (
            f"response has no {TRACE_HEADER} header; build the app with "
            "debug=True to enable decision tracing"
        )
        raise AssertionError(msg)
    actual = raw.split(",") if raw else []
    assert actual == expected_nodes, f"trace {actual} != expected {expected_nodes}"
