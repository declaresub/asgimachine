# Releasing

The maintainer-facing runbook for cutting a release. It complements
[SECURITY.md](SECURITY.md), which tells a *consumer* how to verify a release; this
tells the *maintainer* how to produce one.

> **Status: versioning live; publish workflow built; not yet publishing.**
> `pyproject.toml` uses `hatch-vcs`, so `uv build` produces tag-derived versions, and
> [`publish.yml`](.github/workflows/publish.yml) is in place. asgimachine is **not** on
> PyPI yet — the protected environments and the PyPI-side trusted-publisher
> registration (see *Remaining setup* below) must be done before the first tag.

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

The publish workflow — [`.github/workflows/publish.yml`](.github/workflows/publish.yml)
— is built. On a `vX.Y.Z` tag it runs four jobs: **build & SBOM** → **TestPyPI** →
**PyPI** → **sign & GitHub release**. TestPyPI and PyPI publish via **trusted
publishing (OIDC — no long-lived tokens)** with **PEP 740 attestations**; the release
job signs the artifacts with **Sigstore** and attaches the wheel, sdist, `.sigstore`
bundles, and the **CycloneDX SBOM** to the GitHub release. `workflow_dispatch` runs a
no-publish **dry-run** (build + SBOM only).

To cut a release:

1. Choose the version per the `0.x` policy above; update the CHANGELOG.
2. Open a PR with the CHANGELOG; required checks green; **squash-merge** (GitHub signs
   the merge commit).
3. Locally: tag `vX.Y.Z`, run `uv build`, confirm the artifact filename is the exact
   version — *then* push the tag. (The workflow re-checks this and fails the build if
   the built version doesn't match the tag.)
4. Approve the **`testpypi`** then **`pypi`** environment when the run pauses for
   review (the manual gate — do not auto-approve).
5. Verify the published artifacts (wheel, sdist, `.sigstore`, SBOM) on PyPI and the
   GitHub release; then fill in SECURITY.md's "Verifying a release" section.

### Remaining setup before the first release

These are **not yet done** — the workflow can't publish until they are:

- **Protected environments.** `testpypi` and `pypi` must exist with a **required
  reviewer** (the manual approval gate). Until then the environment key doesn't gate
  anything. (Repo settings → Environments, or via `gh api`.)
- **Trusted-publisher registration (PyPI-side, account-level — only the maintainer can
  do this).** On both **PyPI** and **TestPyPI**, add a *pending* publisher (the project
  doesn't exist yet) with these exact values:

  | Field | Value |
  |---|---|
  | PyPI project name | `asgimachine` |
  | Owner | `declaresub` |
  | Repository | `asgimachine` |
  | Workflow filename | `publish.yml` |
  | Environment | `pypi` (on PyPI) / `testpypi` (on TestPyPI) |

- **Account hygiene.** 2FA with a hardware key on PyPI/TestPyPI; no long-lived API
  tokens (trusted publishing only).
