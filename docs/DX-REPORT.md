# Developer-experience log

Friction met while setting up and building Telltale for QNX SDP 8.0, written as it happens.

## 2026-11-14: host setup on an Apple-Silicon Mac
- The build machine is an M1 Mac. The plan is QNX's documented macOS route: SDP host tools in a `linux/amd64` Docker
  container (https://devblog.qnx.com/how-to-use-macos-to-develop-for-qnx-8-0/), and an x86_64 QEMU guest.
- Getting the SDP needs a myQNX account and a QNX Everywhere licence first; the host-side, QNX-independent parts
  (bench analysis, rounding rule, CI) were started while that is pending.
