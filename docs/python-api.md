# Python API and recovery

`GadgetClient` runs on Python 3.11+ on Linux and macOS. Use it as an async context
manager so the WebSocket and exclusive credential lock are released. Keep one
owner per device identity; independent gadgets use independent directories.

```python
async with GadgetClient("/path/to/private/device-state") as gadget:
    print(gadget.ready["workspaces"])
    await gadget.select_workspace("WORKSPACE_ID")
    print(await gadget.bots("WORKSPACE_ID"))
```

The directory is created with mode 0700; state files are mode 0600 and atomically
replaced. Existing unsafe permissions are rejected. The store contains bearer
credentials and encryption keys in plaintext protected by filesystem permissions.
Use device disk encryption/OS protections as appropriate; this is not a hardware
keystore. Do not copy a paired state directory to another device.

## Methods

| Method | Result and meaning |
| --- | --- |
| `select_workspace(workspace_id)` | Validates the host-advertised workspace and awaits selection acknowledgement |
| `bots(workspace_id)` | Host Bot roster JSON |
| `send_text(workspace_id, bot_id, text, mutation_id=..., thread_id=None)` | Host message acknowledgement; not the completed assistant reply |
| `transcript(workspace_id, bot_id, before_sequence=None, limit=50)` | Bounded page with `messages`, `interactions`, `latestCursor`, `hasMore`, optional `nextBeforeSequence` |
| `replay(workspace_id, bot_id, after_cursor=None, limit=50)` | Bounded `events`, `activeStatuses`, `hasMore`, `resetRequired`, optional `nextCursor` |
| `interrupt(workspace_id, bot_id, turn_id, thread_id=...)` | Host interruption acknowledgement for that exact thread/turn |
| `events()` | Single-consumer async iterator of verified application events from subscription onward |
| `reconnect()` | Refresh if needed, reconnect, authenticate the desktop and restore chosen workspace; does not resend requests |

JSON results preserve the host's current protocol fields. The SDK does not
reimplement a chat store or turn state machine. Runtime inputs are bounded;
unknown/malformed cryptographic frames fail closed. The maximum message text is
24,000 UTF-8 bytes, page size 100, encoded frame size 1 MiB, pending requests 32,
and queued live events 128. Default request/handshake timeout is 30 seconds.

**Current workspace limitation:** the desktop shares its Remote workspace
selection across connected controllers. The SDK selects explicitly and sends
`workspaceId` every time; another controller's selection can cause a rejection.
It cannot silently retarget the request. This is not per-device authorization.
See [architecture](architecture.md) for the required host change.

## Durable messages

Generate a mutation ID once for a real-world intent and persist it alongside
the text, workspace, Bot and optional thread before the first send. Clear the
outbox only after acknowledgement. A timeout means the host may already have
accepted the message. Reconnect, reconcile, and retry the identical intent with
the **same** mutation ID. A new wire request/message ID is expected on retry;
the host's mutation identity is what prevents duplicate domain work.

The [terminal button](../examples/terminal-button.py) demonstrates an atomic,
owner-only outbox. It asks before retrying a pending request after restart and
does not move that request to a different Bot if command-line options change.

## Events and replay

For a simple display, use transcript/replay as the authoritative path:

1. Fetch a transcript and remember `latestCursor` after applying the page.
2. Fetch replay after that cursor; apply events in the host's order, then save
   `nextCursor`. Follow `hasMore` immediately; do not skip to an outer relay sequence.
3. If `resetRequired` or `cursor_stale` is returned, obtain a fresh transcript
   and rebuild the projection. Follow `nextBeforeSequence` for older history.
4. After reconnect, replay from the last applied cursor. Do not assume that
   missed live events were buffered by the relay.

The `watch` CLI implements this read-only loop and retries network interruption
with bounded backoff. It prints JSON pages and a reset marker; consumers that
need restart persistence must save the last applied cursor themselves.

`events()` is optional for low-latency invalidation. Events can include other
authorized channels, so filter workspace and channel identity before rendering.
Duplicate signed envelopes are dropped using a bounded in-memory set. A relay
sequence gap or local queue overflow ends delivery with `ReplayRequired`;
reconnect and recover via the host cursor. No events are accumulated without a
live subscriber. Only one consumer may iterate a client's events at once.

## Errors

- `ConnectionLost`: connection ended or acknowledgement timed out; a mutation
  may have completed. Explicitly reconnect and reconcile.
- `ReplayRequired`: live event continuity was lost. Rebuild/replay the projection.
- `RemoteError`: host or relay rejected a request; inspect `.code`. No automatic
  mutation retry or permission escalation occurs.
- `ProtocolError`: malformed or unauthenticated data. Stop and investigate.
- `PairingRequired`: credentials are missing, revoked or unrecoverable.

Access expiry closes the socket; the next reconnect rotates credentials when
needed. Refresh requests are signed and serialized under the process lock;
the old session ID is the stable refresh idempotency key. A lost HTTP response
leaves the old local state intact for recovery on a later attempt. Current
Relays can replay supported rotations; older `refresh_already_used` responses
require a new pairing. Revocation events and close code 4003 delete local state.

## Development Relay

The default pairing origin is `https://relay.moldable.sh`. A custom TLS Relay
must be selected explicitly with `pair --relay https://your-relay.example`.
HTTP/WS are allowed only for loopback development with
`--allow-insecure-localhost`. The claimed WebSocket must match the chosen
origin and desktop. HTTP redirects and ambient proxy configuration are disabled.

## Scoped gadgets and device runtime

For an Add gadget pairing, `client.grant` contains the fixed workspace/Bot.
Selecting those same IDs is harmless; other IDs fail. `bots()` returns only the
assigned Bot and `events()` polls its durable replay without phone broadcasts.
Use `GadgetRuntime`, `Action` and `Sensor` for [actions and observations](actions-and-sensors.md).
Existing legacy pairings retain the behavior described above.
