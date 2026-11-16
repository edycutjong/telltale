# Security policy

Telltale is a research/hackathon monitor that runs on QNX 8.0 (QEMU guest). It is not certified
for any safety function and must not be used as one.

## Reporting a vulnerability
Use GitHub's private vulnerability reporting (Security tab → "Report a vulnerability"). Please do
not open a public issue for a suspected vulnerability. Expect an acknowledgement within a week.

## Scope
- The monitor, resource managers and secpol policy in `src/` and `secpol/`.
- Host tooling in `tools/` and `scripts/`.

Out of scope: the QNX SDP, QEMU and third-party code under `third_party/` (report those upstream).
