"""The v0 conformance suite (PLAN.md §11 tier 2).

Table-driven ``(request → expected status + headers)`` covering each v0 node's
success and failure edge. This is the executable spec and the M0 acceptance gate:
200/304/405/401/406/404/501/503 + HEAD + OPTIONS.
"""

from __future__ import annotations

import pytest

from tests.conftest import FIXED_ETAG, Toggles


def test_200_ok(client_for) -> None:
    resp = client_for(Toggles()).get("/r")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    assert resp.headers["etag"] == FIXED_ETAG
    assert "last-modified" in resp.headers
    assert resp.json() == {"ok": True}


def test_head_has_no_body_but_same_headers(client_for) -> None:
    resp = client_for(Toggles()).head("/r")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    assert resp.headers["etag"] == FIXED_ETAG
    assert resp.content == b""


def test_503_service_unavailable(client_for) -> None:
    resp = client_for(Toggles(available=False)).get("/r")
    assert resp.status_code == 503


def test_501_unknown_method(client_for) -> None:
    resp = client_for(Toggles()).request("BREW", "/r")
    assert resp.status_code == 501


def test_405_method_not_allowed_sets_allow(client_for) -> None:
    resp = client_for(Toggles(methods=["GET", "HEAD"])).request("DELETE", "/r")
    assert resp.status_code == 405
    allow = {m.strip() for m in resp.headers["allow"].split(",")}
    assert allow == {"GET", "HEAD", "OPTIONS"}


def test_401_unauthorized_with_challenge(client_for) -> None:
    resp = client_for(Toggles(authorized="Bearer")).get("/r")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def test_401_unauthorized_without_challenge(client_for) -> None:
    resp = client_for(Toggles(authorized=False)).get("/r")
    assert resp.status_code == 401
    assert "www-authenticate" not in resp.headers


def test_403_forbidden(client_for) -> None:
    resp = client_for(Toggles(forbidden=True)).get("/r")
    assert resp.status_code == 403


def test_406_not_acceptable(client_for) -> None:
    resp = client_for(Toggles()).get("/r", headers={"accept": "text/csv"})
    assert resp.status_code == 406


def test_406_wildcard_accept_is_ok(client_for) -> None:
    resp = client_for(Toggles()).get("/r", headers={"accept": "*/*"})
    assert resp.status_code == 200


def test_404_not_found(client_for) -> None:
    resp = client_for(Toggles(exists=False)).get("/r")
    assert resp.status_code == 404


def test_options_returns_allow(client_for) -> None:
    resp = client_for(Toggles(methods=["GET", "HEAD"])).options("/r")
    assert resp.status_code == 200
    allow = {m.strip() for m in resp.headers["allow"].split(",")}
    assert allow == {"GET", "HEAD", "OPTIONS"}
    assert resp.content == b""


def test_options_ignores_accept(client_for) -> None:
    # OPTIONS is decided at B3, ahead of content negotiation: never a 406.
    resp = client_for(Toggles()).options("/r", headers={"accept": "text/csv"})
    assert resp.status_code == 200


def test_304_if_none_match(client_for) -> None:
    resp = client_for(Toggles()).get("/r", headers={"if-none-match": FIXED_ETAG})
    assert resp.status_code == 304
    assert resp.headers["etag"] == FIXED_ETAG
    assert resp.content == b""


def test_304_if_none_match_star(client_for) -> None:
    resp = client_for(Toggles()).get("/r", headers={"if-none-match": "*"})
    assert resp.status_code == 304


def test_200_if_none_match_mismatch(client_for) -> None:
    resp = client_for(Toggles()).get("/r", headers={"if-none-match": '"other"'})
    assert resp.status_code == 200


def test_304_if_modified_since_not_modified(client_for) -> None:
    # Last-Modified is 2026-01-01; a later If-Modified-Since means "not modified".
    resp = client_for(Toggles()).get(
        "/r",
        headers={"if-modified-since": "Wed, 02 Jan 2030 00:00:00 GMT"},
    )
    assert resp.status_code == 304


def test_200_if_modified_since_modified(client_for) -> None:
    resp = client_for(Toggles()).get(
        "/r",
        headers={"if-modified-since": "Sun, 01 Jan 2020 00:00:00 GMT"},
    )
    assert resp.status_code == 200


@pytest.mark.parametrize(
    ("accept", "offered", "expected_ct"),
    [
        ("application/json", ["application/json"], "application/json"),
        ("application/*", ["application/json"], "application/json"),
        (None, ["application/json", "text/plain"], "application/json"),
        ("text/plain", ["application/json", "text/plain"], "text/plain"),
    ],
)
def test_negotiation_picks_expected(client_for, accept, offered, expected_ct) -> None:
    headers = {"accept": accept} if accept else {}
    resp = client_for(Toggles(offered=offered)).get("/r", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == expected_ct
