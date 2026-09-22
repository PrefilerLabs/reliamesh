# Security policy

ReliaMesh is maintained by Prefiler Labs Private Limited. The 0.1.x release line
receives security fixes while it is the latest stable line. Upgrade promptly when
a security release is announced. There is no stated response-time SLA.

Do not post credentials, customer data, exploit payloads containing real data, or
unredacted operational logs in public issues. Use the repository's [private
vulnerability reporting](https://github.com/PrefilerLabs/reliamesh/security/advisories/new)
feature if enabled. If that channel is
unavailable, open an issue containing only a request for a private maintainer
contact, without vulnerability details. A dedicated public security mailbox is
not claimed until the owner has provisioned and verified one.

A useful private report includes affected version, impact, minimal synthetic
reproduction, and mitigation ideas. Use only systems you own or have explicit
permission to test. Do not access another tenant's data or disrupt the hosted
service to demonstrate an issue.

Operators should revoke exposed keys, restrict affected endpoints, preserve
sanitized evidence, and restore access only after verification. Rotate application
keys through the key API; a read-only key cannot create a broader credential.
Protect admin hashes, deployment identities, backups, and repository/release
credentials separately. See [the threat model](docs/threat-model.md).
