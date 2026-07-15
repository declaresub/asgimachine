"""Authorization header parsing (RFC 9110 §11.6.1)."""

from __future__ import annotations

import base64

import pytest

from asgimachine.auth import basic_credentials, bearer_token, parse_authorization


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("Bearer abc", ("bearer", "abc")),
        ("bearer abc", ("bearer", "abc")),  # scheme is case-insensitive
        ("BEARER   abc  ", ("bearer", "abc")),  # extra whitespace collapsed/trimmed
        ("Basic dXNlcjpwYXNz", ("basic", "dXNlcjpwYXNz")),
        ("Digest realm=x, nonce=y", ("digest", "realm=x, nonce=y")),  # any scheme
        ("Negotiate", ("negotiate", "")),  # scheme only
        ("Be@rer abc", None),  # scheme is not a valid token -> rejected
        ("@#$ abc", None),
    ],
)
def test_parse_authorization(header, expected) -> None:
    assert parse_authorization(header) == expected


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer abc123", "abc123"),
        ("bearer abc123", "abc123"),
        ("Basic dXNlcjpwYXNz", None),  # wrong scheme
        ("Bearer", None),  # no token
        ("Bearer    ", None),
        ("Bearer ab cd", None),  # space -> not a single token68
        ("Bearer abc!def", None),  # '!' is outside token68
        ("Bearer aGVsbG8.d29ybGQ-_~+/=", "aGVsbG8.d29ybGQ-_~+/="),  # full token68 set
        (None, None),
    ],
)
def test_bearer_token(header, expected) -> None:
    assert bearer_token(header) == expected


def test_basic_credentials_roundtrip() -> None:
    assert basic_credentials(_basic("alice", "s3cret")) == ("alice", "s3cret")


def test_basic_credentials_password_may_contain_colon() -> None:
    # RFC 7617: split on the FIRST colon; the password may hold more.
    assert basic_credentials(_basic("alice", "a:b:c")) == ("alice", "a:b:c")


@pytest.mark.parametrize(
    "header",
    [
        None,
        "Bearer abc",  # wrong scheme
        "Basic !!!notbase64!!!",  # invalid base64
        "Basic " + base64.b64encode(b"nocolon").decode(),  # missing colon
    ],
)
def test_basic_credentials_rejects(header) -> None:
    assert basic_credentials(header) is None
