"""Accept parsing and proactive-negotiation selection (PLAN.md §4 C3-F7).

Parse ``Accept`` and the ``Accept-*`` axes, honor q-values and ``*`` wildcards,
and pick the best match from the resource's offered set (first offer wins ties).
``choose_media_type`` drives C3/C4; ``choose_language`` and ``choose_encoding``
drive the D and F nodes (language lookup per RFC 4647, the identity default for
encodings per RFC 9110 §12.5.3). Charset (webmachine's E nodes) is intentionally
omitted — RFC 9110 §12.5.2 deprecates ``Accept-Charset``.
"""

from __future__ import annotations

import math
from collections.abc import Callable

# Defensive input bounds. A client-supplied Accept has no RFC length limit, and
# selection is O(ranges x offers); cap the work an adversarial header can force.
# Excess ranges are ignored (logged as a silent cap only in that they're dropped
# past the bound), not an error — a truncated Accept still negotiates sanely.
_MAX_ACCEPT_LEN = 8192
_MAX_RANGES = 64


def parse_content_type(header: str | None) -> str | None:
    """Extract the lowercased ``type/subtype`` from a Content-Type header.

    Parameters (``; charset=...``) are dropped. Returns ``None`` for a missing or
    malformed value (no ``/``). Used to match request bodies against acceptors.
    """

    if not header:
        return None
    media = header[:_MAX_ACCEPT_LEN].split(";", 1)[0].strip().lower()
    return media if "/" in media else None


def _clamp_qvalue(value: str) -> float:
    """Parse a qvalue, clamped to [0, 1]. Rejects nan/inf (RFC 9110 §12.4.2)."""

    try:
        q = float(value)
    except ValueError:
        return 0.0
    if math.isnan(q):
        return 0.0
    return max(0.0, min(1.0, q))


def _parse_accept(header: str) -> list[tuple[str, str, float]]:
    """Return ``(type, subtype, q)`` triples, unsorted. Malformed parts drop."""

    parsed: list[tuple[str, str, float]] = []
    for raw in header[:_MAX_ACCEPT_LEN].split(","):
        if len(parsed) >= _MAX_RANGES:
            break
        part = raw.strip()
        if not part:
            continue
        media, _, params = part.partition(";")
        media = media.strip()
        if "/" not in media:
            continue
        mtype, subtype = media.split("/", 1)
        q = 1.0
        for param in params.split(";"):
            key, _, value = param.strip().partition("=")
            if key.strip().lower() == "q":
                q = _clamp_qvalue(value)
        parsed.append((mtype.strip().lower(), subtype.strip().lower(), q))
    return parsed


def _specificity(mtype: str, subtype: str) -> int:
    if mtype == "*":
        return 0
    if subtype == "*":
        return 1
    return 2


def choose_media_type(accept: str | None, offered: list[str]) -> str | None:
    """Pick the offered media type best satisfying ``Accept``.

    No/empty ``Accept`` header means "anything" → the first offered type. Returns
    ``None`` when nothing acceptable matches (the 406 signal).
    """

    if not offered:
        return None
    if not accept or not accept.strip():
        return offered[0]

    ranges = _parse_accept(accept)
    if not ranges:
        return offered[0]

    best: str | None = None
    best_key = (-1.0, -1, -1)  # (q, specificity, reversed offer index)
    for index, offer in enumerate(offered):
        omtype, _, osubtype = offer.partition("/")
        omtype, osubtype = omtype.lower(), osubtype.lower()
        for rmtype, rsubtype, q in ranges:
            type_ok = rmtype == "*" or rmtype == omtype
            sub_ok = rsubtype == "*" or rsubtype == osubtype
            if not (type_ok and sub_ok) or q <= 0.0:
                continue
            # Prefer higher q, then more specific range, then earlier offer.
            key = (q, _specificity(rmtype, rsubtype), -index)
            if key > best_key:
                best_key = key
                best = offer
    return best


