#!/usr/bin/env bash
# One recorded fault run on the QNX target (QEMU): start the stack on the hero drive, fire one fault at a
# trace time, let detection + recovery play out, collect the raw logs into evidence/runs/<run-id>-<class>/.
#   scripts/scenario.sh <stale|freeze|blank|misdraw|crash|none> [at_trace_s] [settle_s]
# Env: TT_POLICY=1 runs every process in its secpol type; TT_HOLD (default 8) steady seconds before the drive.
set -euo pipefail
cd "$(dirname "$0")/.."
CLS=${1:?class}; AT=${2:-12.221}; SETTLE=${3:-12}
RID="${TT_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
EV="evidence/runs/$RID-$CLS"; mkdir -p "$EV"
G=scripts/guest.sh
$G sudo "sh /tmp/tt/tt_up.sh trace-hero.csv ${TT_HOLD:-8} ${TT_POLICY:-0}" > "$EV/up.txt" 2>&1
if [ "$CLS" != none ]; then
  $G sudo "cd /tmp/tt; ./ttinject $CLS --at $AT --run $(date +%s)" > "$EV/inject.txt" 2>&1
fi
sleep "$SETTLE"
$G sudo 'cd /tmp/tt; ./ttctl status; echo; cat /dev/telltale-events; echo; cat /dev/vehicle' > "$EV/status.txt" 2>&1
$G sudo 'pidin -F "%a %b %N %p %J %B" | grep -E "vehicled|vehfeed|telltale|cluster|hog" | grep -v grep' > "$EV/pidin.txt" 2>&1 || true
for f in telltale cluster vehicled vehfeed super; do $G get "/tmp/tt/log/$f.log" "$EV/" 2>/dev/null || true; done
$G get /tmp/tt/log/checks.csv "$EV/" 2>/dev/null || true
$G get '/tmp/tt/ev/*.csv' "$EV/" 2>/dev/null || true
echo "run $RID class=$CLS at=$AT accel=QEMU(TCG) policy=${TT_POLICY:-0}" > "$EV/RUN.txt"
cat "$EV/inject.txt" 2>/dev/null; grep -E 'DETECT|cause|SIGKILL|INCIDENT|->|gone' "$EV/telltale.log" || true
