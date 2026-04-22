# Security Policy

## Reporting a vulnerability

Please report security issues privately to **founders@ravi.id**.

Do not file a public GitHub issue for suspected vulnerabilities — we'd rather fix it before it's widely known.

Include in your report:

- A description of the issue and the impact you believe it has.
- Steps to reproduce (a proof-of-concept is very helpful).
- The version or commit you tested against.
- Any suggested remediation, if you have one.

## What to expect

- We'll acknowledge your report within **3 business days**.
- We'll share our initial assessment (scope, severity, fix plan) within **10 business days**.
- We'll coordinate disclosure timing with you once a fix is ready. We aim to ship fixes within **90 days** of the initial report.
- We're happy to credit you in the release notes. Let us know how you'd like to be attributed, or if you'd prefer to stay anonymous.

## Scope

In scope:

- The Agent on Demand API (this repository).
- Authentication, authorization, and secret handling (API keys, runtime keys, repo tokens, encryption at rest).
- Session isolation between users.
- The hosted deployment at `aod.ravi.id`.

Out of scope:

- Issues in dependencies — please report those upstream. If the issue is in how we *use* a dependency, that's in scope.
- Issues in the underlying infrastructure we don't control (Render, Postgres, Sprites, model providers).
- Social engineering, physical attacks, and anything requiring access to a user's own machine or account.

## Safe harbor

Good-faith security research against our hosted deployment is welcome. As long as you:

- Avoid privacy violations, destruction of data, and disruption to other users' sessions.
- Only interact with accounts you own or have explicit permission to test.
- Give us a reasonable window to respond before any public disclosure.

…we won't pursue legal action or ask your provider to suspend your account.
