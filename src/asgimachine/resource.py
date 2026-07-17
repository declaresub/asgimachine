"""The Resource base class and per-request context (PLAN.md §5).

Every callback is ``async`` and ships a *correct HTTP default* (§2.3): a resource
that overrides only ``represent``/``PRODUCES`` already gets 405/406/404/304/501/503
behavior for free.

Per-request state lives on :class:`Ctx` (or a resource-defined subclass), never on
the shared resource instance — resources hold only their wired collaborators (§2.2).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from http import HTTPStatus

from .http import DEFAULT_MAX_BODY_BYTES
from .negotiation import parse_content_type
from .trace import Trace


def _reason(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return ""


if TYPE_CHECKING:
    from .codec import Codec
    from .event import Event
    from .http import HttpRequest, HttpResponse
    from .schema import ResourceDescription

# A producer turns the resolved context into a representation value (§6).
Producer = Callable[["Ctx"], Awaitable[Any]]

# The catch-all for an unexpected exception raised during the walk. Returns None
# (-> the graph's standard 500), an HttpResponse (-> that response), or raises
# (re-raise to propagate to the substrate's outer handler; raise HaltResponse for
# a custom response). Configured app-wide at build_app, or overridden per resource.
OnException = Callable[["Ctx", Exception], Awaitable["HttpResponse | None"]]

# A Retry-After hint for an unavailable response: delta-seconds (an int) or an
# HTTP-date (a datetime), per RFC 9110 §10.2.3. Returned by an availability
# callback alongside a plain bool.
RetryHint = int | datetime


@dataclass(slots=True)
class Ctx:
    """webmachine's ReqData: the *framework's* per-request state.

    Deliberately minimal and domain-agnostic — it holds the request, the trace,
    negotiation result, and codec registry, plus an untyped ``extra`` bag. Domain
    state (a principal, the loaded entity) is not the framework's business: a
    resource that wants typed per-request state subclasses ``Ctx`` and declares
    it (see ``Resource.context_class`` and PLAN.md §2.7).
    """

    request: HttpRequest
    trace: Trace = field(default_factory=Trace)
    chosen_media_type: str | None = None
    # The proactively-negotiated variant axes (D/F). None when the resource
    # doesn't offer that axis; a representation reads them to serve the right
    # translation / content-coding.
    chosen_language: str | None = None
    chosen_encoding: str | None = None
    allowed_methods: frozenset[str] = field(default_factory=frozenset[str])
    extra: dict[str, Any] = field(default_factory=dict[str, Any])
    # The per-request wide event (§ observability). Callbacks and instrumented code
    # enrich it in place; the core fills owned fields and emits it once at the
    # request boundary. Always present, so writing to it is free; emitted only when
    # an EventSink is configured.
    event: Event = field(default_factory=dict[str, object])
    # Framework config: the media-type -> Codec registry for this request.
    codecs: dict[str, Codec] = field(default_factory=dict[str, "Codec"])


class Resource[C: Ctx = Ctx]:
    """Base class for graph-lane endpoints. Override only what you care about.

    Generic over its context type ``C``: subclass ``Ctx`` for typed per-request
    state and declare it via ``context_class`` (and ``Resource[MyCtx]`` for the
    checker). Plain ``Resource`` uses base ``Ctx``.
    """

    # The Ctx (sub)class the core constructs for each request. Declare a subclass
    # alongside ``Resource[MyCtx]`` when a resource needs typed per-request state.
    context_class: ClassVar[type[Ctx]] = Ctx

    async def lifespan(self, ctx: C) -> AsyncGenerator[None]:
        """Per-request setup/teardown, wrapping the whole graph walk.

        Override as a *plain async generator* — acquire in the setup half,
        ``yield`` exactly once, release after. **No decorator**: the core wraps
        this in an async context manager itself, so a forgotten
        ``@asynccontextmanager`` can't bite. (Forgetting the ``yield`` instead is
        a type error, since the declared return is ``AsyncIterator[None]``.)

        The core opens this before B13 and closes it on the way out, guaranteed
        across every exit — a normal response, a halt (404/401/…), a raised
        error, or a client disconnect. The teardown is cancellation-shielded, so
        a disconnect cannot interrupt resource release; and for a *streaming*
        response it is deferred until the body is fully drained, so a connection
        stashed on ``ctx`` stays alive for the life of the stream. An in-flight
        exception is fed into the generator (so ``async with conn.transaction()``
        rolls back), but the lifespan must not *suppress* it::

            async def lifespan(self, ctx: MyCtx) -> AsyncIterator[None]:
                async with self._pool.acquire() as conn:  # released on any exit
                    ctx.conn = conn
                    yield
        """
        yield

    KNOWN_METHODS = frozenset(
        {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"},
    )

    # B10 -> 405 + Allow. The set of methods this resource supports. Static shape,
    # not per-request behavior: 405 is a property of the target resource (RFC 9110
    # §15.5.6), while per-principal gating belongs in `forbidden` (403). Declare
    # it on the class (or per-instance in __init__); it is the schema anchor.
    ALLOWED_METHODS: frozenset[str] = frozenset({"GET", "HEAD"})

    async def allowed_methods(self, ctx: C) -> frozenset[str]:
        # The graph reads the method set through this callback, defaulting to the
        # ALLOWED_METHODS declaration. Override only for genuine per-request
        # variation; the schema still documents ALLOWED_METHODS.
        return self.ALLOWED_METHODS

    # --- B13 ---------------------------------------------------------------
    async def service_available(self, ctx: C) -> bool | RetryHint:
        # True = available. False = 503 with no hint. An int (delta-seconds) or a
        # datetime (an HTTP-date) = 503 carrying a ``Retry-After`` header (RFC 9110
        # §10.2.3) — e.g. return 30 during a brief maintenance window. This is
        # service-*wide* backpressure; a per-client quota is within_rate_limit below.
        return True

    # --- B13a --------------------------------------------------------------
    async def within_rate_limit(self, ctx: C) -> bool | RetryHint:
        # True = within limit, proceed. False = 429 (Too Many Requests, RFC 6585)
        # with no hint. An int (delta-seconds) or datetime (an HTTP-date) = 429
        # carrying a ``Retry-After`` header. Runs right after service_available and
        # before method/auth/body checks, so an over-limit request is shed at the
        # cheapest point — ideal for throttling a login against credential-stuffing.
        # Key the limiter on ctx.request (IP, header) — the principal isn't known
        # yet. 429 postdates webmachine v3; this is an additive node, recorded only
        # when it fires. Default True (no limit). Contrast service_available (503,
        # everyone) with this (429, this client).
        return True

    # --- B11 ---------------------------------------------------------------
    async def uri_too_long(self, ctx: C) -> bool:  # -> 414
        # True when the request target is longer than this resource will serve.
        # Default False — most deployments cap URI length at the server/proxy
        # first, so this rarely fires; recorded in the trace only when it does.
        return False

    # --- B8 ----------------------------------------------------------------
    async def is_authorized(self, ctx: C) -> bool | str:
        # True = authorized; False = 401 (no challenge); str = 401 with that
        # WWW-Authenticate challenge value.
        return True

    # --- B7 ----------------------------------------------------------------
    async def forbidden(self, ctx: C) -> bool:
        return False

    # --- B7a (v4, httpdd) --------------------------------------------------
    async def is_legally_restricted(self, ctx: C) -> bool:  # -> 451 (RFC 7725)
        # True when the resource is denied for legal reasons (e.g. a takedown).
        return False

    # --- G7 ----------------------------------------------------------------
    async def resource_exists(self, ctx: C) -> bool:
        return True

    # --- conditional GET (G8-L17 subset) -----------------------------------
    async def generate_etag(self, ctx: C) -> str | None:
        return None

    async def last_modified(self, ctx: C) -> datetime | None:
        return None

    # --- precondition-required (428, RFC 6585) -----------------------------
    async def require_conditional_write(self, ctx: C) -> bool:
        # True demands that a PUT/PATCH/DELETE carry an update precondition
        # (``If-Match`` or ``If-Unmodified-Since``); an unconditional write is a
        # 428, so a client can't blindly overwrite state it hasn't seen — the
        # "lost update" guard. Default False. A *present* precondition still flows
        # through the normal 412 path; 428 fires only when none was sent.
        return False

    # O18 -> 300. When the resource offers several representations and wants the
    # client to choose, return 300 with the list (from PRODUCES). Default: pick
    # one via negotiation and return 200.
    async def multiple_choices(self, ctx: C) -> bool:
        return False

    # --- caching (v3): emitted on cacheable responses (200/304) ------------
    async def expires(self, ctx: C) -> datetime | None:
        # -> Expires header.
        return None

    async def cache_control(self, ctx: C) -> str | None:
        # -> Cache-Control header, e.g. "public, max-age=31536000, immutable"
        # for an archived, immutable feed page.
        return None

    # C3/C4 content negotiation. PRODUCES lists the offered media types in
    # preference order (declared-first wins ties); a codec encodes the single
    # representation built by represent(). Set on the class or per-instance.
    PRODUCES: tuple[str, ...] = ("application/json",)

    # C4a -> serve default instead of 406 (v4, RFC 9110 §12.1): when an Accept
    # can't be satisfied, a resource that declares this True disregards Accept and
    # serves PRODUCES[0] rather than 406. Static shape, read through the thin
    # callback below (§2.7); the schema drops 406 from the surface when it is set.
    IGNORE_UNACCEPTABLE: ClassVar[bool] = False

    async def ignore_unacceptable(self, ctx: C) -> bool:
        return self.IGNORE_UNACCEPTABLE

    async def represent(self, ctx: C) -> Any:
        # The representation value (a domain model / dict / etc.), encoded by the
        # negotiated codec. The typed return is the response model for schema.
        raise NotImplementedError(
            f"{type(self).__name__} offers {self.PRODUCES} but does not "
            "implement represent().",
        )

    async def variances(self, ctx: C) -> Sequence[str]:
        # Extra request-header names this representation varies on, emitted in
        # Vary. The core adds "Accept" automatically when more than one media
        # type is offered (and the matching Accept-* axis for each of
        # LANGUAGES/ENCODINGS offered), so only list additional axes.
        return []

    # --- D/F proactive negotiation (v3) ------------------------------------
    # Language / content-coding, each parallel to PRODUCES: declare the offered
    # values in preference order (first wins ties) and the core negotiates against
    # the matching Accept-* header. Empty (the default) means the axis is not
    # negotiated — the header is ignored, no Vary axis, no 406. When offered, an
    # unsatisfiable Accept-* is a 406 (unless ``ignore_unacceptable`` serves the
    # default instead), and the choice is exposed on ctx for ``represent``.
    #
    # (Charset — webmachine's E nodes — is deliberately absent: RFC 9110 §12.5.2
    # deprecates ``Accept-Charset``. Charset belongs on the Content-Type parameter.)
    #
    # asgimachine negotiates and advertises; it does not compress or transcode
    # (that is the substrate's / your representation's job — "rent Layer 2"). Read
    # ``ctx.chosen_language`` to pick a translation and ``ctx.chosen_encoding`` to
    # produce bytes in that content-coding and set ``Content-Encoding``.

    # D4/D5 -> 406. Offered language tags; matched RFC 4647 lookup-style (a request
    # for ``en-US`` is served by an offered ``en``, and vice versa). Sets
    # Content-Language.
    LANGUAGES: tuple[str, ...] = ()

    async def languages(self, ctx: C) -> Sequence[str]:
        return self.LANGUAGES

    # F6/F7 -> 406. Offered content-codings; ``identity`` is acceptable by default
    # unless the client refuses it (RFC 9110 §12.5.3).
    ENCODINGS: tuple[str, ...] = ()

    async def encodings(self, ctx: C) -> Sequence[str]:
        return self.ENCODINGS

    # --- error bodies (§4 v4, RFC 9457) ------------------------------------
    # Media types offered for *error* bodies (4xx/5xx), negotiated against Accept
    # separately from PRODUCES — the main negotiation may have failed (406) or not
    # run yet (401 before C4). Declare more (e.g. add "text/html") + a codec to
    # serve browsers HTML errors; an unmatched Accept falls back to the first.
    ERROR_PRODUCES: ClassVar[tuple[str, ...]] = ("application/problem+json",)

    async def error_body(self, ctx: C, status: int, media_type: str) -> Any | None:
        # The body for a 4xx/5xx response, encoded as ``media_type`` (the
        # negotiated error representation). Default: an RFC 9457 problem detail.
        # Override to add ``detail``/``instance``/custom members, render per
        # ``media_type``, or return None for an empty body.
        return {"type": "about:blank", "title": _reason(status), "status": status}

    # --- unexpected exceptions ---------------------------------------------
    async def on_exception(self, ctx: C, exc: Exception) -> HttpResponse | None:
        # Catch-all for an *unexpected* exception raised during the walk. Only
        # ``Exception`` reaches here — a client disconnect (``CancelledError``) and
        # other ``BaseException``s always propagate (teardown + re-raise). Runs
        # inside the walk with ``ctx`` in scope, so it is where you report the error
        # and record its id onto ``ctx`` before the response is built.
        #
        # Default: **re-raise**, so the exception propagates to the substrate's
        # outer handler (Starlette's ServerErrorMiddleware, an ASGI error reporter,
        # ...) — behavior is unchanged unless you opt in. Return instead to have the
        # graph own the 500: ``None`` -> the standard negotiated ``problem+json``
        # body (via ``error_body``); an ``HttpResponse`` -> that response; or raise
        # ``HaltResponse(...)`` for full control. Set a default for the whole app at
        # ``build_app(on_exception=...)``; override here for one resource.
        raise exc

    # --- write path (§4 v2) -----------------------------------------------
    # Body-validation nodes. Traversed only for body-bearing methods
    # (POST/PUT/PATCH); each ships a correct default that passes.
    async def malformed_request(self, ctx: C) -> bool:  # B9 -> 400
        return False

    async def valid_content_headers(self, ctx: C) -> bool:  # B6 -> 501
        return True

    # B4 -> 413. The largest request body (bytes) this resource will accept.
    # A declaration, not a callback: the graph rejects a larger declared
    # Content-Length here, and the substrate caps the *actual* read at this value
    # (the backstop for a chunked or lying Content-Length). Raise it for an
    # upload resource; ``ClassVar`` so the checker forbids per-instance mutation.
    MAX_BODY_BYTES: ClassVar[int] = DEFAULT_MAX_BODY_BYTES

    async def valid_entity_length(self, ctx: C) -> bool:  # B4 -> 413
        # Reject a declared Content-Length over the limit. Absent or unparseable
        # Content-Length falls through to the substrate's read cap.
        declared = ctx.request.headers.get("content-length")
        if declared is None:
            return True
        try:
            return int(declared) <= self.MAX_BODY_BYTES
        except ValueError:
            return True

    # The mirror of PRODUCES on the write side: request Content-Types this
    # resource accepts. Empty by default (a read-only resource declares none).
    CONSUMES: ClassVar[tuple[str, ...]] = ()

    async def apply(self, ctx: C, body: Any) -> Any:
        # The write handler for PUT/PATCH/POST-create. The core decodes the
        # request via the negotiated codec and parses it into ``body``'s declared
        # type before calling this — annotate ``body: NoteInput`` (a Pydantic
        # model) and a bad body is a 400 (parse, don't validate). Annotate it
        # loosely (``dict``/``object``) to receive the decoded structure as-is.
        # Its return is the response representation.
        raise NotImplementedError(
            f"{type(self).__name__} accepts writes but does not implement apply().",
        )

    async def known_content_type(self, ctx: C) -> bool:
        # B5 -> 415. Default: if the resource declares CONSUMES, the request's
        # Content-Type must be one of them; otherwise anything is accepted.
        if not self.CONSUMES:
            return True
        media = parse_content_type(ctx.request.headers.get("content-type"))
        return media is not None and media in self.CONSUMES

    async def is_conflict(self, ctx: C) -> bool:
        # O14 -> 409. e.g. a PUT that would violate an invariant.
        return False

    # DELETE (M20/M16)
    async def delete_resource(self, ctx: C) -> bool:
        # Perform the delete; return True once enacted. Resources that allow
        # DELETE must implement this.
        raise NotImplementedError(
            f"{type(self).__name__} allows DELETE but does not implement "
            "delete_resource().",
        )

    async def delete_completed(self, ctx: C) -> bool:
        # M20 -> True = fully done (204); False = accepted for later (202).
        return True

    # POST (N11)
    async def post_is_create(self, ctx: C) -> bool:
        # True -> POST creates a new resource at create_path (201 + Location).
        # False -> POST is an action; process_post handles it.
        return False

    async def create_path(self, ctx: C) -> str:
        raise NotImplementedError(
            f"{type(self).__name__} sets post_is_create but does not implement "
            "create_path().",
        )

    async def process_post(self, ctx: C) -> Any:
        raise NotImplementedError(
            f"{type(self).__name__} handles POST but does not implement "
            "process_post() (or set post_is_create).",
        )

    async def see_other(self, ctx: C) -> str | None:  # N11 -> 303
        # After the POST's side effects run (create_path/apply or process_post),
        # return a URL to redirect the client to with 303 See Other — the
        # POST-Redirect-Get pattern. None (the default) responds 201 (create) /
        # 200 (action) normally. The URL overrides the created/action response.
        return None

    async def accepted(self, ctx: C) -> str | None:  # O20a -> 202
        # After a write handler *enqueues* work it can't finish inside the request
        # budget, return a URL to a status-monitor resource: the graph responds 202
        # Accepted + Location instead of framing a completed 200/201/204 — the
        # asynchronous request-reply pattern (hand off to a background task, let the
        # client poll the monitor). None (the default) frames the result normally.
        # Applies to POST/PUT/PATCH; DELETE has its own 202 via delete_completed.
        # Mutually exclusive with see_other (which, on POST, is checked first).
        return None

    # --- G7-false branch: the resource does not (currently) exist ----------
    async def previously_existed(self, ctx: C) -> bool:  # K7
        # True routes a missing resource to redirect/gone handling.
        return False

    async def moved_permanently(self, ctx: C) -> str | None:  # K5 -> 301
        return None

    async def permanent_redirect(self, ctx: C) -> str | None:  # K5a -> 308 (v4)
        # RFC 7538: a permanent redirect that *preserves the method* (301 does
        # not). Prefer this over moved_permanently for non-idempotent targets.
        return None

    async def moved_temporarily(self, ctx: C) -> str | None:  # L5 -> 307
        return None

    # --- schema (§10) ------------------------------------------------------
    def describe(self) -> ResourceDescription | None:
        # Opt in to OpenAPI generation by returning a ResourceDescription.
        # None (the default) leaves this route out of any generated schema.
        return None
