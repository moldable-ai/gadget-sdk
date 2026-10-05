# Moldable Gadget SDK

Work directly on `main`. Do not delegate unless the user explicitly requests it.
Keep this repository standalone and suitable for public distribution. Never add
credentials, personal workspace data, proprietary host source, or copied artwork.
Retain required attribution whenever third-party code is introduced.

Read `docs/architecture.md` and `docs/roadmap.md` before changing the protocol.
Existing host contracts are authoritative; proposed APIs must be labeled proposed.
Keep transport, credential storage, public client API and hardware adapters separate.
Use explicit types, bounded inputs and established cryptography libraries.
Never silently retry a mutation with a new client mutation ID.

Run `uv run --all-extras pytest` and `uv run --extra dev ruff check .` for SDK changes.
Native desktop/iOS stories and verification state belong in
`../moldable-desktop/docs/qa/`; do not duplicate them here.
Do not publish packages or push to a public repository without being asked.
