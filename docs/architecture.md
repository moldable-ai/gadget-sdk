> Current source adds the scoped extension described in
> [actions and sensors](actions-and-sensors.md) and [the protocol](protocol.md).
> Descriptions of a shared Remote grant/key below apply to legacy pairings and
> the embedded voice bridge, not Settings → Remote → Add gadget.

# Architecture

Moldable Gadget SDK connects a device to a person's running Moldable desktop.
The desktop owns conversations, Bots, app access, approvals and action outcomes.
The device owns its inputs, display, local hardware and identity.

## Controller client

```text
Button / terminal / device program
                 |
         Python GadgetClient
    pairing · credentials · Bot API
                 |
   signed handshake + encrypted WebSocket
                 |
          Moldable Relay
        routing and revocation
                 |
       Moldable desktop host
  workspace → Bot → authoritative channel
```

The Python client speaks existing Relay protocol v2. It can pair, discover
workspaces and Bots, append text, retrieve transcripts, replay semantic events,
and interrupt a turn. No new server, incoming port, cloud model key, or desktop
plugin is needed. The Mac must be online with Remote enabled.

The initial client is an **experimental remote controller for trusted devices**.
Its selected workspace and Bot are routing choices, not security restrictions.
Current pairing issues the same broad controller scopes as the mobile client.
The room encryption key is shared among paired controllers. Targeted routing
does not provide per-device cryptographic isolation, and revocation does not
erase a key already learned by a device. Do not claim least-privilege gadget
access until the host and Relay changes below ship.

The current desktop Relay transport also owns one selected Remote workspace
shared across controllers. The SDK explicitly selects a workspace and includes
its ID in every scoped request; the host rejects mismatches. Selecting a gadget
workspace can therefore affect a simultaneously connected phone's Remote scope.
Per-controller selection is required before multi-workspace gadgets are a
supported production experience. The Mac's visible workspace is separate.

## Boundaries

- `pairing.py`: validates the copied setup link, pins the desktop identity,
  claims a one-time pairing and rotates credentials.
- `storage.py`: an owner-only, atomic state file and exclusive process lock.
  A device gets its own state directory; it never reads desktop credentials.
- `protocol.py`: established Ed25519, HKDF-SHA256 and AES-256-GCM primitives,
  exact routing fields, signature verification and bounded frames.
- `client.py`: authenticated connection, request correlation, bounded event
  buffering, explicit workspace/Bot methods, reconnection and replay access.
- `cli.py`: provisioning and terminal development commands. Pairing input is
  hidden from terminal echo and never accepted as a command-line argument.
- `examples/`: small device programs using the same public client API.

SDK event delivery is bounded and ephemeral. A disconnect, sequence gap or
overflow requires the caller to recover via `bot.events.replay` or a fresh
transcript; relay sequence numbers are not semantic cursors. Mutations are never
automatically resent. A caller persists its mutation ID with the intended action
before sending and reuses that ID if delivery is uncertain.

## Gadget extension (proposed)

Add a host-owned registry keyed by authenticated device ID and workspace grant.
The registry binds each gadget to explicit Bots/channels, accepted sensor types,
approved actions, and presentation capabilities. Device-supplied descriptions
are untrusted metadata, not permission or agent instructions.

An action descriptor has a stable name, bounded JSON Schema input/output,
side-effect category and timeout. The host validates permission and schema;
the device independently validates arguments and local safety limits. Commands
carry durable IDs and expiry times; devices journal non-idempotent outcomes
before acknowledging. After a disconnect, the host records an unknown outcome
until reconciliation. Never blindly replay an actuator command.

Sensors submit timestamped observations with deduplication keys, units and
quality. An observation updates state without implicitly starting a Bot turn or
speaking. User input and configured automation are separate response triggers.

Display capabilities describe useful limits (text, dimensions, colors, input,
audio); they do not expose platform component trees. Media is asset-addressed
with bounded transfer and cancellation. A confirmed device acknowledgement is
required before the host claims that output was shown or an action completed.

## Host work required

1. Add gadget enrollment with host-approved workspace/Bot/action grants. Enforce
   those grants in the authoritative desktop APIs, including reads and replay.
2. Add per-device encryption keys and a key-rotation/revocation design before
   treating low-trust sensors as isolated peers. Keep existing phones compatible.
3. Add an exact gadget method allowlist in Relay, plus host authorization and
   protocol negotiation. Authentication capabilities never grant permissions.
   Replace the shared Remote workspace selection with per-controller scope.
4. Replace the remote turn path's hard-coded iOS presentation assumption with
   a host-issued target for the actual authenticated device and its capabilities.
5. Add host-to-gadget action requests, acknowledgement, cancellation and durable
   reconciliation. Existing controller-to-host RPC alone does not supply this.
6. Generalize pairing UI labels and show device grants, status and revocation.

## Embedded-device bridge and voice

The implemented [device bridge](device-bridge.md) runs on a trusted Python host.
A device connects over TLS using its own enrolled identity and an independently
implemented Gadget Protocol v1 endpoint. The bridge holds controller credentials
and fixes one workspace/Bot target. It does not expose arbitrary controller
requests to the device. Revocation is checked on input, output, and while idle.

The current device firmware core remains a separately installed, pinned upstream
implementation. Its C++ core has been exercised against the bridge with its real
simulator transport and renderer. Board builds, downstream display adaptation,
host labels and distribution remain distinct deliverables. See provenance in
[third-party notices](../THIRD_PARTY_NOTICES.md). A simulator pass does not prove
physical board behavior.

The optional voice adapter accepts bounded, sequenced push-to-talk PCM. The
bridge negotiates WebRTC through the paired desktop, waits for manual input
mode, transcribes the recording, then submits text through normal Bot admission.
It never asks the voice model for a response or tool call. Reply speech comes
from the desktop's scoped speech-asset service and is paced to the device.
No provider credential is sent to the bridge or firmware. Cancellation before
admission discards the recording; cancellation after admission requests a Bot
turn stop. Recovered replies are text-only and never automatically spoken.

This is recorded push-to-talk, not hands-free full-duplex Voice. Device actions,
image capture, sensor persistence, OTA and broader provider support still require
implementation and their own evidence. None are implied by voice transport.
