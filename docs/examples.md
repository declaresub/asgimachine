# Examples

Runnable apps live in [`examples/`](https://github.com/declaresub/asgimachine/tree/main/examples).
Run any of them with:

```bash
uvicorn examples.<name>:app --reload
```

| Example | Shows |
|---|---|
| `accounts` | a pure-read resource with conditional GET (the canonical adoption target) |
| `feed` | immutable, CDN-cacheable feed pages — the outbox case |
| `events` | Server-Sent Events streaming *through* the graph |
| `connection` | per-request DB connection via [`lifespan`](concepts/lifespan.md) |
| `notes_app` | the full gradient: collection + member resources, the command lane, an auth policy, and self-served OpenAPI |

## The resource gradient (`notes_app`)

The dogfood app is the best tour — it puts several resource shapes, the command
lane, and an authorization policy side by side without cosplay:

- `GET /health` — a public read-only resource (no auth).
- `POST /token` — the **command lane**: credential exchange → bearer token.
- `GET/POST /notes` — a **collection**: any authenticated user may list or create.
- `GET/PUT/DELETE /notes/{id}` — a **member** whose authorization runs through an
  ordered Allow/Deny [`RuleEngine`][asgimachine.policy.RuleEngine] (owner or admin).

It also serves its own **OpenAPI** document at `/openapi.json`, generated from the
resources — the error surface (`401`/`403`/`404`/`406`/`415`/…) is *derived from
which callbacks each resource overrode*, and models are hoisted into
`components.schemas`.

## Immutable feeds (`feed`)

The endpoint class that most rewards the whole exercise. Archived feed pages are
full and will never change, so they get a stable `ETag` and
`Cache-Control: …, immutable` — a CDN can hold them forever. The head page (still
filling) is `no-cache` and revalidates via conditional GET (a `304` whenever
nothing new arrived).

```bash
curl -i localhost:8000/feed/0     # archived -> immutable
curl -i localhost:8000/feed/2     # head -> no-cache, revalidate
```
