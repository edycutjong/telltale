#!/bin/sh
# On-target checks of the Core items the fault runs do not assert (run as root from /tmp/tt on the QNX guest, no policy):
# cat /dev/vehicle, ttctl status|disarm|arm|period|rearm|debug, cat /dev/telltale + /dev/telltale-events,
# TRUTH_LOST (the feeder dies -> truth lost -> the feeder returns -> hand-back) and NO_DISPLAY (no renderer comes back).
# Starts its own stack on the hero drive. Each line: PASS|FAIL <what>. Last line: core: <n>/<m> passed.
cd /tmp/tt || exit 1
P=0; N=0
ok()  { N=$((N+1)); P=$((P+1)); echo "PASS $*"; }
bad() { N=$((N+1)); echo "FAIL $*"; }
chk() { if eval "$2"; then ok "$1"; else bad "$1 (got: $(echo "$3" | head -2 | tr '\n' ' '))"; fi; }
st()  { cat /dev/telltale | head -1; }
waitst() { n=0; while ! st | grep -q "state=$1 " && [ $n -lt $2 ]; do sleep 0.1; n=$((n+1)); done; st | grep -q "state=$1 "; }
cp telltale.conf telltale-run.conf
sh tt_up.sh trace-hero.csv 30 0 - all script > log/core-up.txt 2>&1
waitst NORMAL 150; S=$(st)
chk "monitor armed on the parked car (state NORMAL)" 'echo "$S" | grep -q "armed=1 state=NORMAL"' "$S"
V=$(cat /dev/vehicle)
chk "cat /dev/vehicle prints seq, speed, gear and lamps" 'echo "$V" | grep -Eq "seq=[1-9][0-9]* .*speed_kmh=[0-9.]+ gear=. lamps=0x[0-9a-f]{2}"' "$V"
O=$(./ttctl status 2>&1)
chk "ttctl status = the /dev/telltale status line" 'echo "$O" | grep -q "period_ms=20 "' "$O"
./ttctl disarm > /dev/null 2>&1; S=$(st)
chk "ttctl disarm -> armed=0" 'echo "$S" | grep -q "armed=0 "' "$S"
./ttctl arm > /dev/null 2>&1; S=$(st)
chk "ttctl arm -> armed=1" 'echo "$S" | grep -q "armed=1 "' "$S"
./ttctl period 40 > /dev/null 2>&1; sleep 0.3; S=$(st)
chk "ttctl period 40 -> period_ms=40 (the knob)" 'echo "$S" | grep -q "period_ms=40 "' "$S"
if ./ttctl period 3 > /dev/null 2>&1; then bad "ttctl period 3 refused (EINVAL, range 5..1000)"; else ok "ttctl period 3 refused (EINVAL, range 5..1000)"; fi
./ttctl period 20 > /dev/null 2>&1; sleep 0.3; S=$(st)
chk "ttctl period 20 -> period_ms=20" 'echo "$S" | grep -q "period_ms=20 "' "$S"
if ./ttctl rearm > /dev/null 2>&1 && ./ttctl debug off > /dev/null 2>&1; then ok "ttctl rearm + debug off accepted"; else bad "ttctl rearm + debug off accepted"; fi
E=$(cat /dev/telltale-events | head -1)
chk "cat /dev/telltale-events starts with the incident CSV header" 'echo "$E" | grep -q "^id,verdict,cause,shown,truth,"' "$E"
# TRUTH_LOST: the feeder dies; the truth goes stale; the monitor opens TRUTH_LOST and shows dashes on the safe layer
slay -f -Q vehfeed > /dev/null 2>&1
waitst TRUTH-LOST 50; S=$(st)
chk "feeder killed -> state TRUTH-LOST" 'echo "$S" | grep -q "state=TRUTH-LOST "' "$S"
SC=; [ -f scenario-hero.csv ] && SC="--scenario scenario-hero.csv"
./vehfeed trace-hero.csv --hold 30 $SC > log/vehfeed2.log 2>&1 < /dev/null &
waitst NORMAL 100; S=$(st)
chk "feeder back -> verified hand-back, state NORMAL" 'echo "$S" | grep -q "state=NORMAL "' "$S"
L=$(cat /dev/telltale | tail -1)
chk "TRUTH_LOST incident closed: last=TT-TRL-* verdict=TRUTH_LOST recovered=yes in /dev/telltale" 'echo "$L" | grep -Eq "last=TT-TRL-[0-9]+ verdict=TRUTH_LOST .*recovered=yes"' "$L"
chk "the TRUTH_LOST row is in /dev/telltale-events" 'cat /dev/telltale-events | grep -q ",TRUTH_LOST,"' "$(cat /dev/telltale-events | tail -1)"
S=$(st)
chk "period knob kept the configured tick_stall_ms=150 (not the 57 ms rule floor)" 'echo "$S" | grep -q "tick_stall_ms=150 "' "$S"
# NO_DISPLAY: stop the respawn loop, kill the renderer; nothing registers within restart_timeout_ms
touch stop; slay -f -Q cluster > /dev/null 2>&1
waitst NO-DISPLAY 120; S=$(st)
chk "renderer gone and not restarted -> state NO-DISPLAY (safe layer keeps the truth up)" 'echo "$S" | grep -q "state=NO-DISPLAY "' "$S"
grep -E "TRUTH|NO_DISPLAY|NO-DISPLAY|restart timeout|DETECT|INCIDENT|->" log/telltale.log > log/core-telltale.txt 2>/dev/null
rm -f stop
echo "core: $P/$N passed"
