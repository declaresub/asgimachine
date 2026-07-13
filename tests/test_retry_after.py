"""Retry-After on the availability node (B13 -> 503).

``service_available`` may return a hint instead of a bare bool: an int
(delta-seconds) or a datetime (an HTTP-date), which the graph emits as a
``Retry-After`` header (RFC 9110 §10.2.3). ``True`` stays available; ``False``
is a 503 with no hint.
"""

from __future__ import annotations

from datetime import UTC, datetime

from asgimachine.conditional import http_date
from tests.conftest import Toggles


def test_available_is_200(client_for) -> None:
    assert client_for(Toggles()).get("/r").status_code == 200


def test_false_is_503_without_hint(client_for) -> None:
    resp = client_for(Toggles(available=False)).get("/r")
    assert resp.status_code == 503
    assert "retry-after" not in resp.headers


def test_int_hint_is_delta_seconds(client_for) -> None:
    resp = client_for(Toggles(available=30)).get("/r")
    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "30"


def test_datetime_hint_is_http_date(client_for) -> None:
    when = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    resp = client_for(Toggles(available=when)).get("/r")
    assert resp.status_code == 503
    assert resp.headers["retry-after"] == http_date(when)