# --- the D/F axes: flat token lists (language / encoding) -------------------
#
# ``Accept-Language`` and ``Accept-Encoding`` are ``token;q=`` lists rather than
# ``type/subtype`` ranges, so they share one parser and one selector; only the
# per-axis *match* rule differs (exact vs. language lookup) plus the encoding-only
# ``identity`` default. (Charset — the E nodes — is omitted; ``Accept-Charset`` is
# deprecated by RFC 9110 §12.5.2.)

# A match rule: given a header range token and an offered token (both lowercased),
# return a specificity score (higher = more specific) when the range matches the
# offer, or None when it does not.
_Match = Callable[[str, str], "int | None"]


def _parse_token_list(header: str) -> list[tuple[str, float]]:
    """Return ``(token, q)`` pairs (lowercased), unsorted. Malformed parts drop.

    Same defensive bounds as ``_parse_accept`` — an adversarial ``Accept-*`` is
    truncated, never an error."""

    parsed: list[tuple[str, float]] = []
    for raw in header[:_MAX_ACCEPT_LEN].split(","):
        if len(parsed) >= _MAX_RANGES:
            break
        part = raw.strip()
        if not part:
            continue
        token, _, params = part.partition(";")
        token = token.strip().lower()
        if not token:
            continue
        q = 1.0
        for param in params.split(";"):
            key, _, value = param.strip().partition("=")
            if key.strip().lower() == "q":
                q = _clamp_qvalue(value)
        parsed.append((token, q))
    return parsed


def _select(
    header: str | None,
    offered: list[str],
    *,
    matches: _Match,
    identity: str | None = None,
) -> str | None:
    """Pick the offered token best satisfying a ``token;q=`` ``Accept-*`` header.

    No/empty/garbage header means "anything" -> the first offered token. Returns
    ``None`` when nothing acceptable matches (the 406 signal). ``identity`` names a
    token acceptable *by default* when no range mentions it (RFC 9110 §12.5.3, for
    encodings) — an explicit ``identity;q=0`` or ``*;q=0`` still excludes it.
    """

    if not offered:
        return None
    if not header or not header.strip():
        return offered[0]
    ranges = _parse_token_list(header)
    if not ranges:
        return offered[0]

    best: str | None = None
    best_key = (-1.0, -1, -1)  # (q, specificity, reversed offer index)
    for index, offer in enumerate(offered):
        low = offer.lower()
        matched: tuple[float, int] | None = None
        for token, q in ranges:
            spec = matches(token, low)
            if spec is None:
                continue
            cand = (q, spec)
            if matched is None or cand > matched:
                matched = cand
        # identity is acceptable by default only when *no* range referred to it
        # (a matched-with-q=0 leaves ``matched`` set, so it stays excluded below).
        if matched is None and identity is not None and low == identity:
            matched = (0.001, 0)
        if matched is None or matched[0] <= 0.0:
            continue
        key = (matched[0], matched[1], -index)
        if key > best_key:
            best_key, best = key, offer
    return best


def _match_exact(rng: str, token: str) -> int | None:
    """Encoding matching: the wildcard, or a case-insensitive equal."""

    if rng == "*":
        return 0
    return 1 if rng == token else None


def _match_language(rng: str, tag: str) -> int | None:
    """RFC 4647 lookup-style matching on subtag boundaries: the wildcard, an exact
    tag, a more-general range (``en`` matches tag ``en-US``), or the lookup
    fallback (range ``en-US`` matched by the more-general offered tag ``en``).
    More specific matches score higher, so the closest offered tag wins."""

    if rng == "*":
        return 0
    if rng == tag:
        return 3
    if tag.startswith(rng + "-"):  # range more general than the tag
        return 2
    return 1 if rng.startswith(tag + "-") else None  # lookup fallback


def choose_language(accept_language: str | None, offered: list[str]) -> str | None:
    """D4/D5: pick the offered language tag best satisfying ``Accept-Language``."""

    return _select(accept_language, offered, matches=_match_language)


def choose_encoding(accept_encoding: str | None, offered: list[str]) -> str | None:
    """F6/F7: pick the offered content-coding best satisfying ``Accept-Encoding``.

    ``identity`` is acceptable by default unless the header explicitly refuses it
    (RFC 9110 §12.5.3)."""

    return _select(accept_encoding, offered, matches=_match_exact, identity="identity")
