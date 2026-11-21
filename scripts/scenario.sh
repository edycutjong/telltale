#!/usr/bin/env bash
# One recorded fault run on the QNX target (QEMU): start the stack on the hero drive, fire one fault at a
# trace time, let detection + recovery play out, collect the raw logs into evidence/runs/<run-id>-<class>/.
#   scripts/scenario.sh <stale|freeze|blank|misdraw|crash|lampdrop|starve|none> [at_trace_s] [settle_s]
# Env: TT_POLICY=1 runs every process in its secpol type; TT_HOLD (default 8) steady seconds before the drive;
#      TT_CPU=N pins the stack to CPU N (Screen must already be pinned there: scripts/guest_screen.sh '' N);
#      TT_SUP=slm restarts the renderer with slm (scripts/target/telltale-slm.xml) instead of the shell loop;
#      TT_SCOPE=renderer pins only the cluster (+ the hog) to TT_CPU: "STARVE (renderer CPU only)";
#      starve: TT_HOG_MS (default 3000), TT_HOG_PRIO (default 40) -- hog on TT_CPU (default 3), no bug planted;
#      pidin snapshots of Screen + monitor threads during the hog go to g5-pidin.txt (gate G5).
#      TT_EVROOT (default evidence/runs) where the run dir goes; TT_CONF_EXTRA extra telltale.conf lines for this
#      run (e.g. "mismatch_n=1000"); TT_HEAL_AT=S sends `ttinject heal` when the trace reaches S seconds.
#      TT_TRACE=path.csv replays that trace (t_s,speed_mps; copied to the guest) instead of data/trace-hero.csv
#      (the bench cuts one slice per injection); TT_NOCOLLECT=1 skips the pidin/status/log copy-back extras.
#      TT_DISARM=1 runs `ttctl disarm` before the fault (observe-only monitor: the B2 ablation).
#      TT_DEBUG=1 turns on engineering mode (`ttctl debug on`) and captures two display frames with ttshot (a separate
#      process reading what Screen composed): banner.png once the engineering banner is up, card.png once the incident
#      card is shown, card-hidden.png once it has been hidden again. TT_DEBUG=2: engineering mode only (no ttshot frames).
#      TT_REC=S records the composed display for up to S seconds (stopped at collection) with ttrec (a separate low-priority process, `on -p 9`,
#      screen_read_display every TT_REC_MS=33 ms of the regions in REC_REGIONS or TT_REC_ARGS, changed tiles only, into RAM) -> rec.ttv + rec.txt in TT_REC_DIR
#      (default the run dir); tools/ttv_decode.py turns it into PNG frames. Demo-video footage, not a bench input.
set -euo pipefail
cd "$(dirname "$0")/.."
CLS=${1:?class}; AT=${2:-12.221}; SETTLE=${3:-12}
RID="${TT_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
EV="${TT_EVROOT:-evidence/runs}/$RID-$CLS"; mkdir -p "$EV"
G=scripts/guest.sh
TRACE=trace-hero.csv
if [ -n "${TT_TRACE:-}" ]; then $G put "$TT_TRACE"; TRACE=$(basename "$TT_TRACE"); fi
# per-run monitor config: the deployed telltale.conf plus TT_CONF_EXTRA (tt_up.sh uses telltale-run.conf)
$G sudo "cd /tmp/tt; cp telltale.conf telltale-run.conf; printf '%b\\n' '${TT_CONF_EXTRA:-}' >> telltale-run.conf"
[ "$CLS" = starve ] && TT_CPU=${TT_CPU:-3}
$G sudo "sh /tmp/tt/tt_up.sh $TRACE ${TT_HOLD:-8} ${TT_POLICY:-0} ${TT_CPU:--} ${TT_SCOPE:-all} ${TT_SUP:-script}" > "$EV/up.txt" 2>&1
HP=${TT_HOG_PRIO:-40}
# with the policy loaded the control and fault tools run in their own type (injector_t: telltale:control, cluster:fault)
IJ=; [ "${TT_POLICY:-0}" = 1 ] && IJ="env TT_TYPE=injector_t"
[ "${TT_DISARM:-0}" = 1 ] && $G sudo "cd /tmp/tt; $IJ ./ttctl disarm" > "$EV/disarm.txt" 2>&1
if [ "${TT_DEBUG:-0}" = 2 ]; then   # engineering view only: no ttshot watcher loops (their grep-every-50-ms polling loads
  $G sudo "cd /tmp/tt; $IJ ./ttctl debug on" > "$EV/debug.txt" 2>&1   # the QEMU guest; ttrec films the display instead)
elif [ "${TT_DEBUG:-0}" = 1 ]; then
  $G sudo "cd /tmp/tt; rm -f log/*.ppm log/shot-*.txt; $IJ ./ttctl debug on
    ( (n=0; while ! grep -q 'mode=engineering' log/telltale.log && [ \$n -lt 600 ]; do sleep 0.05; n=\$((n+1)); done; sleep 0.3; ./ttshot log/banner.ppm) > log/shot-banner.txt 2>&1 < /dev/null & )
    ( (n=0; while ! grep -q 'CARD shown' log/telltale.log && [ \$n -lt 900 ]; do sleep 0.05; n=\$((n+1)); done; sleep 0.5; ./ttshot log/card.ppm) > log/shot-card.txt 2>&1 < /dev/null & )
    ( (n=0; while ! grep -q 'CARD hidden' log/telltale.log && [ \$n -lt 1200 ]; do sleep 0.05; n=\$((n+1)); done; sleep 0.3; ./ttshot log/card-hidden.ppm) > log/shot-card-hidden.txt 2>&1 < /dev/null & )" > "$EV/debug.txt" 2>&1
