#!/usr/bin/env bash
# P1a gate spikes on the running QNX target (scripts/run_qemu.sh): G2 run, G1 read-back, G4 timing, G3 secpol.
# Writes raw outputs to evidence/spike/<run-id>/ and a summary via tools/spike_report.py.
# Prereqs: `make target` (binaries in out/x86_64), guest booted, ssh key installed (tools/qnx_serial.py install-key).
set -euo pipefail
cd "$(dirname "$0")/.."
G=scripts/guest.sh
B=out/${QNX_ARCH:-x86_64}
RID="${TT_RUN_ID:-$(date -u +%Y%m%dT%H%MZ)}"
EV="evidence/spike/$RID"
mkdir -p "$EV"
log() { echo "== $*" | tee -a "$EV/run.log"; }

log "run $RID accel=QEMU(TCG) target=QSTI x86_64"
# a running binary cannot be overwritten: stop leftovers from an earlier run, clear old outputs
$G sudo 'slay -f -Q g1_readback g3_secpol g4_timer >/dev/null 2>&1; rm -f /tmp/tt/g1_readback /tmp/tt/g3_secpol /tmp/tt/g4_timer /tmp/tt/*.ppm /tmp/tt/g4-*.csv; true'
$G put "$B/g1_readback" "$B/g2_hello" "$B/g3_secpol" "$B/g4_timer" "$B/g3_spike.bin" spike/g3_spike.pol
$G run 'uname -a; pidin info | head -3' | tee "$EV/target-uname.txt" | tee -a "$EV/run.log"

log "G2: qcc-built binary runs on the target"
$G run '/tmp/tt/g2_hello' | tee "$EV/g2.txt" | tee -a "$EV/run.log"

log "Screen: software virtual display (graphics-virtual-display.conf), no window manager, no demolauncher"
$G sudo 'ls /dev/screen >/dev/null 2>&1 || { (screen -c /usr/share/screen/graphics-virtual-display.conf >/dev/null 2>&1 &); waitfor /dev/screen/ 15; }; echo screen-ready' | tee -a "$EV/run.log"
$G sudo 'slay -f g1_readback 2>/dev/null; true'

log "G1: renderer (uid 1000) + reader (root, WM|DM context): routes 1 (display) and 2 (window)"
$G run 'cd /tmp/tt; (nohup ./g1_readback render 100 80 > render.log 2>&1 &); sleep 3; cat render.log' | tee -a "$EV/g1.txt"
$G sudo 'cd /tmp/tt; ./g1_readback read 100 80 plain' 2>&1 | tee -a "$EV/g1.txt"
log "G1: top-z cover over the right half (composition proof) + covered-window read (S-OCC)"
$G run 'cd /tmp/tt; (nohup ./g1_readback cover 300 80 200 200 > cover.log 2>&1 &); sleep 3; cat cover.log' | tee -a "$EV/g1.txt"
$G sudo 'cd /tmp/tt; ./g1_readback read 100 80 covered' 2>&1 | tee -a "$EV/g1.txt"
$G run 'slay -f -Q g1_readback >/dev/null 2>&1; true'
log "G1: S-UID - non-root reads with an application context (same uid 1000, other uid 1001)"
$G run 'cd /tmp/tt; (nohup ./g1_readback render 100 80 > render.log 2>&1 &); sleep 3; TT_CTX_APP=1 ./g1_readback read 100 80 uid1000-app' 2>&1 | tee -a "$EV/g1.txt"
$G sudo 'cd /tmp/tt; chmod 777 /tmp/tt; on -u user1 env TT_CTX_APP=1 ./g1_readback read 100 80 uid1001-app' 2>&1 | tee -a "$EV/g1.txt"
$G run 'cd /tmp/tt; ./g1_readback read 100 80 uid1000-wm' 2>&1 | tee -a "$EV/g1.txt" || true

log "G4: 5000 x 20 ms timer pulses (SCHED_FIFO 60) + 500 checks x 3 screen_read_display rects"
$G sudo 'cd /tmp/tt; ./g4_timer 5000 60 > g4-timer.csv 2> g4-timer.txt; ./g1_readback bench 500 > g4-readback.csv 2> g4-readback.txt; cat g4-timer.txt'  | tee -a "$EV/run.log"

log "G4 knob: ask for a 100 us tick (ClockPeriod); record whether the image allows it"
$G sudo 'cd /tmp/tt; ./g4_timer 200 60 100 2>&1 >/dev/null' | tee "$EV/g4-tick-knob.txt" | tee -a "$EV/run.log"

log "G3: runtime secpolpush of spike/g3_spike.pol, typed channel connects under uid 1000"
$G sudo 'cd /tmp/tt; secpol -Q; if secpol -Q | grep -q "policy id:0"; then secpolpush /tmp/tt/g3_spike.bin; fi; secpol -Q' 2>&1 | tee "$EV/g3.txt"
$G sudo 'cd /tmp/tt; slay -f g3_secpol 2>/dev/null; rm -f *.chan
  (./g3_secpol as vehicle_t 1000 server /tmp/tt/v.chan > v.log 2>&1 &)
  (./g3_secpol as render_t 1000 server /tmp/tt/r.chan > r.log 2>&1 &)
  (./g3_secpol server /tmp/tt/d.chan > d.log 2>&1 &)
  sleep 2; cat v.log r.log d.log
  echo "-- monitor_t"; ./g3_secpol as monitor_t 1000 client /tmp/tt/v.chan /tmp/tt/r.chan /tmp/tt/d.chan
  echo "-- render_t";  ./g3_secpol as render_t 1000 client /tmp/tt/v.chan /tmp/tt/r.chan
  echo "-- untyped uid 1000"; on -u qnxuser ./g3_secpol client /tmp/tt/v.chan /tmp/tt/r.chan /tmp/tt/d.chan
  slay -f g3_secpol; true' 2>&1 | tee -a "$EV/g3.txt"

log "collect"
$G get '/tmp/tt/*.ppm' "$EV/"
$G get '/tmp/tt/g4-*.csv' "$EV/"
$G run 'slay -f -Q g1_readback >/dev/null 2>&1; true'
python3 tools/spike_report.py "$EV"
log "done: $EV"
