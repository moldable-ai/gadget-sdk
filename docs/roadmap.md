# Delivery plan

The initial preview supplies a working controller client and device bridge.
The stages below also track capabilities beyond that preview; they are not all
implemented or released APIs. See [verification](verification.md) for evidence.

| Stage | Deliverable | Exit condition |
| --- | --- | --- |
| 1 — Python developer preview | Pairing, encrypted controller client, CLI, text example, docs, license, CI | Independent crypto vectors and socket tests pass; package builds; native pairing, text, replay and revocation verified against a current host before public release |
| 2 — Scoped gadgets | Desktop registry, workspace/Bot grants, per-device keys, targeted presentation, Relay authorization | Two devices cannot read or act outside their grants; revocation and reconnect tested through production Relay and native desktop/iOS |
| 3 — Actions and observations | Schema-based device actions, sensor ingest, semantic event routing, durable command outcomes | A Bot controls a synthetic lamp and reads a sensor; denial, timeout, cancellation, duplicate delivery and restart reconcile correctly |
| 4 — Embedded core | ESP-IDF component, one ESP32-S3 profile, desktop simulator sharing the core | Text and button journey passes on simulator and physical board; bounded memory, power loss and reconnect demonstrated |
| 5 — Voice and display | Desktop media bridge, push-to-talk, paced playback, interruption, asset display | Actual recorded and audible round trip; cancellation and device-specific presentation verified |
| 6 — Distribution | More boards, Wi-Fi provisioning, signed OTA/rollback, installers and release artifacts | Reproducible builds, license inventory, upgrade/rollback and physical validation per advertised board |

Current progress:

- Stage 1: implemented and verified with independent protocol/socket tests and
  temporary live desktop pairing, text, replay, reconnect and revocation.
- Stage 2: current source implements host-enforced workspace/Bot grants,
  separate content keys, scoped Relay methods, gadget-aware presentation and
  native management. Production Relay and native verification are tracked in
  the desktop QA catalog; implementation alone is not a release pass.
- Stage 3: current source implements descriptor-bound approvals, bounded schemas,
  durable command outcomes, timestamped observations and a simulated light/sensor
  example. Socket tests cover duplicate/lost-ack delivery, cancellation, invalid
  input and process recovery. Native end-to-end verification is tracked separately.
- Stage 4: the bridge interoperates with the separately licensed Protocol v1
  device core, verified with its real compiled simulator. Board overlays and
  build profiles live with the device plans. Physical board checks remain.
- Stage 5: recorded push-to-talk, host transcription, normal Bot execution,
  host speech synthesis and paced PCM playback pass a live software round trip.
  Cancellation, new-recording interruption, and silent replay are covered.
  Physical microphones/speakers and image output remain separate work.
- Stage 6: the initial `0.2.0a1` alpha is published on PyPI and GitHub with a
  wheel, source archive and checksums. Source installation is also available.
  Signed updates, rollback and physical validation remain separate work.

Scoped grants add a separate `gadget.*` namespace. The desktop refuses to issue
a gadget QR if the Relay does not echo its exact grant scope. Legacy pairings
remain compatible, with their broader access explicitly documented.
Native desktop/iOS QA remains in the desktop repository. Build-time success is
never physical hardware verification. Package publication and a public GitHub
repository are separate release actions.

## Release checklist

- Run protocol/security/socket tests, lint and package build from a clean checkout.
- Inspect wheel/sdist contents for credentials, local paths, fixtures and notices.
- Record compatible host and Relay revisions and live verification in the desktop
  QA catalog. Do not advertise an unverified host range.
- Confirm the public license, contribution and security-reporting policy.
- Tag a preview release only after live pairing/reconnect/revocation passes.
