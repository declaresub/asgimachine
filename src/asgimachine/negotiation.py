"""Accept parsing and media-type selection (PLAN.md §4 C3/C4).

v0 is deliberately minimal: parse ``Accept``, honor q-values and ``*`` wildcards,
and pick the best match from the resource's offered types (first offer wins ties).
Full ``Accept-Language``/``Charset``/``Encoding`` negotiation is v3.
"""

from __future__ import annotations


def _parse_accept(header: str) -> list[tuple[str, str, float]]:
    """Return ``(type, subtype, q)`` triples, unsorted. Malformed parts drop."""

    parsed: list[tuple[str, str, float]] = []
    for raw in header.split(","):
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
                try:
                    q = float(value)
                except ValueError:
                    q = 0.0
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
