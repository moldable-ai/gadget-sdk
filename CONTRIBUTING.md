# Contributing

Keep the SDK independent of a Moldable source checkout. Public examples should
work from the installed package and use only documented APIs. Follow the
[architecture](docs/architecture.md); proposed host methods need coordinated
host/Relay work and must not be presented as existing capabilities.

Use a virtual environment, then run:

```sh
uv sync --all-extras
uv run --all-extras pytest
uv run --all-extras ruff check .
uv run --all-extras ruff format --check .
uv build
```

Tests should protect wire interoperability, credential lifecycle, recovery or
observable device behavior. Use synthetic identities and an isolated peer;
never require a real account in CI. Document physical hardware separately from
simulator tests, and keep desktop/iOS UI verification in the host QA catalog.

Include docs and an example when changing the public API. Preserve exact wire
spellings, explicit scope, bounded queues, stable mutation IDs and authenticated
desktop output. New dependencies need a license review and lockfile update.
Retain copyright and license notices for any reused source; do not copy artwork
or brand assets without permission. Contributions to original SDK code and docs
are under the repository's MIT license.

For reports, include OS/Python/package versions, a minimal synthetic reproduction
and sanitized errors. Do not attach a state directory, complete pairing link,
whole-flash backup, private conversation or account token.
