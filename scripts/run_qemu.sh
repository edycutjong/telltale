#!/usr/bin/env bash
# Boot the QNX 8.0 target: the QNX Everywhere Quick Start Target Image (QSTI) for QEMU, x86_64 guest.
#
# Adapted from the image's documented launch (`mkqnximage --run`, which uses
#   -smp 8 -vga none -device virtio-vga-gl -display sdl,gl=on  + a Linux bridge + KVM).
# Changes for a macOS host, and why:
#   - accelerator: TCG (no KVM on macOS; HVF cannot run an x86_64 guest on Apple Silicon). ALL timing
#     measured on this target is labelled "QEMU (TCG)" and is not hardware timing.
#   - display: -display none. Homebrew QEMU on macOS has no virglrenderer (no virtio-vga-gl), so Screen
#     cannot use the image's graphics-virtio-virgl.conf. Telltale starts Screen on the image's own
#     software "virtual display" config instead (scripts/guest_screen.sh); composition happens in Screen.
#   - network: user-mode with host forwards (no bridge, no root): ssh -> 127.0.0.1:2222, VNC -> :5901.
#   - disk: the QSTI ships a 47 GB raw disk; we convert it once to sparse qcow2 (~5 GB, identical bytes,
#     checked with `qemu-img compare`).
#   - serial console on a unix socket (+ log file) so scripts can drive it headless.
#
# Usage: scripts/run_qemu.sh [extra qemu args]       (runs in the foreground; use & or nohup)
# Env:   QSTI_DIR   unpacked QSTI runtime dir (default $HOME/qnx800/images/qemu/qemu)
#        TT_RUN     runtime dir for sockets/logs (default /tmp/tt-qemu)
#        TT_SMP     guest CPUs (default 4)   TT_RAM (default 3G)
set -euo pipefail
QSTI_DIR="${QSTI_DIR:-$HOME/qnx800/images/qemu/qemu}"
RUN="${TT_RUN:-/tmp/tt-qemu}"
O="$QSTI_DIR/output"
mkdir -p "$RUN"

if [ ! -f "$O/disk-qemu.qcow2" ]; then
  [ -f "$O/disk-qemu" ] || { echo "run_qemu.sh: no QSTI disk in $O (install com.qnx.qnx800.quickstart.qemu, run unpack_qemu_image.sh)" >&2; exit 1; }
  echo "converting raw QSTI disk to qcow2 (one time)..."
  qemu-img convert -O qcow2 "$O/disk-qemu" "$O/disk-qemu.qcow2"
  qemu-img compare "$O/disk-qemu" "$O/disk-qemu.qcow2"
fi

rm -f "$RUN/serial.sock" "$RUN/mon.sock"
echo "QEMU (TCG) x86_64, QSTI $(basename "$(dirname "$QSTI_DIR")")/$(basename "$QSTI_DIR"), serial log $RUN/serial.log"
exec qemu-system-x86_64 -accel tcg,thread=multi -cpu max -smp "${TT_SMP:-4}" -m "${TT_RAM:-3G}" \
  -drive file="$O/disk-qemu.qcow2",if=ide,id=drv0 -kernel "$O/ifs.bin" \
  -chardev socket,id=s0,path="$RUN/serial.sock",server=on,wait=off,logfile="$RUN/serial.log" -serial chardev:s0 \
  -netdev user,id=net0,hostfwd=tcp:127.0.0.1:2222-:22,hostfwd=tcp:127.0.0.1:5901-:5900 -device virtio-net-pci,netdev=net0 \
  -object rng-random,filename=/dev/urandom,id=rng0 -device virtio-rng-pci,rng=rng0 \
  -vga std -display none -monitor unix:"$RUN/mon.sock",server,nowait -pidfile "$RUN/qemu.pid" "$@"
