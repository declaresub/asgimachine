# Content negotiation

**The idea, first — because it's under-used:** one URL can have several
*representations*. A client says which formats it can handle in the `Accept` header;
the server picks the best one it offers and returns *that* — or `406 Not Acceptable`
if it can't meet the request. So `GET /widgets` can hand JSON to your app and CSV to
a spreadsheet, from the same URL, with no `?format=csv` hack.

**The goal here:** offer a resource in more than one format. Declare the formats in
`PRODUCES`, build the value *once* in `represent`, and register a codec per format.

```python
import csv
import io

from asgimachine.codec import JsonCodec
from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route


class CsvCodec:
    def encode(self, value: object) -> bytes:       # value is what represent() returned
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(value[0].keys()))
        writer.writeheader()
        writer.writerows(value)
        return buf.getvalue().encode()

    def decode(self, raw: bytes) -> object:
        raise NotImplementedError                   # only needed if you also CONSUME csv


class Widgets(Resource):
    PRODUCES = ("application/json", "text/csv")      # offered, in preference order

    async def represent(self, ctx: Ctx) -> object:
        return [{"id": 1, "name": "cog"}, {"id": 2, "name": "gear"}]


app = build_app([
    resource_route(
        "/widgets",
        Widgets(),
        codecs={"application/json": JsonCodec(), "text/csv": CsvCodec()},
    ),
])
```

```
$ curl -s localhost:8000/widgets -H accept:application/json
[{"id":1,"name":"cog"},{"id":2,"name":"gear"}]

$ curl -s localhost:8000/widgets -H accept:text/csv
id,name
1,cog
2,gear

$ curl -s localhost:8000/widgets -H 'accept:image/png' -o /dev/null -w '%{http_code}\n'
406
```

## One value, many formats

The key separation: **`represent` builds a domain value; a codec turns it into the
chosen format.** `represent` returns the *same* list of dicts no matter what was
asked for — it never looks at `Accept`. The framework negotiates a media type and
hands the value to that type's codec to encode. Adding XML later is a new codec and
one more entry in `PRODUCES`; `represent` doesn't change.

## How the negotiation goes

The core matches the request's `Accept` against `PRODUCES` (RFC 9110 proactive
negotiation):

- **q-values and wildcards** are honored — `Accept: text/csv;q=0.9,
  application/json;q=0.5` picks CSV; `*/*` or a missing `Accept` takes the first
  offered type (JSON here).
- **Nothing acceptable is a `406`**, with the negotiated `problem+json` error body.
- The response `Content-Type` is the chosen type, and **`Vary: Accept` is set
  automatically** whenever more than one type is offered, so caches key on it.

The chosen type is on `ctx.chosen_media_type` if `represent` ever needs to branch on
it — but usually it shouldn't; that's the codec's job.

## Serve the default instead of 406

If you'd rather ignore an unsatisfiable `Accept` and serve your default than reject
it, opt in:

```python
class Widgets(Resource):
    PRODUCES = ("application/json", "text/csv")
    IGNORE_UNACCEPTABLE = True      # an unofferable Accept -> PRODUCES[0], not 406
```

## Notes

!!! note "Codecs replace the default registry"
    Passing `codecs=` **replaces** the JSON-only default — it doesn't merge. Include
    `application/json` explicitly (as above) if you still offer it. A `PRODUCES` type
    with no matching codec can't be encoded.

- **Request bodies negotiate too**, the mirror image: `CONSUMES` + the same codec's
  `decode`. See [Accept a request body](request-body.md#more-than-one-content-type).
- **Language and encoding** are separate negotiation axes (`LANGUAGES`, `ENCODINGS`)
  handled the same way — see [Negotiation & errors](../concepts/negotiation.md).