fi
# where the cluster can change (digits + safe-layer marker, tick, lamps, incident card, engineering banner): read only these,
# the whole display every 150 frames -- keeps the recorder from perturbing the run it films (src/tools/ttrec.c)
REC_REGIONS="150 544,192,352,288 544,480,224,96 64,64,1152,64@2 864,128,416,224@3 0,608,1280,96@3"
if [ -n "${TT_REC:-}" ]; then
  $G sudo "cd /tmp/tt; rm -f /dev/shmem/tt-rec.ttv log/rec.txt; (on -p 9 ./ttrec /dev/shmem/tt-rec.ttv ${TT_REC_MS:-33} $TT_REC ${TT_REC_ARGS:-$REC_REGIONS} > log/rec.txt 2>&1 < /dev/null &)" > /dev/null 2>&1
fi
if [ "$CLS" = starve ]; then
  HT=; [ "${TT_POLICY:-0}" = 1 ] && HT="env TT_TYPE=hog_t"
  $G sudo "cd /tmp/tt; ($HT ./hog ${TT_HOG_MS:-3000} $HP $TT_CPU --at $AT > log/hog.log 2>&1 &)
    while ! grep -q t_hog_start log/hog.log 2>/dev/null; do sleep 0.05; done; sleep 0.3
    for i in 1 2 3; do echo \"--- snapshot \$i\"; pidin -F '%a %b %N %p %J %B' | grep -E 'screen|telltale|cluster|hog' | grep -v grep; sleep 0.4; done" > "$EV/g5-pidin.txt" 2>&1
  $G get /tmp/tt/log/hog.log "$EV/" 2>/dev/null || true
elif [ "$CLS" != none ]; then
  $G sudo "cd /tmp/tt; $IJ ./ttinject ${TT_INJECT:-$CLS} --at $AT --run $(date +%s)" > "$EV/inject.txt" 2>&1
fi
if [ -n "${TT_HEAL_AT:-}" ]; then
  $G sudo "cd /tmp/tt; $IJ ./ttinject heal --at $TT_HEAL_AT" > "$EV/heal.txt" 2>&1
fi
sleep "$SETTLE"
$G sudo 'cd /tmp/tt; ./ttctl status; echo; cat /dev/telltale-events; echo; cat /dev/vehicle' > "$EV/status.txt" 2>&1
if [ "${TT_NOCOLLECT:-0}" = 1 ]; then   # bench: only the logs the row extractor and a reader need
  for f in telltale cluster; do $G get "/tmp/tt/log/$f.log" "$EV/" 2>/dev/null || true; done
else
$G sudo 'pidin -F "%a %b %N %p %J %B" | grep -E "vehicled|vehfeed|telltale|cluster|hog|slm" | grep -v grep' > "$EV/pidin.txt" 2>&1 || true
for f in telltale cluster vehicled vehfeed super; do $G get "/tmp/tt/log/$f.log" "$EV/" 2>/dev/null || true; done
$G get /tmp/tt/log/checks.csv "$EV/" 2>/dev/null || true
fi
if [ "${TT_DEBUG:-0}" = 1 ]; then
  for f in banner card card-hidden; do
    $G get "/tmp/tt/log/shot-$f.txt" "$EV/" 2>/dev/null || true
    $G get "/tmp/tt/log/$f.ppm" "$EV/" 2>/dev/null && python3 -c 'import sys; from PIL import Image; Image.open(sys.argv[1]).save(sys.argv[2], optimize=True)' "$EV/$f.ppm" "$EV/$f.png" && rm -f "$EV/$f.ppm"
  done
fi
$G get '/tmp/tt/ev/*.csv' "$EV/" 2>/dev/null || true
if [ -n "${TT_REC:-}" ]; then
  RD="${TT_REC_DIR:-$EV}"; mkdir -p "$RD"
  $G sudo 'slay -f -Q ttrec >/dev/null 2>&1; n=0; while pidin arg | grep -q "[t]trec" && [ $n -lt 40 ]; do sleep 0.25; n=$((n+1)); done; true'
  $G get /dev/shmem/tt-rec.ttv "$RD/rec.ttv" && $G get /tmp/tt/log/rec.txt "$RD/rec.txt" || echo "scenario: no ttrec recording" >&2
fi
KPOL=$($G sudo 'secpol -Q | head -1' 2>/dev/null | tr ' ' '_' || true)   # the kernel's policy state, not just the requested mode
echo "run $RID class=$CLS inject=${TT_INJECT:-$CLS} heal_at=${TT_HEAL_AT:--} conf_extra=${TT_CONF_EXTRA:--} at=$AT accel=QEMU(TCG) policy=${TT_POLICY:-0} debug=${TT_DEBUG:-0} cpu=${TT_CPU:-any} scope=${TT_SCOPE:-all} sup=${TT_SUP:-script} kernel_policy=${KPOL:-unknown}$([ "$CLS" = starve ] && echo " hog_prio=$HP hog_ms=${TT_HOG_MS:-3000}")" > "$EV/RUN.txt"
cat "$EV/inject.txt" 2>/dev/null || true   # STARVE runs have no inject.txt (set -e would exit 1 here)
grep -E 'DETECT|cause|SIGKILL|INCIDENT|->|gone|safe' "$EV/telltale.log" || true
