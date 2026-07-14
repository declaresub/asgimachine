# Coverage vs. the webmachine graph

If you're here because you already know webmachine, this is the page you want: an
honest, node-by-node account of how much of the
[v3 decision graph](https://raw.githubusercontent.com/wiki/basho/webmachine/images/http-headers-status-v3.png)
asgimachine implements, what it deliberately leaves out, and where it adds nodes
the original diagram never had.

asgimachine is a **straight-line function**, not a node-per-node interpreter
([`core.run`](../reference.md)), so several adjacent micro-nodes in a region (the
`If-Match` cluster `G8/G9/G11`, say) collapse into a single decision that records
one representative label in the [trace](decision-graph.md). The table below groups
by *behavior* for that reason.

## The main spine — implemented

| Behavior | Canonical node(s) | Result | Callback |
|---|---|---|---|
| Service available? | B13 | 503 | `service_available` |
| Known method? | B12 | 501 | `KNOWN_METHODS` |
| URI too long? | **B11** | 414 | `uri_too_long` |
| Method allowed? | B10 | 405 + `Allow` | `allowed_methods` / `ALLOWED_METHODS` |
| Malformed request? | B9 | 400 | `malformed_request` |
| Authorized? | B8 | 401 (+ `WWW-Authenticate`) | `is_authorized` |
| Forbidden? | B7 | 403 | `forbidden` |
| Valid content headers? | B6 | 501 | `valid_content_headers` |
| Known content type? | B5 | 415 | `known_content_type` / `CONSUMES` |
| Entity length OK? | B4 | 413 | `valid_entity_length` / `MAX_BODY_BYTES` |
| OPTIONS? | B3 | 200 + `Allow` | — |
| Accept → media type | C3/C4 | 406 | `PRODUCES` / `represent` |
| Accept-Language → language | D4/D5 | 406 + `Content-Language` | `languages` / `LANGUAGES` |
| Accept-Encoding → coding | F6/F7 | 406 | `encodings` / `ENCODINGS` |
| Resource exists? | G7 | → missing branch | `resource_exists` |
| `If-Match` | G8/G9/G11, H7 | 412 | `generate_etag` |
| `If-Unmodified-Since` | H10/H11/H12 | 412 | `last_modified` |
| `If-None-Match` | I12/I13/K13, J18 | 304 / 412 | `generate_etag` |
| `If-Modified-Since` | L13/L14/L15/L17 | 304 | `last_modified` |
| PUT to missing → create | I7, P3 | 201 / 409 | `apply`, `is_conflict` |
| Previously existed → moved/gone | K5, K7, L5, M5 | 301 / 307 / 410 | `moved_permanently`, `moved_temporarily`, `previously_existed` |
| Not found | (terminal) | 404 | — |
| DELETE | M16, M20 | 204 / 202 | `delete_resource`, `delete_completed` |
| POST create / action | N11, N16 | 201 + `Location` / 200 | `post_is_create`, `create_path`, `process_post` |
| POST → redirect (PRG) | **N11 see-other** | 303 + `Location` | `see_other` |
| Conflict? | O14 / P3 | 409 | `is_conflict` |
| Multiple choices? | O18 | 300 | `multiple_choices` |
| Response has entity? | O20 | 204 | (return `None`) |

That's the full happy path plus the complete conditional-request suite, content
negotiation, and the write path.

## Extensions beyond canonical webmachine

These nodes aren't in the 2011 v3 diagram; they come from the more RFC-complete
[http-decision-diagram](https://github.com/for-GET/http-decision-diagram) or later
RFCs. Each is additive — a boolean callback with a correct default — so it's
traced only when it fires and never disturbs the canonical path.

| Node | Behavior | Result | Callback |
|---|---|---|---|
| B7a | Legally restricted? | 451 (RFC 7725) | `is_legally_restricted` |
| K5a | Permanent method-preserving redirect | 308 (RFC 7538) | `permanent_redirect` |
| C4a | Serve-anyway (disregard an unsatisfiable `Accept`) | default instead of 406 (RFC 9110 §12.1) | `ignore_unacceptable` / `IGNORE_UNACCEPTABLE` |
| W1 | Precondition required (unconditional write) | 428 (RFC 6585) | `require_conditional_write` |
| P0 | Body parse (parse, don't validate) | 400 | `apply`'s typed `body` |
| — | Negotiated error bodies | RFC 9457 `problem+json` on every 4xx/5xx | `error_body` / `ERROR_PRODUCES` |

The `a`-suffixed labels (`B7a`, `K5a`, `C4a`, and the `N11a` see-other step) mark
additive nodes so they read distinctly in a trace.

## Deliberately omitted (won't add)

- **`Accept-Charset` (E5/E6).** RFC 9110 §12.5.2 **deprecates** the request header
  — UTF-8 is ubiquitous, a charset list wastes bandwidth and eases passive
  fingerprinting, and user agents no longer send it. Charset belongs on the
  Content-Type `charset` parameter now, and letting a deprecated header force a
  hard 406 would run against the spec. See [Negotiation](negotiation.md).
- **Content-MD5 validation** (webmachine folds this into B9). The header was
  removed from HTTP by RFC 7231 and isn't in RFC 9110; there's nothing to check.

## Genuine gaps (not yet implemented)

None of these block real use — each has a clean workaround — but they're honest
divergences from the full graph:

- **POST to a missing resource → create** (M7/N5 `allow_missing_post`). A POST to a
  URI that doesn't exist yet currently falls through to **404**. The idiomatic
  model — POST to the parent *collection* resource, which creates the child — works
  today and is arguably the cleaner design, so this is a low-priority gap.
- **301 on a PUT to a moved target** (I4 `moved_permanently?` on the write path).
  asgimachine resolves "moved" only in the read/missing branch (K5/K5a/L5), not on
  PUT.
- **Finer 201-vs-200 on PUT-to-existing** (P11 `new_resource?`). A PUT that creates
  a *subordinate* resource (sets `Location`) should be 201; asgimachine returns 201
  only for PUT-to-missing (I7) and 200/204 for PUT-to-existing.

## One labeling caveat

asgimachine reuses the label **`L7`** for the terminal 404, whereas in the
canonical flowchart `L7` is the "POST?" decision in the missing-resource branch.
Everywhere else the recorded labels match the diagram, so a trace stays diff-able
against the spec — but that one is a knowing divergence worth flagging to anyone
reading traces against the original.
