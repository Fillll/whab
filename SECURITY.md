# Security policy

## Report a vulnerability privately

Sign in to GitHub and use [Report a vulnerability](https://github.com/Fillll/whab/security/advisories/new)
to send a private report to this repository's maintainers. GitHub private
vulnerability reporting is enabled for `Fillll/whab` and was verified through the
repository settings API on 2026-10-23.

Include the affected host/plugin versions, how they were installed, the affected
capability and a minimal reproduction using fictional data. Describe the
expected authorization or financial boundary and the observed result. If a
configuration detail matters, provide its setting name and a redacted example.

Keep bank tokens, passwords, encryption/TLS keys, private budget files, real
receipts and household records out of reports, attachments and public issues.
Use synthetic fixtures to demonstrate data exposure or corruption. If a secret
has already been exposed, revoke or rotate it through its owning service before
sharing further diagnostic material.

Maintainers use the private advisory to request clarification, assess impact,
coordinate a fix and agree on disclosure. There is no guaranteed response or
remediation deadline. Use ordinary public issues for non-sensitive bugs; keep
security details in the private advisory until disclosure is coordinated.

## Supported versions

| Component | Security maintenance |
| --- | --- |
| Host and SDK | Current source version on the default `main` branch |
| Bundled plugins | Current independently versioned packages maintained on `main`, with their declared host requirements |
| Earlier host/plugin versions and release tags | Reports are welcome; fixes target current `main`. Older versions do not have a separate backport commitment |
| Third-party plugins | Their maintainers own fixes. Report a vulnerability in WHAB's host boundary here, even if an external plugin exposed it |

Version ranges describe API compatibility, not a promise to backport security
fixes to every compatible release. Tags and releases are published only through
the repository's explicit release process; a source version bump does not imply
a released or installed update.

## Trust boundaries

Installed plugins are trusted executable Python with the process user's
filesystem and network privileges. Scoped SDK inputs reduce accidental coupling;
they do not contain malicious plugin code. A separate worker process provides
failure containment only; an operating-system security sandbox requires
additional restrictions.

The host owns TLS, local Host/origin checks, request validation, financial
validation and documented remote-access exceptions. An API interface plugin must
authenticate remote operations, and bank webhooks require signature verification.
HTTPS alone does not supply a household login system. See the
[architecture and invariants](whab/docs/architecture.md) for the current contracts.
