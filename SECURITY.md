# Security policy

## Reporting a vulnerability

Report privately through
[GitHub Security Advisories](https://github.com/boanlab/KloudChat/security/advisories/new).
Do not open a public issue.

If you cannot use advisories, email the maintainers listed in
[CODEOWNERS](.github/CODEOWNERS). Include:

- what an attacker can do, and what access they need to start;
- the smallest reproduction you have;
- the version — an image tag or commit SHA.

You should get an acknowledgement within three working days and an assessment
within ten. We will tell you when a fix ships and credit you in the advisory
unless you ask us not to.

## Supported versions

KloudChat is pre-1.0. Fixes land on `main` and in the next tagged release; there
are no maintained backport branches yet. Run `:latest` or a recent tag.

## Scope

In scope, and treated as vulnerabilities:

- Anything that discloses the LiteLLM master key, another user's virtual key,
  or a user API key.
- Cross-tenant access: reading or writing another account's sessions,
  artifacts, projects, files, or usage.
- Authentication and session handling — refresh token replay, privilege
  escalation to `admin`, bypassing the pending-approval gate.
- Credit accounting that lets a user spend past their assigned monthly quota,
  or that bills one account for another's usage.
- Share links that expose more than the shared artifact, or that survive
  revocation.
- Server-side request forgery through connector, gateway, or fetch URLs.
- Path traversal or unauthorised reads against the file store.
- Stored XSS in rendered Markdown, artifacts, or branding assets.

Out of scope:

- Findings in [`KloudChat-LLM`][backend] — report them there. Model
  routing, web search, code execution and speech-to-text are that project's.
- Anything that requires administrator credentials to exploit. An
  administrator can already configure the gateway, upload branding, and read
  organisation-wide usage; that is the role, not a flaw.
- Cost of running an unauthenticated instance. If you deploy with
  `KCHAT_SIGNUP_MODE=open` and no quota, that is a configuration choice.
- Missing hardening headers on a deployment that is not behind TLS. See
  [docs/deployment.md](docs/deployment.md) for what the reverse proxy is
  expected to add.
- Denial of service through resource exhaustion by an authenticated,
  approved user.

## Deployment expectations

Several protections are the operator's, not the application's:

- **Terminate TLS in front of KloudChat** and set `KCHAT_COOKIE_SECURE=true`. The
  refresh token is an httpOnly cookie; over plain HTTP it is readable on the
  wire.
- **Keep LiteLLM off the public network.** KloudChat's `/llm` route is the only
  intended path to it, and that route authenticates with per-user API keys.
- **Set `KCHAT_CORS_ORIGINS`** to the exact origins that serve the app.
- **Treat `.env` as a credential file.** It holds `KCHAT_JWT_SECRET` and, if
  you bootstrap it there, the LiteLLM master key. Rotating `KCHAT_JWT_SECRET`
  invalidates every issued access token, which is the intended effect after a
  suspected compromise.

## Design notes relevant to reports

Two behaviours look like bugs and are not:

**`GET /api/files/{id}/content` accepts the access token as a `?t=` query
parameter.** `<img>`, `<audio>` and `<video>` cannot send an `Authorization`
header, and the token lives in memory rather than in a cookie. Without this,
no image, clip or video this instance generates could be displayed inside it.
The token appears in access logs as a result, so the exception is attached to
that one route (`core/deps.py:current_viewer`) rather than to the default
dependency.

**A revoked share token and a token that never existed both return 404.**
Distinguishing them would confirm to a stranger that a link was once valid,
which is information about somebody else's account.

[backend]: https://github.com/boanlab/KloudChat-LLM
