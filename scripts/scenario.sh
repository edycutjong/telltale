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
set -euo pipefail
cd "$(dirname "$0")/.."
CLS=${1:?class}; AT=${2:-12.221}; SETTLE=${3:-12}
RID="${TT_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
EV="${TT_EVROOT:-evidence/runs}/$RID-$CLS"; mkdir -p "$EV"
G=scripts/guest.sh
# per-run monitor config: the deployed telltale.conf plus TT_CONF_EXTRA (tt_up.sh uses telltale-run.conf)
$G sudo "cd /tmp/tt; cp telltale.conf telltale-run.conf; printf '%b\\n' '${TT_CONF_EXTRA:-}' >> telltale-run.conf"
[ "$CLS" = starve ] && TT_CPU=${TT_CPU:-3}
$G sudo "sh /tmp/tt/tt_up.sh trace-hero.csv ${TT_HOLD:-8} ${TT_POLICY:-0} ${TT_CPU:--} ${TT_SCOPE:-all} ${TT_SUP:-script}" > "$EV/up.txt" 2>&1
HP=${TT_HOG_PRIO:-40}
if [ "$CLS" = starve ]; then
  HT=; [ "${TT_POLICY:-0}" = 1 ] && HT="env TT_TYPE=hog_t"
  $G sudo "cd /tmp/tt; ($HT ./hog ${TT_HOG_MS:-3000} $HP $TT_CPU --at $AT > log/hog.log 2>&1 &)
    while ! grep -q t_hog_start log/hog.log 2>/dev/null; do sleep 0.05; done; sleep 0.3
    for i in 1 2 3; do echo \"--- snapshot \$i\"; pidin -F '%a %b %N %p %J %B' | grep -E 'screen|telltale|cluster|hog' | grep -v grep; sleep 0.4; done" > "$EV/g5-pidin.txt" 2>&1
  $G get /tmp/tt/log/hog.log "$EV/" 2>/dev/null || true
elif [ "$CLS" != none ]; then
  $G sudo "cd /tmp/tt; ./ttinject ${TT_INJECT:-$CLS} --at $AT --run $(date +%s)" > "$EV/inject.txt" 2>&1
fi
if [ -n "${TT_HEAL_AT:-}" ]; then
  $G sudo "cd /tmp/tt; ./ttinject heal --at $TT_HEAL_AT" > "$EV/heal.txt" 2>&1
fi
sleep "$SETTLE"
$G sudo 'cd /tmp/tt; ./ttctl status; echo; cat /dev/telltale-events; echo; cat /dev/vehicle' > "$EV/status.txt" 2>&1
$G sudo 'pidin -F "%a %b %N %p %J %B" | grep -E "vehicled|vehfeed|telltale|cluster|hog|slm" | grep -v grep' > "$EV/pidin.txt" 2>&1 || true
for f in telltale cluster vehicled vehfeed super; do $G get "/tmp/tt/log/$f.log" "$EV/" 2>/dev/null || true; done
$G get /tmp/tt/log/checks.csv "$EV/" 2>/dev/null || true
$G get '/tmp/tt/ev/*.csv' "$EV/" 2>/dev/null || true
echo "run $RID class=$CLS inject=${TT_INJECT:-$CLS} heal_at=${TT_HEAL_AT:--} conf_extra=${TT_CONF_EXTRA:--} at=$AT accel=QEMU(TCG) policy=${TT_POLICY:-0} cpu=${TT_CPU:-any} scope=${TT_SCOPE:-all} sup=${TT_SUP:-script}$([ "$CLS" = starve ] && echo " hog_prio=$HP hog_ms=${TT_HOG_MS:-3000}")" > "$EV/RUN.txt"
cat "$EV/inject.txt" 2>/dev/null; grep -E 'DETECT|cause|SIGKILL|INCIDENT|->|gone|safe' "$EV/telltale.log" || true
