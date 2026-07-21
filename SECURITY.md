# Security Policy

## Reporting a vulnerability

Please report security vulnerabilities **privately** — do not open a public issue
or PR.

Use GitHub's private vulnerability reporting: the repository's **Security** tab →
**Report a vulnerability**, or open a draft advisory directly at
<https://github.com/declaresub/asgimachine/security/advisories/new>. This creates
a private channel with the maintainer.

Include the affected version or commit, a description of the issue and its impact,
and a minimal reproduction if you have one. You will receive an acknowledgement,
and we will coordinate a fix and disclosure timeline with you.

## Supported versions

asgimachine is pre-1.0 and still evolving. Until a 1.0 release, security fixes
land on `main` and in the latest release; there is no backport guarantee for
older tags. Track `main` for the current state.

## Supply-chain posture

Controls currently in place:

- **Protected `main`** — pull requests required, **signed commits**, required CI +
  security checks, no force-push or deletion, no admin bypass.
- **Immutable `v*` tags.**
- **GitHub Actions pinned to commit SHAs** (enforced repo-wide); least-privilege
  workflow token; workflows audited in CI (zizmor) and dependencies scanned
  (osv-scanner) on every pull request.
- **Dependabot** alerts + security updates (7-day cooldown on new releases);
  **secret scanning** + push protection.
- **Trusted publishing to PyPI** (OIDC, no long-lived tokens) from **protected
  environments that require manual approval**, with PEP 740 attestations.

## Verifying a release

Releases are published to PyPI via **trusted publishing** (OIDC — no long-lived
tokens) by the [`publish.yml`](.github/workflows/publish.yml) workflow, which also
attaches a **CycloneDX SBOM** to the [GitHub
release](https://github.com/declaresub/asgimachine/releases). Every PyPI artifact
carries a **PEP 740 attestation** binding it to this repository and workflow.

### Verify the PyPI attestation

Each wheel and sdist on PyPI has a build attestation, verifiable with
[`pypi-attestations`](https://pypi.org/project/pypi-attestations/):

```console
$ uvx pypi-attestations verify pypi \
    --repository https://github.com/declaresub/asgimachine \
    pypi:asgimachine-0.1.0-py3-none-any.whl
OK: asgimachine-0.1.0-py3-none-any.whl
```

A pass confirms the file was built and published by this repository's workflow, not
re-uploaded by a leaked token. The raw provenance is also served by PyPI, e.g.
<https://pypi.org/integrity/asgimachine/0.1.0/asgimachine-0.1.0-py3-none-any.whl/provenance>,
and shown on each file's page under the PyPI project.

### Pin by hash

`pip`/`uv` verify PyPI's per-file hashes on install; pin them for a
reproducible, tamper-evident install (a lockfile with hashes, or
`--require-hashes`).

### Inspect the SBOM

The GitHub release carries `asgimachine.cdx.json`, a CycloneDX 1.6 bill of
materials listing the runtime dependency closure. Cross-check it against what you
actually install.

> **`v0.1.0` note:** its GitHub-release assets do **not** include Sigstore `.sigstore`
> bundles — a bug in the first release's workflow (since fixed). Its provenance is the
> PyPI PEP 740 attestation above. From the next release onward, the GitHub release also
> carries Sigstore bundles, verifiable with `sigstore verify identity` against the
> workflow identity (`.../.github/workflows/publish.yml@refs/tags/vX.Y.Z`).
