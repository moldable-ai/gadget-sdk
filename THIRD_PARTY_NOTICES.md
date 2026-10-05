# Third-party notices

Original SDK code and documentation are licensed under MIT. This checkout does
not vendor third-party firmware, hardware drivers or artwork. Runtime and build
dependencies are installed from their own distributions and retain their licenses.

| Direct runtime dependency | Purpose | License |
| --- | --- | --- |
| cryptography | Ed25519, HKDF-SHA256 and AES-GCM | Apache-2.0 OR BSD-3-Clause |
| httpx | Pairing and refresh HTTP client | BSD-3-Clause |
| websockets | WebSocket transport | BSD-3-Clause |
| aiortc (optional voice extra) | WebRTC data channel and media transport | BSD-3-Clause |

The lockfile records resolved dependencies, including transitive and development
packages. Their distributions contain the authoritative notices. A future bundled
binary/firmware release must include the notices for all code it redistributes.
The SDK license does not grant rights to third-party names, marks or service access.

## Embedded-device wire compatibility

The independent Python implementation in `src/moldable_gadget/device/` follows
the public [Hermes Gadget Protocol v1](https://github.com/Adolanium/hermes-gadget-sdk/blob/323e3303ab68981f810fc3208119cd8a22e64af0/docs/protocol.md),
from Hermes Gadget SDK revision `323e3303ab68981f810fc3208119cd8a22e64af0`.
The wire names `hermes-gadget.v1` and `hermes-gadget/v1` are compatibility
identifiers. No upstream implementation or artwork is copied into these modules.
Upstream firmware is a separate installation with its own license and notices.

The optional voice extra installs PyAV and its FFmpeg-linked distribution,
pylibsrtp, and their transitive dependencies. Their installed wheels carry
additional codec/library license notices; include those notices when bundling
a runtime.
