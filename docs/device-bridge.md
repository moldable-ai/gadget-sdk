# Embedded-device bridge

The bridge runs on a trusted Mac or Linux host with Python 3.11+. It holds one
Moldable controller pairing and routes one connected device to one configured
workspace and Bot. The device receives neither controller credentials nor an
arbitrary Relay proxy. Current controller pairing still has broad access; keep
the bridge host trusted. Workspace selection is shared with other controllers
on current desktop versions.

The bridge implements text, enrollment, revocation, explicit recovery, and a
voice path enabled with `--voice`. Its software round trip has been verified
with the actual device core and a native desktop; it is not a physically tested
hardware release. Device actions,
sensor persistence, images, and firmware updates are not enabled by this endpoint.
Sensor events cannot implicitly start a Bot or speak.

## Start locally

Pair the bridge using the normal hidden-prompt `moldable-gadget pair` command.
Discover workspace and Bot identifiers, then run:

```sh
moldable-gadget bridge serve --workspace WORKSPACE_ID --bot BOT_ID
```

The default endpoint is `ws://127.0.0.1:8765/gadget`, suitable for a simulator on
the same machine. For a physical device, supply `--host`, `--tls-cert`, and
`--tls-key`. Non-loopback listening requires TLS. The firmware's certificate
bundle must trust the certificate and the device must resolve its hostname.
Device-plan profiles may explicitly embed a private CA at build time. Use that
plan's trust-provisioning workflow for a local certificate; an unprovisioned
self-signed certificate will not work.

Connect firmware implementing the Gadget Protocol v1 contract documented in
[third-party notices](../THIRD_PARTY_NOTICES.md). The device generates its own
32-byte identity key. On first connection it shows a pending identity; on the
bridge host inspect and approve that exact identity:

```sh
moldable-gadget device list
moldable-gadget device approve hg-DEVICE_ID
```

Use the same global `--state-dir` before every command if a custom directory was
chosen. Approval expires after five minutes; forget an expired pending record,
then reconnect. Approval must come from the bridge owner, not from device text.

```sh
moldable-gadget device revoke hg-DEVICE_ID
moldable-gadget device forget hg-DEVICE_ID
```

Revocation cuts off requests and outgoing replies and disconnects the device.
Forgetting permits a new enrollment; it does not itself approve the device.
The bridge checks approval while a connection is idle as well as during traffic.
The registry holds at most 32 identities and resides in a private `devices/`
subdirectory of the controller state directory. It never prints keys.

## Interrupted requests

Before appending text, the bridge saves its intent and mutation ID in `outbox/`.
If the connection or bridge dies, another request is blocked until explicit
recovery. Stop the bridge, then run:

```sh
moldable-gadget bridge recover --workspace WORKSPACE_ID --bot BOT_ID
```

Recovery reuses the saved mutation ID; it does not create a new user request.
It prints replayed text and terminal status to the host terminal. It does not
speak replayed output. Restart `bridge serve` afterwards. Check the desktop if
the host's replay retention has expired. Do not delete an uncertain outbox and
resend unless you intend a second request.

A device disconnect stops local delivery but does not assume the Bot stopped.
The device's Cancel input explicitly requests interruption. Device-side request
IDs deduplicate the last 128 text inputs within one connection; they are not
trusted to remain unique across firmware restarts.

## Verification

The independent socket suite covers enrollment, fixed routing, live revocation,
outbox recovery, and corrupt audio rejection. A separate executable drives the
actual compiled device core, including its own WebSocket transport, identity
storage, HMAC reconnect, display renderer, and revocation:

```sh
uv run --with-editable /path/to/device-sdk python scripts/verify-device-core.py \
  --library /path/to/device-sdk/build/host/libhgsim.dylib \
  --output /tmp/device-core-evidence
```

On Linux use the compiled `.so` path. Build the pinned upstream revision listed
in the notices using its own build instructions first. The script records the
library hash, a framebuffer PNG, and a JSON result. It uses a synthetic desktop
peer, so it does not replace native Moldable QA or physical board testing.

The unmodified reference firmware labels its host with its original name.
Use a device-plan profile with a matching agent label when building for
Moldable. The initial device collection supplies that presentation overlay;
the wire protocol stays unchanged. Flash the matching profile when switching
agents rather than changing only the endpoint behind an incorrect label.

## Recorded push-to-talk (experimental)

Install `pip install -e '.[voice]'`, then add `--voice` to `bridge serve`. The
desktop must have its Realtime API connector and speech service configured. No
provider API key is entered on the device or bridge. This path does not use the
desktop's ChatGPT OAuth voice connector and does not switch providers silently.

A press records up to 60 seconds of mono PCM16. Release submits the recording
for transcription through a host-negotiated WebRTC data channel. Automatic
responses are disabled before audio is sent; only the resulting text is
submitted to the configured Bot. The complete Bot answer is displayed and
synthesized by the desktop speech service. Playback uses 40 ms PCM frames.

Cancel before Bot admission discards transcription. Cancel after admission
requests the authoritative Bot turn stop. A new press stops speaker playback
before microphone capture. Reconnect and explicit recovery never speak replayed
answers. A disconnected bridge preserves an uncertain Bot intent for recovery.

See the official [Realtime push-to-talk flow](https://developers.openai.com/api/docs/guides/realtime-conversations#push-to-talk)
and [aiortc peer connection API](https://aiortc.readthedocs.io/en/latest/api.html).
The implementation uses the desktop's configured transcription model rather
than introducing a separate device-side provider setting.
