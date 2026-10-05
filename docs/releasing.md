# Publishing the SDK

The GitHub repository is `moldable-ai/gadget-sdk`. The PyPI distribution name is
`moldable-gadget-sdk`; its import and command names are `moldable_gadget` and
`moldable-gadget`.

## First publication

An authorized PyPI account configures a pending GitHub Trusted Publisher at
<https://pypi.org/manage/account/publishing/> with:

- PyPI project: `moldable-gadget-sdk`
- GitHub owner: `moldable-ai`
- Repository: `gadget-sdk`
- Workflow filename: `release.yml`
- Environment: `pypi`

The repository's `pypi` environment must exist. Publishing uses GitHub OIDC;
do not add a long-lived PyPI token to the repository. See
[PyPI's first-publication instructions](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).

## Each release

1. Record implemented capabilities, limitations and compatible host evidence.
   Follow the verification gates in [the delivery plan](roadmap.md).
2. Set the intended version in `pyproject.toml`, update `uv.lock`, and commit on
   `main`. The version is immutable once published on PyPI.
3. Run SDK tests, lint, formatting and package build. Inspect distributions for
   unintended files. Confirm the release workflow and PyPI publisher are ready.
4. Create and push an annotated tag whose name is `v` plus that exact version,
   for example `v0.1.0a1`.

The release workflow runs the Linux/macOS test matrix, checks tag/version parity,
builds wheel and source distributions, validates their metadata, and installs
the wheel into a clean environment outside the checkout. It then publishes to
PyPI and creates a GitHub release with those exact distributions and SHA-256
checksums. Alpha, beta, release-candidate and development versions are marked
as GitHub prereleases. Never retag a published release to different source.

After publication, install the exact PyPI version in a fresh environment and
check the CLI. Verify the public metadata, release artifacts and checksums.
Update the README's package installation instructions only after the package
is confirmed available.

## GitHub-only previews

GitHub release artifacts can ship before PyPI account setup is complete.
The `pypi` job runs only when repository variable `PYPI_PUBLISH_ENABLED` is
`true`; leave it unset until the trusted publisher is configured. The GitHub
release still requires all checks and the clean installation test to pass.
After configuring PyPI, enable the variable before tagging the next version.

## CI cost policy

Finish tests, lint, formatting and package validation locally before tagging.
SDK checks run only through a release or an explicit manual dispatch; ordinary
pushes and pull requests do not spend hosted runner time. Publish one prepared
candidate rather than tagging intermediate work.
