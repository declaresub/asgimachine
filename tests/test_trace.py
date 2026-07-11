"""Trace assertions (PLAN.md §9, §11 tier 3).

Assert the exact node path for representative requests to verify graph wiring and
catch accidental short-circuits — driven through the real ``X-Asgimachine-Trace``
debug header via ``assert_trace``, not by poking core internals.
"""

from __future__ import annotations

from asgimachine.testing import assert_trace

from tests.conftest import FIXED_ETAG, Toggles


def test_full_get_trace_path(client_for) -> None:
    resp = client_for(Toggles()).get("/r")
    assert resp.status_code == 200
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4", "G7", "O18"])


def test_options_trace_stops_at_b3(client_for) -> None:
    resp = client_for(Toggles()).options("/r")
    assert resp.status_code == 200
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "B3"])


def test_304_trace_reaches_conditional_node(client_for) -> None:
    resp = client_for(Toggles()).get("/r", headers={"if-none-match": FIXED_ETAG})
    assert resp.status_code == 304
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4", "G7", "K13"])


def test_404_trace(client_for) -> None:
    resp = client_for(Toggles(exists=False)).get("/r")
    assert resp.status_code == 404
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4", "G7"])


def test_405_short_circuits_trace(client_for) -> None:
    resp = client_for(Toggles(methods=["GET", "HEAD"])).request("DELETE", "/r")
    assert resp.status_code == 405
    assert_trace(resp, ["B13", "B12", "B10"])


def test_501_short_circuits_trace(client_for) -> None:
    resp = client_for(Toggles()).request("BREW", "/r")
    assert resp.status_code == 501
    assert_trace(resp, ["B13", "B12"])


def test_503_short_circuits_trace(client_for) -> None:
    resp = client_for(Toggles(available=False)).get("/r")
    assert resp.status_code == 503
    assert_trace(resp, ["B13"])


def test_401_trace(client_for) -> None:
    resp = client_for(Toggles(authorized="Bearer")).get("/r")
    assert resp.status_code == 401
    assert_trace(resp, ["B13", "B12", "B10", "B8"])


def test_no_trace_header_without_debug() -> None:
    # Non-debug apps must not leak the trace header.
    from asgimachine import TRACE_HEADER
    from asgimachine.substrate.starlette import build_app, resource_route
    from asgimachine.testing import make_client
    from tests.conftest import ConfigurableResource

    app = build_app([resource_route("/r", ConfigurableResource(Toggles()))])
    resp = make_client(app).get("/r")
    assert resp.status_code == 200
    assert TRACE_HEADER.lower() not in resp.headers
