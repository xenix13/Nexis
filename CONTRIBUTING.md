# Contributing to Noxa

Thanks for your interest in Noxa! Contributions of every size are welcome: bug reports, ideas, documentation, translations, themes and code.

## Before you start

- **Bugs:** search the [existing issues](https://github.com/xenix13/Noxa/issues) first, then open a [bug report](https://github.com/xenix13/Noxa/issues/new/choose) with your version, install method and the relevant log lines (`journalctl -u seance`). Remove addresses, names and tokens from logs.
- **Ideas:** open a [feature request](https://github.com/xenix13/Noxa/issues/new/choose) describing the problem you want solved, not only the solution.
- **Security issues:** never in a public issue. See [SECURITY.md](SECURITY.md).
- **Bigger changes:** open an issue to discuss them before writing a lot of code, so that we agree on the approach.

## Easy ways to help

- **Themes:** propose one straight from Noxa (*Settings → Appearance → Theme store → Create a theme… → Propose on GitHub*), or see [`static/themes`](static/themes/README.md).
- **Translations:** the interface exists in English and French; adding a language is two files ([how](docs/development.md#translations)).
- **Documentation:** fixes and clarifications to [`docs/`](docs) are always useful.

## Working on the code

Everything you need is in the [development guide](docs/development.md): running from source, tests, code layout and translations.

- Keep the server **standard library only** (no new Python dependencies) and the interface **build-free** (plain ES modules).
- Media folders are **read-only** for Noxa: never write next to the user's files.
- Every user-visible string goes through `tr()` and needs its translation (`tests/test_i18n.py` checks it).
- Add or update tests for what you change, and run `python3 -m unittest discover -s tests -t .` before opening the pull request.
- Open pull requests against the `test` branch. Keep them focused: one topic per pull request.

## Code of conduct

Be kind and constructive. Harassment or discrimination of any kind is not tolerated in issues, pull requests or discussions.
