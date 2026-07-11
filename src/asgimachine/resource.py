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
    from .http import HttpRequest
    from .schema import ResourceDescription

# A producer turns the resolved context into a representation value (§6). The
# core serializes its return: bytes/str pass through, Pydantic models via
# ``model_dump_json``, everything else via ``json.dumps``.
Producer = Callable[["Ctx"], Awaitable[Any]]

# An acceptor consumes the request body (parse + apply the write) for one request
# Content-Type. Its return is the response representation: a value -> 200 + body,
# None -> 204 (no content). See §6 and the write path in core.
Acceptor = Callable[["Ctx"], Awaitable[Any]]


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
    allowed_methods: list[str] = field(default_factory=list[str])
    user: Any = None
    entity: Any = None
    # The acceptor selected for a write request's Content-Type (set at B5).
    acceptor: Acceptor | None = None
    extra: dict[str, Any] = field(default_factory=dict[str, Any])


class Resource:
    """Base class for graph-lane endpoints. Override only what you care about."""

    KNOWN_METHODS = frozenset(
        {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"},
    )

    # --- B13 ---------------------------------------------------------------
    async def service_available(self, ctx: Ctx) -> bool:
        return True

    # --- B10 ---------------------------------------------------------------
    async def allowed_methods(self, ctx: Ctx) -> list[str]:
        return ["GET", "HEAD"]

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

    # --- caching (v3): emitted on cacheable responses (200/304) ------------
    async def expires(self, ctx: Ctx) -> datetime | None:
        # -> Expires header.
        return None

    async def cache_control(self, ctx: Ctx) -> str | None:
        # -> Cache-Control header, e.g. "public, max-age=31536000, immutable"
        # for an archived, immutable feed page.
        return None

    # --- C3/C4: content negotiation ---------------------------------------
    async def content_types_provided(self, ctx: Ctx) -> Sequence[tuple[str, Producer]]:
        return [("application/json", self.to_json)]

    async def variances(self, ctx: Ctx) -> Sequence[str]:
        # Extra request-header names this representation varies on, emitted in
        # Vary. The core adds "Accept" automatically when more than one media
        # type is offered, so only list additional axes (e.g. an auth header).
        return []

    async def to_json(self, ctx: Ctx) -> Any:
        # The default JSON producer. Read resources override this (or
        # content_types_provided) to build their representation.
        raise NotImplementedError(
            f"{type(self).__name__} provides application/json but does not "
            "implement to_json() (or override content_types_provided).",
        )

    # --- write path (§4 v2) -----------------------------------------------
    # Body-validation nodes. Traversed only for body-bearing methods
    # (POST/PUT/PATCH); each ships a correct default that passes.
    async def malformed_request(self, ctx: Ctx) -> bool:  # B9 -> 400
        return False

    async def valid_content_headers(self, ctx: Ctx) -> bool:  # B6 -> 501
        return True

    async def valid_entity_length(self, ctx: Ctx) -> bool:  # B4 -> 413
        return True

    # Acceptors mirror producers on the write side: each handles one request
    # Content-Type. Empty by default — a read-only resource declares none.
    async def content_types_accepted(self, ctx: Ctx) -> Sequence[tuple[str, Acceptor]]:
        return []

    async def known_content_type(self, ctx: Ctx) -> bool:
        # B5 -> 415. Default: if the resource declares acceptors, the request's
        # Content-Type must match one of them; otherwise anything is accepted.
        accepted = await self.content_types_accepted(ctx)
        if not accepted:
            return True
        media = parse_content_type(ctx.request.headers.get("content-type"))
        return media is not None and media in {mtype for mtype, _ in accepted}

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
