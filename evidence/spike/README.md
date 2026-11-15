# Gate spikes on the QNX 8.0 target

Before building the monitor, four things had to be proven on a real QNX 8.0 target. Each one is a small C program
in [`spike/`](../../spike), built with `qcc` (`make target`) and run on the target by [`scripts/spike.sh`](../../scripts/spike.sh)
(`make spike`). One run directory holds the raw output, the read-back images and a generated `SUMMARY.md`.

**Recorded run:** [`20261115T1216Z/SUMMARY.md`](20261115T1216Z/SUMMARY.md), from a fresh boot. All gates PASS.
An earlier run on another boot, made with the previous version of the scripts (commit 4bc9e72), had the same G1,
S-OCC, S-UID and G3 connect results, a timer p95 of 4.60 ms and a 3-rect check p95 of 5.85 ms. It is in the git history.

**Target:** the QNX Everywhere Quick Start Target Image for QEMU (QNX 8.0.0, x86_64 guest), launched by
[`scripts/run_qemu.sh`](../../scripts/run_qemu.sh) on an Apple-Silicon Mac. **Every time below is QEMU (TCG) time:
x86_64 emulated on an arm64 host. It is not hardware timing.**

| gate | question | how it was tested | result (run 20261115T1216Z) |
|---|---|---|---|
| G2 | do `qcc` binaries run on the target? | `spike/g2_hello.c` prints `uname` and `num_cpu` from the syspage | runs: `QNX 8.0.0 … x86pc, cpu=4`. The guest boots headless; see the first limit below |
| G1 | can a second process read back the pixels that Screen actually composed? | `g1_readback render` (uid 1000) draws a known 400×200 pattern: four colour bars (red, green, blue, white), a 7-segment digit, and a 16-step blue ladder whose steps are 1 apart. `g1_readback read` (root, window-manager + display-manager context) reads it back with `screen_read_display` (route 1, the composed display) and `screen_read_window` (route 2, the window), then compares every pixel | both routes **byte-exact**: 0 of 80,000 px differ |
| G1 (composition) | is route 1 the composed output, not just one window? | a top-z window covers the right half, then the same read runs again | display read: **40,000 px differ, exactly the covered half**. Window read: still exact (S-OCC) |
| S-UID | can a non-root process read the window? | the renderer grants read access with `SCREEN_PROPERTY_PERMISSIONS`; readers use an application context | uid 1000 and a different uid, 1001, both read the window **byte-exact**. Without root, the display read and a window-manager context are refused (EACCES) |
| G4 | is the check fast and regular enough? | 5,000 pulses from a 20 ms `timer_create(CLOCK_MONOTONIC, SIGEV_PULSE)` timer at SCHED_FIFO 60; 500 checks of 3 `screen_read_display` rects (1280×64, 360×336, 8×8) | timer error p95 **4.66 ms** (bar ≤ 5). One 3-rect check p95 **3.66 ms** (bar ≤ 10) |
| G3 (minimal) | does secpol express the monitor's least privilege? | [`spike/g3_spike.pol`](../../spike/g3_spike.pol), compiled with `secpolcompile` and pushed at runtime with `secpolpush`. Three types (`monitor_t`, `vehicle_t`, `render_t`), each process run as uid 1000, plus an untyped control server run as root | `monitor_t` **may** connect to `vehicle_t` and is **denied** (EPERM) on `render_t`. An untyped process with the same uid is denied on both typed channels. The policy grants each type `setuid/setgid:1000` only: a `monitor_t` drop to uid 1001 is **refused** (EPERM) |

## What these spikes do not show yet

- **The guest has no visible display, and Screen runs on a software "virtual display".** Homebrew QEMU on macOS has
  no virglrenderer, so the image's virtio-gpu config (`graphics-virtio-virgl.conf`) cannot start. QEMU runs with
  `-display none`, and Telltale starts Screen with the image's own `graphics-virtual-display.conf`
  ([`scripts/guest_screen.sh`](../../scripts/guest_screen.sh)). Screen still composes every window into that display,
  and route 1 reads that composed output. No virtio-gpu scanout is involved, the image's welcome screen never
  appears, and no host-side framebuffer capture was compared with the read-back.
- **The policy is pushed after boot**, from `/tmp`, by `secpolpush`. Loading it from the boot image comes with the
  monitor itself, and so does the rest of the G3 list: resource-manager write denial, signal rules, and the
  scheduling policy observed per process.
- **Timing is QEMU (TCG).** The timer runs on the image's 1 ms tick. Asking `ClockPeriod()` for 100 µs returns
  "Not supported" on this image (see `run.log`, step "G4 knob"). The p95 is 4.66 ms against a 5 ms bar, so the margin
  is small. The bench reports the measured timer error with every run.

## Reproduce

```sh
docker build --platform linux/amd64 -t telltale-qnx-host:local docker   # host tools image; the SDP stays on the host
make target                                      # qcc via scripts/qcc.sh -> out/x86_64/
scripts/run_qemu.sh &                            # boots the QSTI (needs com.qnx.qnx800.quickstart.qemu installed + unpacked)
scripts/guest.sh key && tools/qnx_serial.py install-key ~/.ssh/telltale_qnx.pub
scripts/guest.sh wait && make spike              # -> evidence/spike/<run-id>/SUMMARY.md
```

`secpol -Q` reports a pushed policy as `(immutable)`, so `spike.sh` pushes only when no policy is loaded yet. For a
run that includes the push, reboot the guest first.
