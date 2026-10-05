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
- Stage 2: a local bridge registry issues a separate device identity and fixes
  routing to one workspace/Bot. This is **not** a host-enforced narrow controller
  grant. Host/Relay capability grants and a native device-management UI remain.
- Stage 3: device actions and durable sensor ingestion remain future work.
- Stage 4: the bridge interoperates with the separately licensed Protocol v1
  device core, verified with its real compiled simulator. Board overlays and
  build profiles live with the device plans. Physical board checks remain.
- Stage 5: recorded push-to-talk, host transcription, normal Bot execution,
  host speech synthesis and paced PCM playback pass a live software round trip.
  Cancellation, new-recording interruption, and silent replay are covered.
  Physical microphones/speakers and image output remain separate work.
- Stage 6: source installation is available. Hosted releases, signed updates,
  rollback and physical validation are not supplied by this preview.

The client and current bridge add no host wire methods. Host-enforced grants
will require coordinated desktop and Relay releases with compatibility gates.
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
