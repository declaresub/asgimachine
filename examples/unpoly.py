"""Unpoly example — the hypermedia frontend asgimachine fits best.

[Unpoly](https://unpoly.com) is the one popular frontend library with *first-class*
conditional-request support: it reads the ``ETag`` a response carries, stores it
against the fragment, and re-sends it as ``If-None-Match`` on every reload/poll. A
``304 Not Modified`` tells Unpoly to keep the fragment it already has. That is
exactly the response asgimachine emits for free — so the two halves click together
with almost no glue.

This one resource, mounted at ``/``, shows the whole fit:

- **One URL, two representations.** A plain browser navigation gets the full HTML
  page; an Unpoly request (which carries an ``X-Up-Version`` header) gets *just* the
  ``#notes`` fragment. ``variances`` declares ``X-Up-Version`` so the response
  correctly carries ``Vary: X-Up-Version`` — a shared cache never hands a fragment
  to a full-page navigation. (This is the custom-header negotiation axis that a
  hand-rolled backend usually forgets.)
- **Conditional GET drives the poll.** ``[up-poll]`` reloads ``#notes`` every few
  seconds. ``generate_etag`` keys the validator on the note count *and* the variant,
  ``cache_control`` says ``no-cache`` (revalidate every time), and the graph answers
  ``304`` whenever nothing changed — so the poll costs one round-trip and no body.
- **POST-Redirect-Get for the write.** The form ``POST``s a note; ``apply`` parses
  the urlencoded body into a typed model (parse, don't validate), and ``see_other``
  returns ``303 -> /``. Unpoly follows the redirect, re-fetches the fragment, and
  swaps ``#notes`` in place.

Codecs are injected at the composition root: a ``text/html`` encoder for responses
and an ``application/x-www-form-urlencoded`` decoder for the form body. The default
JSON registry is never touched.

    uvicorn examples.unpoly:app --reload
    # open http://localhost:8000/ and watch the network panel:
    #   GET /  (X-Up-Version)  -> 200, ETag: "notes-0-frag"
    #   GET /  (poll)          -> 304   (nothing changed)
    #   POST / (add a note)    -> 303 -> GET / -> 200, ETag: "notes-1-frag"
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass, field
from urllib.parse import parse_qsl

from pydantic import BaseModel
from starlette.applications import Starlette

from asgimachine.codec import Codec
from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route

UNPKG = "https://unpkg.com/unpoly@3"


# --- codecs: the media types this resource speaks (injected at the root) ----


class HtmlCodec:
    """Encodes an already-rendered HTML string to bytes (response side only)."""

    def encode(self, value: object) -> bytes:
        return str(value).encode()

    def decode(self, raw: bytes) -> object:  # pragma: no cover - html is never consumed
        raise NotImplementedError("text/html is a response-only media type here")


class FormCodec:
    """Decodes an ``application/x-www-form-urlencoded`` body to a dict (request side
    only). The dict is then parsed into ``apply``'s typed model by the core."""

    def encode(
        self, value: object
    ) -> bytes:  # pragma: no cover - forms aren't produced
        raise NotImplementedError("form bodies are request-only")

    def decode(self, raw: bytes) -> object:
        return dict(parse_qsl(raw.decode()))


# --- domain -----------------------------------------------------------------


class NoteInput(BaseModel):
    """The write body — the core parses the decoded form into this (400 on a bad
    body), so no separate validation step is needed."""

    text: str


@dataclass(slots=True)
class NoteStore:
    """In-memory notes, wired into the resource at the composition root."""

    notes: list[str] = field(default_factory=list[str])

    def add(self, text: str) -> None:
        self.notes.append(text)

    @property
    def version(self) -> int:
        # Monotonic enough for a demo: the ETag changes whenever a note is added.
        return len(self.notes)


@dataclass(slots=True)
class NotesCtx(Ctx):
    fragment: bool = False  # set in generate_etag/represent from the request headers


# --- the resource -----------------------------------------------------------


class NotesResource(Resource[NotesCtx]):
    """GET/HEAD/POST a single notes list, served as HTML to browsers and Unpoly."""

    ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST"})
    PRODUCES = ("text/html",)
    CONSUMES = ("application/x-www-form-urlencoded",)
    context_class = NotesCtx

    def __init__(self, store: NoteStore) -> None:
        self._store = store

    def _is_unpoly(self, ctx: NotesCtx) -> bool:
        # Unpoly sets X-Up-Version on every request it makes; a plain navigation
        # (or curl) does not. That header is what full-page-vs-fragment turns on.
        return ctx.request.headers.get("x-up-version") is not None

    async def variances(self, ctx: NotesCtx) -> Sequence[str]:
        # The representation depends on X-Up-Version, so it belongs in Vary — else a
        # shared cache could serve a bare fragment to a full-page navigation.
        return ("X-Up-Version",)

    async def generate_etag(self, ctx: NotesCtx) -> str | None:
        ctx.fragment = self._is_unpoly(ctx)
        variant = "frag" if ctx.fragment else "full"
        # The variant is part of the validator: the fragment and the full page are
        # different representations at the same URL, so they must not share an ETag.
        return f'"notes-{self._store.version}-{variant}"'

    async def cache_control(self, ctx: NotesCtx) -> str | None:
        # Revalidate every time: the client may hold the fragment, but must check
        # it's current. That check is the conditional GET Unpoly's poll rides on.
        return "no-cache"

    async def represent(self, ctx: NotesCtx) -> object:
        items = "".join(f"<li>{html.escape(text)}</li>" for text in self._store.notes)
        fragment = (
            '<div id="notes" up-poll up-interval="3000">'
            f"<ul>{items or '<li><em>no notes yet</em></li>'}</ul>"
            "</div>"
        )
        # An Unpoly request wants only the fragment to swap; a browser navigation
        # wants the whole document. Same URL, negotiated on X-Up-Version.
        return fragment if ctx.fragment else _page(fragment)

    async def post_is_create(self, ctx: NotesCtx) -> bool:
        return True

    async def create_path(self, ctx: NotesCtx) -> str:
        return "/"  # the collection; see_other overrides the response with a 303

    async def apply(self, ctx: NotesCtx, body: NoteInput) -> None:
        self._store.add(body.text)
        return None

    async def see_other(self, ctx: NotesCtx) -> str | None:
        # POST-Redirect-Get: Unpoly follows the 303, re-fetches /, and swaps #notes.
        return "/"


def _page(fragment: str) -> str:
    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        "<title>Notes</title>"
        f'<link rel="stylesheet" href="{UNPKG}/unpoly.min.css">'
        f'<script src="{UNPKG}/unpoly.min.js"></script>'
        "</head><body>"
        "<h1>Notes</h1>"
        '<form method="post" action="/" up-submit up-target="#notes">'
        '<input name="text" placeholder="a new note" required>'
        '<button type="submit">Add</button>'
        "</form>"
        f"{fragment}"
        "</body></html>"
    )


# --- composition root -------------------------------------------------------


def make_app(store: NoteStore | None = None, *, debug: bool = False) -> Starlette:
    store = store if store is not None else NoteStore(notes=["welcome to asgimachine"])
    codecs: dict[str, Codec] = {
        "text/html": HtmlCodec(),
        "application/x-www-form-urlencoded": FormCodec(),
    }
    return build_app(
        [resource_route("/", NotesResource(store), codecs=codecs)], debug=debug
    )


app = make_app()
