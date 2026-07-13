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

## Verifying a release

_No releases have been published yet._ When releases begin, artifacts will be
published to PyPI via trusted publishing (OIDC — no long-lived tokens), with
Sigstore signatures, build attestations, and a CycloneDX SBOM attached to the
GitHub release. Verification instructions (`gh attestation verify`, Sigstore
bundle checks) will be documented here then.
