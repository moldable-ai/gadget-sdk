# Verification

The automated tests run against an independent synthetic HTTP/WebSocket peer
using real sockets and cryptography. They protect the SDK's wire shapes,
request/response correlation, signed identity, refresh persistence, single-writer
storage, no silent mutation retry, event deduplication and revocation handling.
They do not prove that a deployed desktop persists one message or runs a Bot.

The protocol fixture is independently generated with Node's crypto implementation;
the Python encoder/decoder must match its fixed bytes and reject tampering.
Fixture keys are synthetic and have no account access.

```sh
uv run --all-extras pytest
uv run --all-extras ruff check .
uv run --all-extras ruff format --check .
uv build
```

## Scoped foundation (current source)

The 0.2 source adds scoped grants, actions, sensors and a durable command journal.
On October 5 its 51 automated tests passed, including real encrypted transport
fault injection for duplicated command delivery, lost result acknowledgements,
restart, cancellation and schema rejection. The wheel/source build passed.
Relay revision `7d6b60e` passed all 181 tests, including authenticated gadget
socket isolation, all nine methods, denied broad APIs, phone broadcast isolation,
and revocation, and was deployed to production with migration `0009`.

The native macOS check also passed scoped pairing, same-mutation text replay,
two-Bot isolation, denied general Remote methods, capability approval, schema
change reapproval, a confirmed simulated light action, timestamped temperature,
offline cancellation/expiry, and both online and offline revocation. The same
SDK process survived a host restart and resumed observations without replaying
the light action. All temporary gadget access was revoked and the original
workspace restored.

The final signed QA executable SHA256 was
`96130660ecc723e3721cabba3819576a76b95c15e70649680c84b05728e0fd55`;
AI build `58dca0b62e6854fd4d5259b2`. Compatible desktop source is
`a62dd0d633c2d254e35981b735f785d7851e5114` (final changes include formatting-only
cleanup after the native run). No compatible released desktop version is asserted.
An authorized temporary iOS 27 simulator pairing subsequently passed phone
continuity and phone-initiated actions with iOS source `05729e3d7afd`. It opened
the existing QA conversation before gadget pairing, requested the approved
light action, and displayed the confirmed result and 22.5 °C. The one device
journal entry matched the host receipt. After gadget revocation, a phone
relaunch restored its QA workspace and reopened the same answer. Both temporary
connections were revoked; the pre-existing physical phone pairing was preserved.
That run used desktop `a62dd0d633c2` and QA executable SHA256
`ceb3eee882382a0dfc77955d6d634b04a4a53fa375898591d9184b6adfd6652a`.
Physical hardware remains unverified. The legacy baseline below is separately
scoped to 0.1.

## Verified preview baseline

On October 5, 2026, Python preview `0.1.0a1` passed a live macOS desktop check
against desktop source `2e09598966764a0f4d47a4c2ed18c46dbbf9fa18`, signed QA
executable SHA256 `967d098c0e997298e54c6c4b64fd5c643313db26a50f15004b9f3ebb3f2af1a9`,
and AI build `1dccc8bd0eee508277bedc59`, using the hosted production Relay.
The deployed Relay revision was not independently established.

The test paired a separate SDK identity, used a dedicated QA workspace with
Luna Medium, displayed the same request and answer in the native desktop,
reconnected and replayed the answer, retried one mutation without another user
message or turn, refreshed credentials, and revoked the live device. Revocation
erased local credentials and a subsequent server refresh was rejected. The
pre-existing iPhone pairing remained listed; iPhone operation was not tested.
The temporary device was revoked and the original desktop/Remote workspace
selection restored. This validates one host build, not a compatibility range.

The host may return the original cached acknowledgement with `replayed: false`
on a successful duplicate retry. Compare authoritative message/turn identities
and transcript/replay state rather than using that flag as a delivery count.

## Live release gate

Use a dedicated Moldable QA workspace and a Luna Medium Bot. Through the current
native desktop build, pair a distinct SDK device identity, send one synthetic
request, verify its content and resulting reply in the desktop, reconnect and
replay, retry the same mutation ID without duplicate domain work, and revoke the
SDK identity. Verify another paired client and its scope remain usable.

Native stories and results are maintained in the desktop repository's
`docs/qa/`; a local peer pass must never be recorded as a Computer Use pass.
Live pairing creates persistent remote access and must be authorized. Physical
Pi/ESP32 tests need exact hardware and firmware revisions. They are separate
from both desktop QA and host-side protocol tests.

No public release, live host compatibility certification or physical device
support is implied by building a wheel successfully.

## Embedded bridge and voice — October 5, 2026

The actual compiled round-display device core completed enrollment, text,
HMAC reconnect and revocation against this bridge. The independently implemented
desktop peer was synthetic for that first check. The later native-host run used
a real paired desktop and provider: a synthetic recording was transcribed,
submitted to a Luna Medium QA Bot, displayed in native chat, and returned as
4.2 seconds of mono 16 kHz PCM to the device core's simulated speaker. This is
software media verification; no physical microphone, speaker or board was used.

The same core verified cancellation before admission (no extra user message),
Talk interrupting playback, reconnect without replayed speech, and device
revocation. A pre-fix provider configuration-ordering failure was reproduced
and is guarded by a real local WebRTC data-channel regression. Offline controller
revocation also exposed an HTTP-401 upgrade case; the client now refreshes once
and resolves revoked grants to a pairing-required error.

Native build, exact device-core revision, scoped conversation evidence, audio,
and cleanup are maintained in the desktop repository's
`docs/qa/status.md` and `docs/qa/suites/gadget-sdk.md`.

The Anything Devices collection now supplies the matching host label, an
E1002 persistent-text port, pinned board build profiles and optional local-CA
trust. Both embedded targets compile with ESP-IDF v6.0.1. The 800 × 480 device
simulator also verifies a button request, a stable reply beyond the normal
display timeout, HMAC reconnect and revocation. These are software results;
physical GPIO, display refresh, audio quality, battery life and flashing remain
unverified. Firmware build hashes and notices live with those device plans.

## Initial package publication — October 6, 2026

Release `v0.2.0a1` targets SDK commit
`47aa3ff404d4fa235b006e8d3986ed768ecc2bd5`. The prepared release's incomplete jobs
were retried after GitHub's runner-allocation incident; its already passing
macOS/Python 3.11 job was reused. Linux/macOS on Python 3.11 and 3.13 all passed,
as did the build, clean wheel installation, PyPI Trusted Publishing and GitHub
artifact publication.

A fresh installation from PyPI outside the checkout imported `GadgetClient`
and ran both CLI entrypoints on Python 3.12.8. The downloaded GitHub wheel and
source archive matched their release checksums and PyPI SHA-256 digests.
Native host and physical-hardware limits above still apply.

[Release](https://github.com/moldable-ai/gadget-sdk/releases/tag/v0.2.0a1) ·
[PyPI](https://pypi.org/project/moldable-gadget-sdk/0.2.0a1/) ·
[Workflow](https://github.com/moldable-ai/gadget-sdk/actions/runs/37368997319)
