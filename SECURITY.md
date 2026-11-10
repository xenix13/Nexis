# Security policy

## Supported versions

Security fixes are released for the latest stable version (the `main` branch, *Stable* update channel). Servers update themselves from *Settings → Maintenance → Update*.

| Version | Supported |
|---|---|
| 4.x | ✅ |
| < 4.0 | ❌ (update to the latest version) |

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Use GitHub's private reporting instead: [**Report a vulnerability**](https://github.com/xenix13/Noxa/security/advisories/new) (repository *Security* tab → *Report a vulnerability*). Include:

- the affected version and install method;
- a description of the issue and its impact;
- steps to reproduce, or a proof of concept.

You should get a first answer within a week. Once a fix is released, the advisory is published and you are credited if you wish.

## Hardening tips

- Mount media folders read-only.
- Expose Noxa to the Internet only behind an HTTPS reverse proxy, with `SEANCE_COOKIE_SECURE=1` and `SEANCE_TRUST_PROXY=1`.
- Turn on two-factor authentication for administrator accounts.

More in [docs/administration.md](docs/administration.md#security).
