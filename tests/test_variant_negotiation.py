"""Proactive negotiation of the D/F axes (PLAN.md §4 D4/D5, F6/F7).

Language / content-coding negotiation: selection semantics (q-values, wildcards,
RFC 4647 language lookup, the RFC 9110 §12.5.3 identity default), the 406 on an
unsatisfiable offered axis, and the advertised headers + Vary. Each axis is
opt-in — a resource that offers none is byte-for-byte unchanged. (Charset, the E
nodes, is intentionally omitted — RFC 9110 §12.5.2 deprecates Accept-Charset.)
"""

from __future__ import annotations

import pytest

from asgimachine.negotiation import choose_encoding, choose_language
from asgimachine.testing import assert_trace
from tests.conftest import Toggles

# --- selection unit tests ---------------------------------------------------


@pytest.mark.parametrize(
    ("accept", "offered", "expected"),
    [
        ("fr", ["en", "fr"], "fr"),  # exact
        ("en-US, fr;q=0.9", ["en", "fr"], "en"),  # en-US falls back to offered en
        ("en", ["en-US", "fr"], "en-US"),  # request en served by more-specific tag
        ("de", ["en", "fr"], None),  # nothing acceptable -> 406 signal
        ("*", ["en", "fr"], "en"),  # wildcard -> first offered
        ("fr;q=0, en", ["en", "fr"], "en"),  # fr refused
        (None, ["en", "fr"], "en"),  # absent -> first offered
        ("en;q=0.4, fr;q=0.8", ["en", "fr"], "fr"),  # higher q wins
    ],
)
def test_choose_language(accept, offered, expected) -> None:
    assert choose_language(accept, offered) == expected


@pytest.mark.parametrize(
    ("accept", "offered", "expected"),
    [
        ("gzip", ["identity", "gzip"], "gzip"),
        ("br", ["identity", "gzip"], "identity"),  # identity is acceptable by default
        ("identity;q=0", ["identity", "gzip"], None),  # identity refused, gzip unlisted
        ("*;q=0", ["identity", "gzip"], None),  # everything refused -> 406
        ("*;q=0, gzip", ["identity", "gzip"], "gzip"),  # explicit gzip beats *;q=0
        ("gzip, br;q=0", ["br"], None),  # only refused coding offered
    ],
)
def test_choose_encoding(accept, offered, expected) -> None:
    assert choose_encoding(accept, offered) == expected


# --- language (D4/D5) through the graph -------------------------------------


def test_language_negotiated_sets_content_language(client_for) -> None:
    resp = client_for(Toggles(languages=["en", "fr"])).get(
        "/r", headers={"accept-language": "fr"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-language"] == "fr"
    assert "Accept-Language" in resp.headers["vary"]
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4", "D5", "G7", "O18"])


def test_language_unsatisfiable_is_406(client_for) -> None:
    resp = client_for(Toggles(languages=["en", "fr"])).get(
        "/r", headers={"accept-language": "de"}
    )
    assert resp.status_code == 406
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4", "D5"])


def test_language_absent_serves_first_offered(client_for) -> None:
    resp = client_for(Toggles(languages=["en", "fr"])).get("/r")
    assert resp.status_code == 200
    assert resp.headers["content-language"] == "en"


# --- encoding (F6/F7) through the graph -------------------------------------


def test_encoding_negotiated_records_and_varies(client_for) -> None:
    resp = client_for(Toggles(encodings=["identity", "gzip"])).get(
        "/r", headers={"accept-encoding": "gzip"}
    )
    assert resp.status_code == 200
    assert "Accept-Encoding" in resp.headers["vary"]
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4", "F7", "G7", "O18"])


def test_encoding_identity_default_avoids_406(client_for) -> None:
    # A coding the resource does not offer, but identity is acceptable by default.
    resp = client_for(Toggles(encodings=["identity"])).get(
        "/r", headers={"accept-encoding": "br"}
    )
    assert resp.status_code == 200


def test_encoding_unsatisfiable_is_406(client_for) -> None:
    resp = client_for(Toggles(encodings=["gzip"])).get(
        "/r", headers={"accept-encoding": "identity, gzip;q=0"}
    )
    assert resp.status_code == 406
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4", "F7"])


# --- Vary composes; unoffered axes are invisible ----------------------------


def test_all_axes_compose_in_order(client_for) -> None:
    resp = client_for(
        Toggles(
            offered=["application/json", "text/csv"],
            languages=["en"],
            encodings=["identity"],
        )
    ).get("/r", headers={"accept": "application/json"})
    assert resp.status_code == 200
    vary = [v.strip() for v in resp.headers["vary"].split(",")]
    assert vary == ["Accept", "Accept-Language", "Accept-Encoding"]
    assert_trace(
        resp,
        ["B13", "B12", "B10", "B8", "B7", "C4", "D5", "F7", "G7", "O18"],
    )


def test_no_axes_offered_is_unchanged(client_for) -> None:
    # The canonical trace and headers are untouched when nothing is offered.
    resp = client_for(Toggles()).get("/r")
    assert resp.status_code == 200
    assert "content-language" not in resp.headers
    assert "vary" not in resp.headers
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4", "G7", "O18"])
