# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Versioning is `0.x` and pre-1.0: the API may change between minor versions, and
breaking changes ship in a minor bump and are called out here. There is no SemVer
stability promise until a `1.0` release. Versions are derived from git tags
(`vX.Y.Z`) — see [RELEASING.md](RELEASING.md).

## [Unreleased]

## [0.1.0] - 2026-07-21

Initial release — the webmachine v3 subset plus a slice of RFC-completeness
extensions, on a Starlette substrate.

### Added

- **The decision graph.** A resource is a class of small `async` callbacks
  (`is_authorized`, `resource_exists`, `generate_etag`, `represent`, …); the core
  walks the webmachine v3 graph over them. Content negotiation, conditional requests
  (`ETag` / `If-None-Match` / `If-Modified-Since`), `304`, `405 + Allow`, `406`, and
  the POST/PUT/PATCH/DELETE write path fall out of the graph, each with a correct
  default.
- **Additive nodes beyond canonical v3** — `451` (legal), `308` (permanent redirect),
  serve-anyway negotiation, `428` (precondition required), `303` (POST-Redirect-Get),
  `202` (async hand-off), `414` (URI too long), `429` (per-client rate limit), and
  RFC 9457 `problem+json` error bodies.
- **Content negotiation** across media type, language, and encoding, with automatic
  `Vary`.
- **Two lanes** — graph resources and plain `Command` handlers for genuinely
  command-shaped endpoints.
- **Observability** — a decision trace (`X-Asgimachine-Trace`), wide-event logging via
  an `EventSink` (OpenTelemetry conventions), and an `on_exception` catch-all.
- **OpenAPI 3.1** generation derived from resource behavior.
- **Streaming / SSE** responses with a post-commit error boundary.
- **Authorization helpers** (`asgimachine.auth`) that parse the `Authorization` header,
  and an ordered Allow/Deny `RuleEngine`.
- **Typed throughout** (PEP 561 `py.typed`); requires Python 3.13+.

[Unreleased]: https://github.com/declaresub/asgimachine/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/declaresub/asgimachine/releases/tag/v0.1.0
