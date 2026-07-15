"""Authorization header parsing (RFC 9110 §11.6.1) — parse, don't authenticate.

The framework's job is to turn ``Authorization: <scheme> <credentials>`` into its
parts, correctly (the scheme is case-insensitive, and Basic's password may contain
a colon). *Verifying* the credential — looking up a token, checking a password,
validating a JWT — is your resource's job, not the graph's.

Validation is done against the actual RFC 9110 grammar via the ``abnf`` parser (the
same one ``http.py`` uses to validate response header values), each rule applied
where its grammar is known: the scheme is a ``token``; a Bearer credential is a
``token68``; Basic is base64. :func:`parse_authorization` is the generic split and
works for **any** scheme; :func:`bearer_token` and :func:`basic_credentials` decode
the two schemes common in HTTP APIs.
"""

from __future__ import annotations

import base64

from abnf import ParseError
from abnf.grammars import rfc9110

_TOKEN = rfc9110.Rule("token")  # the scheme (RFC 9110 §5.6.2)
_TOKEN68 = rfc9110.Rule("token68")  # a Bearer credential (RFC 6750 / §11.2)


def _matches(rule: rfc9110.Rule, value: str) -> bool:
    try:
        rule.parse_all(value)
    except ParseError:
        return False
    return True


def parse_authorization(header: str | None) -> tuple[str, str] | None:
    """Split an ``Authorization`` value into ``(scheme, credentials)``.

    The scheme must be a valid ``token`` (a non-token is rejected) and is lowercased
    (it is case-insensitive, RFC 9110 §11.1). The credentials are everything after
    the first run of whitespace, verbatim (a scheme-only header yields ``""``) —
    their grammar is scheme-specific, so they are *not* validated here; that is the
    job of the scheme helpers below. Returns ``None`` for a missing/blank header or a
    malformed scheme.
    """

    if header is None:
        return None
    header = header.strip()
    if not header:
        return None
    parts = header.split(None, 1)
    scheme = parts[0]
    if not _matches(_TOKEN, scheme):
        return None
    credentials = parts[1].strip() if len(parts) > 1 else ""
    return (scheme.lower(), credentials)


def bearer_token(header: str | None) -> str | None:
    """The token from an ``Authorization: Bearer <token>`` header, or ``None`` when
    the header is absent, uses a different scheme, or the token is not a valid
    ``token68`` (RFC 6750)."""

    parsed = parse_authorization(header)
    if parsed is None or parsed[0] != "bearer":
        return None
    token = parsed[1]
    return token if _matches(_TOKEN68, token) else None


def basic_credentials(header: str | None) -> tuple[str, str] | None:
    """The ``(user_id, password)`` from an ``Authorization: Basic …`` header (RFC
    7617), or ``None`` when the header is absent, uses a different scheme, or is
    malformed. The password may contain colons; the user id may not (the split is on
    the first colon)."""

    parsed = parse_authorization(header)
    if parsed is None or parsed[0] != "basic":
        return None
    try:
        decoded = base64.b64decode(parsed[1], validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):  # bad base64 / not UTF-8
        return None
    user_id, sep, password = decoded.partition(":")
    if not sep:  # RFC 7617 requires the colon
        return None
    return (user_id, password)
