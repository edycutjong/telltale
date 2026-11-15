#!/usr/bin/env bash
# Start Screen on the QNX guest with the image's software virtual display, if it is not already running,
# and print which config the running Screen uses (so a re-run never silently reuses another setup).
#   scripts/guest_screen.sh [config] [cpu|any]   with a 2nd argument Screen is (re)started: pinned to CPU <cpu>
#                                                (`on -C`, for the one-CPU STARVE runs) or unpinned (any); default /usr/share/screen/graphics-virtual-display.conf (1280x768 rgba8888, sw blit)
# Why not the image's graphics-virtio-virgl.conf: it needs a host QEMU with virglrenderer (virtio-vga-gl),
# which Homebrew QEMU on macOS does not have. Screen still composes all windows into the virtual display.
set -euo pipefail
cd "$(dirname "$0")/.."
CONF="${1:-/usr/share/screen/graphics-virtual-display.conf}"
if [ -n "${2:-}" ]; then
  PIN=; [ "$2" != any ] && PIN="on -C $2"
  scripts/guest.sh sudo "touch /tmp/tt/stop 2>/dev/null; slay -f -Q telltale cluster vehfeed vehicled hog screen >/dev/null 2>&1; sleep 2
(${PIN} screen -c $CONF >/dev/null 2>&1 &); waitfor /dev/screen/ 15; pidin -p screen -F '%a %N %i' | head -3"
fi
scripts/guest.sh sudo "ls /dev/screen >/dev/null 2>&1 || { (screen -c $CONF >/dev/null 2>&1 &); waitfor /dev/screen/ 15; }
pidin arg | grep 'screen -c' | grep -v grep | sed 's/^ */screen running: pid /'
ls /dev/screen >/dev/null 2>&1 && echo screen-ready"
