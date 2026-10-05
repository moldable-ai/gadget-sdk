# Moldable Relay v2 interoperability

This SDK implements the existing controller role. Gadget-specific methods,
device actions and binary audio are not part of this implementation.

## Pairing and identity

The desktop supplies `moldable-remote://pair?setup=<base64url-json>`. The setup
contains `v: 2`, `minProtocol`, `maxProtocol`, `pairingId`, `secret`, `relayURL`,
`expiresAt`, `e2eeKey`, and `desktop: {id, name, publicKey}`. Keys are raw 32-byte
values encoded as unpadded base64url. The SDK validates expiry and exact origin.

The device generates an independent Ed25519 key and UUID, then sends only
`pairingId`, `secret` and its public device descriptor to `POST /v1/pairings/claim`.
The room key never appears in that HTTP request. The returned desktop identity
must match the identity pinned by the setup link.

The response supplies `sessionId`, `accessToken`, `refreshToken`, `expiresAt`,
`refreshExpiresAt`, `websocketURL` and `desktop`. Keep the acronym spellings
exactly as shown. Refresh posts to `/v1/sessions/refresh`, signing these LF-joined
fields with no trailing newline:

```text
moldable-relay-refresh-v1
<sessionId>
<deviceId>
controller
<refreshToken>
<signedAt>
<idempotencyKey = old sessionId>
```

## Connection

Connect with an `Authorization: Bearer <accessToken>` header. Sign the
`connect.challenge` nonce using these LF-joined fields:

```text
moldable-relay-v2
<nonce>
<sessionId>
<deviceId>
controller
2
2
<signedAt>
```

Send the signature and matching fields in a plaintext `connect` request. The
SDK omits the optional capability field. If capabilities are added in a future
version, their comma-joined value must also be included in the signed proof.

After protocol 2 is acknowledged, send encrypted `remote.hello` with a random
32-byte `clientNonce`. The signed/encrypted `ready` event also proves the desktop
key over `moldable-remote-desktop-proof-v1`, the nonce, desktop ID and `2`, joined
by LF. Verify it before trusting the advertised workspaces.

## Application envelopes

Requests contain `type: "req"`, `id`, `method`, a fresh UUID `messageId` and
`encrypted: {v: 1, alg: "A256GCM", nonce, ciphertext}`. JSON parameters go only
inside the ciphertext. Each direction derives a separate AES-256-GCM key:

- HKDF-SHA256 input: raw room key.
- Salt: SHA-256 of UTF-8 `moldable-relay-e2ee-v1`.
- Info: UTF-8 `desktop:<desktopId>:<direction>`.
- Output: 32 bytes. Direction is `controller-to-desktop` or `desktop-to-controller`.

AES-GCM uses a random 12-byte nonce. Ciphertext includes the 16-byte authentication
tag. Additional authenticated data is seven LF-joined fields, no trailing LF:

```text
moldable-relay-e2ee-v1
<direction>
<type>
<id or empty>
<method or empty>
<event or empty>
<messageId>
```

Desktop responses decrypt to `{result: ...}` or `{error: ...}`. Desktop events
use outer `event: "remote.event"` and decrypt directly to the application event.
Both require a desktop Ed25519 signature over these LF-joined fields:

```text
moldable-remote-envelope-signature-v1
<type>
<id or empty>
<method or empty>
<event or empty>
<messageId>
<base64url nonce>
<base64url ciphertext>
```

Verify the pinned desktop signature before decryption. Routing fields reject CR,
LF and NUL and are at most 128 characters. Encoded WebSocket frames are at most
1 MiB. Outer `seq` is Relay metadata for gap detection, not a semantic cursor.
Application successes and events in plaintext are rejected; Relay challenge,
connect result, presence, protocol errors and revocation are the narrow exceptions.

## Domain routing

Call `workspace.select` with `workspaceID` before scoped methods. Each Bot method
then sends `workspaceId` and `botId`. The host derives its internal channel ID.
Text append uses `body: {text, format: "plain"}`, stable `clientMutationId` and
optional `threadId`. Interrupt requires both `turnId` and `threadId`. Transcript
pagination uses `beforeSequence`; replay uses the opaque `afterCursor`.

Reference baseline: desktop source `2e09598966764a0f4d47a4c2ed18c46dbbf9fa18`
and Relay source `6d9d3507461d4441ea5ae5359d4d56df8cab30ab`. These identify
inspected source, not a certified deployed compatibility range.
