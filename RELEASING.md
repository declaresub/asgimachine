# Releasing

The maintainer-facing runbook for cutting a release. It complements
[SECURITY.md](SECURITY.md), which tells a *consumer* how to verify a release; this
tells the *maintainer* how to produce one.

> **Status: versioning is live; publishing is not.** The versioning setup below is
> **in effect** — `pyproject.toml` uses `hatch-vcs`, so `uv build` already produces
> tag-derived versions. asgimachine is **not** on PyPI yet; the release-flow section
> is an outline to be completed when we commit to publishing.

## Versioning

**Tags are the source of truth. The package version is computed from the nearest
git tag** — no `version = "…"` string to bump by hand, no version living in two
places.

We use [`hatch-vcs`](https://github.com/ofek/hatch-vcs), which is
`setuptools_scm`'s machinery exposed through the `hatchling` backend we already use —
so it's the same tag-driven behavior, with no build-backend switch.

### How a version is derived

| Working tree | Computed version |
|---|---|
| Clean, sitting on tag `v0.2.0` | `0.2.0` |
| 3 commits past `v0.2.0` | `0.2.1.dev3+g<sha>` (guesses next patch, marks dev, adds a local segment) |
| Dirty | as above, plus a `.dYYYYMMDD` date |

PyPI rejects the `+local` segment and we don't want `.devN` noise on the index —
which is exactly why **we publish only from a tagged commit**, where the version
comes out clean. Between releases, local builds carry the dev suffix; that's correct
and harmless.

### pyproject.toml configuration (in effect)

```toml
[project]
name = "asgimachine"
dynamic = ["version"]                     # was: version = "0.0.0"

[build-system]
requires = ["hatchling", "hatch-vcs"]     # add hatch-vcs
build-backend = "hatchling.build"

[tool.hatch.version]
source = "vcs"

[tool.hatch.build.hooks.vcs]
version-file = "src/asgimachine/_version.py"   # generated at build time
```

The generated `src/asgimachine/_version.py` is a build artifact — **gitignore it**,
never commit it.

### Reading the version at runtime

Resolve it from installed metadata (not by importing the generated file, which
doesn't exist in a source checkout), in `src/asgimachine/__init__.py`:

```python
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("asgimachine")
except PackageNotFoundError:      # running from a source tree, not installed
    __version__ = "0.0.0"
```

`uv` installs the project editable, so this resolves in development too.

### Tag format: `vX.Y.Z`

Use a `v` prefix. This isn't only convention: the repo's hardening put an
**immutable-tag ruleset on `refs/tags/v*`** (no deletion, no force-push), so a `v`
tag is protected the moment it's pushed. `hatch-vcs` matches `v0.2.0` and strips the
`v` by default — it lines up for free.

### Versioning policy while pre-1.0

asgimachine is `0.x` and experimental, so it follows **`0.x` semantics: anything may
change between minor versions.** Breaking changes ship in a minor bump and are called
out in the CHANGELOG; we don't promise SemVer stability until a `1.0`. (See
[SECURITY.md](SECURITY.md) on the lack of a backport guarantee pre-1.0.)

### Two footguns

1. **CI must fetch tags and full history.** `actions/checkout` defaults to a shallow
   clone with **no tags**, so a build there would compute `0.0.0` or fail. Any job
   that builds the project (CI `test`, docs `build`, and the future publish workflow)
   sets `fetch-depth: 0` on checkout — already done for the first two. The security
   job doesn't build the project, so it stays shallow.
2. **Tags are immutable — verify the version *before* pushing the tag.** Since the
   `refs/tags/v*` ruleset forbids deletion/force-push, a wrong tag is permanent:

   ```bash
   git tag v0.2.0
   uv build        # dist/asgimachine-0.2.0-*.whl  ← the filename MUST be the version you expect
   ```

   If the filename is wrong, delete the *local* tag and fix it before it reaches the
   remote.

## Release flow

_Outline — to be fleshed out (and the workflows built) when we commit to publishing._
The provenance controls below are already the stated intent in
[SECURITY.md](SECURITY.md); this section will become the concrete step list.

1. Choose the version per the `0.x` policy above; update the CHANGELOG.
2. Open a PR with the CHANGELOG (and, first time, the pyproject `hatch-vcs` change);
   required checks green; **squash-merge** (GitHub signs the merge commit).
3. Locally: tag `vX.Y.Z`, run `uv build`, confirm the artifact filename is the exact
   version — *then* push the tag.
4. The tag triggers the publish workflow: build → **TestPyPI** (manual approval) →
   **PyPI** (manual approval), via **trusted publishing (OIDC — no long-lived
   tokens)**, with Sigstore signatures, build attestations, and a CycloneDX SBOM
   attached to the GitHub release.
5. Verify the published artifacts (wheel, sdist, `.sigstore`, SBOM) on PyPI and the
   GitHub release; then fill in SECURITY.md's "Verifying a release" section.

The publish workflow, protected `testpypi`/`pypi` environments, and the SBOM step are
tracked as the remaining publishing work — not yet built.
