# Security

This is a developer preview for trusted devices. With current source and a
compatible desktop/Relay, **Add gadget** issues a separate content key and a
host-enforced grant to one workspace and one Bot. The Relay binds the grant to
the authenticated controller, rejects broad Remote methods, excludes gadgets
from phone broadcasts and phone authentication presence, and preserves its
scope through credential refresh. Desktop rejects attempts to retarget it.

Legacy **Pair Moldable for iOS** links still grant general Remote controller
access and share the legacy room key/workspace selection. Selecting a Bot in
that client does not narrow its grant. Do not mistake a legacy pairing for the
new scoped path. Embedded voice currently uses this legacy path.

A scoped gadget can read and add to its assigned Bot conversation. Choose a
Bot whose accessible context is appropriate for the device. Capability approval
is required separately for every action/sensor and bound to its full descriptor;
changed descriptions, units or schemas need approval again. A gadget's reported
physical result is an assertion from trusted device code, not independent
hardware attestation. Sensor readings are untrusted data and never themselves
start a Bot turn or speech.

Pair only code and hardware you control. Each device creates its own Ed25519
identity. The SDK pins the desktop key from the copied setup, verifies desktop
signatures, encrypts application content, restricts the Relay origin and refuses
plaintext application results. TLS still matters for metadata and credentials.

State files contain secrets protected by owner-only filesystem permissions, not
an encrypted vault. Do not share or commit them, reuse a device state directory
on another machine, or place it inside a public/synced project folder. The CLI
does not accept pairing links as command-line arguments, and `status` excludes
secrets. A stolen device may retain keys even after revocation; revocation blocks
its authorized Relay access but does not erase its storage.

To remove a device, revoke it in the desktop and run `moldable-gadget forget`
locally. Local deletion alone does not revoke server-side access. The SDK has no arbitrary shell, file-access or firmware-flashing API. Approved
actions call handlers registered by device code. The private SQLite journal
admits a command before execution and retains its result before acknowledging
it. Never delete or replace that journal to retry an uncertain physical effect;
inspect the device instead. Async handlers must respect cancellation and return
only after verifying the outcome. Cancellation cannot undo physical changes.

## Reporting

Do not put exploit details or credentials in a public issue. Use GitHub's
[private vulnerability reporting](https://github.com/moldable-ai/gadget-sdk/security/advisories/new)
on the canonical repository to contact the maintainers privately. There is no
response-time or supported-version guarantee for this preview.
