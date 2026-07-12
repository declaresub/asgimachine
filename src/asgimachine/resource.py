"""The Resource base class and per-request context (PLAN.md §5).

Every callback is ``async`` and ships a *correct HTTP default* (§2.3): a resource
that overrides only ``content_types_provided``/``to_json`` already gets
405/406/404/304/501/503 behavior for free.

Per-request state lives on :class:`Ctx`, never on the shared resource instance —
resources hold only their wired collaborators (§2.2).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .negotiation import parse_content_type
from .trace import Trace

if TYPE_CHECKING:
    from .codec import Codec
    from .http import HttpRequest
    from .schema import ResourceDescription

# A producer turns the resolved context into a representation value (§6).
Producer = Callable[["Ctx"], Awaitable[Any]]


@dataclass(slots=True)
class Ctx:
    """webmachine's ReqData + Context: per-request scratch state.

    Carries the request, holds what callbacks compute, and accumulates the
    decision trace. Attributes like ``user``/``entity`` are set by resource
    callbacks; the core only writes ``chosen_media_type``.
    """

    request: HttpRequest
    trace: Trace = field(default_factory=Trace)
    chosen_media_type: str | None = None
    allowed_methods: frozenset[str] = field(default_factory=frozenset[str])
    user: Any = None
    entity: Any = None
    extra: dict[str, Any] = field(default_factory=dict[str, Any])
    # Framework config: the media-type -> Codec registry for this request.
    codecs: dict[str, Codec] = field(default_factory=dict[str, "Codec"])


class Resource:
    """Base class for graph-lane endpoints. Override only what you care about."""

    KNOWN_METHODS = frozenset(
        {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"},
    )

    # B10 -> 405 + Allow. The set of methods this resource supports. Static shape,
    # not per-request behavior: 405 is a property of the target resource (RFC 9110
    # §15.5.6), while per-principal gating belongs in `forbidden` (403). Set it on
    # the class, or per-instance in __init__; the core reads it directly.
    ALLOWED_METHODS: frozenset[str] = frozenset({"GET", "HEAD"})

    # --- B13 ---------------------------------------------------------------
    async def service_available(self, ctx: Ctx) -> bool:
        return True

    # --- B8 ----------------------------------------------------------------
    async def is_authorized(self, ctx: Ctx) -> bool | str:
        # True = authorized; False = 401 (no challenge); str = 401 with that
        # WWW-Authenticate challenge value.
        return True

    # --- B7 ----------------------------------------------------------------
    async def forbidden(self, ctx: Ctx) -> bool:
        return False

    # --- G7 ----------------------------------------------------------------
    async def resource_exists(self, ctx: Ctx) -> bool:
        return True

    # --- conditional GET (G8-L17 subset) -----------------------------------
    async def generate_etag(self, ctx: Ctx) -> str | None:
        return None

    async def last_modified(self, ctx: Ctx) -> datetime | None:
        return None

    # O18 -> 300. When the resource offers several representations and wants the
    # client to choose, return 300 with the list (from PRODUCES). Default: pick
    # one via negotiation and return 200.
    async def multiple_choices(self, ctx: Ctx) -> bool:
        return False

    # --- caching (v3): emitted on cacheable responses (200/304) ------------
    async def expires(self, ctx: Ctx) -> datetime | None:
        # -> Expires header.
        return None

    async def cache_control(self, ctx: Ctx) -> str | None:
        # -> Cache-Control header, e.g. "public, max-age=31536000, immutable"
        # for an archived, immutable feed page.
        return None

    # C3/C4 content negotiation. PRODUCES lists the offered media types in
    # preference order (declared-first wins ties); a codec encodes the single
    # representation built by represent(). Set on the class or per-instance.
    PRODUCES: tuple[str, ...] = ("application/json",)

    async def represent(self, ctx: Ctx) -> Any:
        # The representation value (a domain model / dict / etc.), encoded by the
        # negotiated codec. The typed return is the response model for schema.
        raise NotImplementedError(
            f"{type(self).__name__} offers {self.PRODUCES} but does not "
            "implement represent().",
        )

    async def variances(self, ctx: Ctx) -> Sequence[str]:
        # Extra request-header names this representation varies on, emitted in
        # Vary. The core adds "Accept" automatically when more than one media
        # type is offered, so only list additional axes (e.g. an auth header).
        return []

    # --- write path (§4 v2) -----------------------------------------------
    # Body-validation nodes. Traversed only for body-bearing methods
    # (POST/PUT/PATCH); each ships a correct default that passes.
    async def malformed_request(self, ctx: Ctx) -> bool:  # B9 -> 400
        return False

    async def valid_content_headers(self, ctx: Ctx) -> bool:  # B6 -> 501
        return True

    async def valid_entity_length(self, ctx: Ctx) -> bool:  # B4 -> 413
        return True

    # The mirror of PRODUCES on the write side: request Content-Types this
    # resource accepts. Empty by default (a read-only resource declares none).
    CONSUMES: tuple[str, ...] = ()

    async def apply(self, ctx: Ctx, body: Any) -> Any:
        # The write handler for PUT/PATCH/POST-create. The core decodes the
        # request via the negotiated codec and parses it into ``body``'s declared
        # type before calling this — annotate ``body: NoteInput`` (a Pydantic
        # model) and a bad body is a 400 (parse, don't validate). Annotate it
        # loosely (``dict``/``object``) to receive the decoded structure as-is.
        # Its return is the response representation.
        raise NotImplementedError(
            f"{type(self).__name__} accepts writes but does not implement apply().",
        )

    async def known_content_type(self, ctx: Ctx) -> bool:
        # B5 -> 415. Default: if the resource declares CONSUMES, the request's
        # Content-Type must be one of them; otherwise anything is accepted.
        if not self.CONSUMES:
            return True
        media = parse_content_type(ctx.request.headers.get("content-type"))
        return media is not None and media in self.CONSUMES

    async def is_conflict(self, ctx: Ctx) -> bool:
        # O14 -> 409. e.g. a PUT that would violate an invariant.
        return False

    # DELETE (M20/M16)
    async def delete_resource(self, ctx: Ctx) -> bool:
        # Perform the delete; return True once enacted. Resources that allow
        # DELETE must implement this.
        raise NotImplementedError(
            f"{type(self).__name__} allows DELETE but does not implement "
            "delete_resource().",
        )

    async def delete_completed(self, ctx: Ctx) -> bool:
        # M20 -> True = fully done (204); False = accepted for later (202).
        return True

    # POST (N11)
    async def post_is_create(self, ctx: Ctx) -> bool:
        # True -> POST creates a new resource at create_path (201 + Location).
        # False -> POST is an action; process_post handles it.
        return False

    async def create_path(self, ctx: Ctx) -> str:
        raise NotImplementedError(
            f"{type(self).__name__} sets post_is_create but does not implement "
            "create_path().",
        )

    async def process_post(self, ctx: Ctx) -> Any:
        raise NotImplementedError(
            f"{type(self).__name__} handles POST but does not implement "
            "process_post() (or set post_is_create).",
        )

    # --- G7-false branch: the resource does not (currently) exist ----------
    async def previously_existed(self, ctx: Ctx) -> bool:  # K7
        # True routes a missing resource to redirect/gone handling.
        return False

    async def moved_permanently(self, ctx: Ctx) -> str | None:  # K5 -> 301
        return None

    async def moved_temporarily(self, ctx: Ctx) -> str | None:  # L5 -> 307
        return None

    # --- schema (§10) ------------------------------------------------------
    def describe(self) -> ResourceDescription | None:
        # Opt in to OpenAPI generation by returning a ResourceDescription.
        # None (the default) leaves this route out of any generated schema.
        return None
