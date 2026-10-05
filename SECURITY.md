# Security

This is a developer preview for trusted devices. Pairing grants existing
Moldable remote-controller access; the Python API's small method set is not a
server permission boundary. The current host shares a room key and Remote
workspace selection across controllers. Narrow gadget grants, per-device content-encryption keys
and device-specific presentation remain planned host work.

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
locally. Local deletion alone does not revoke server-side access. No shell,
arbitrary file access, firmware flashing or hardware actuator API is enabled.

## Reporting

Do not put exploit details or credentials in a public issue. Use GitHub's
[private vulnerability reporting](https://github.com/moldable-ai/moldable-gadget-sdk/security/advisories/new)
on the canonical repository to contact the maintainers privately. There is no
response-time or supported-version guarantee for this preview.
