#!/bin/sh
# On-target checks of the Core items the fault runs do not assert (run as root from /tmp/tt on the QNX guest, no policy):
# cat /dev/vehicle, ttctl status|disarm|arm|period|rearm|debug, cat /dev/telltale + /dev/telltale-events,
# TRUTH_LOST (the feeder dies -> truth lost -> the feeder returns -> hand-back), DISARM in the middle of an incident
# (observe-only in every state), a vehicled restart (the monitor reconnects), NO_DISPLAY (no renderer comes back),
# (a crash after earlier incidents is checked at the end)
# startup without truth (TRUTH-LOST, never WAIT forever), the restart cap (a renderer wrong on every start -> TT-GIVEUP,
# then rearm; one record per incident), a renderer blank from boot (never parked in NO-DISPLAY) and the hidden card (invisible). Starts its own stack on the hero drive. Each line: PASS|FAIL <what>. Last line: core: <n>/<m> passed.
cd /tmp/tt || exit 1
P=0; N=0
ok()  { N=$((N+1)); P=$((P+1)); echo "PASS $*"; }
bad() { N=$((N+1)); echo "FAIL $*"; }
chk() { if eval "$2"; then ok "$1"; else bad "$1 (got: $(echo "$3" | head -2 | tr '\n' ' '))"; fi; }
st()  { cat /dev/telltale | head -1; }
waitst() { n=0; while ! st | grep -q "state=$1 " && [ $n -lt $2 ]; do sleep 0.1; n=$((n+1)); done; st | grep -q "state=$1 "; }
field() { st | sed -n "s/.* $1=\([0-9]*\).*/\1/p"; }
rm -f fault-on-start
cp telltale.conf telltale-run.conf
SC=; [ -f scenario-hero.csv ] && SC="--scenario scenario-hero.csv"
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
if ./ttctl period 3 > /dev/null 2>&1; then bad "ttctl period 3 refused (EINVAL, range 5..100)"; else ok "ttctl period 3 refused (EINVAL, range 5..100)"; fi
if ./ttctl period 500 > /dev/null 2>&1; then bad "ttctl period 500 refused (EINVAL: the 4-bit tick aliases past 266 ms)"; else ok "ttctl period 500 refused (EINVAL: the 4-bit tick aliases past 266 ms)"; fi
./ttctl period 20 > /dev/null 2>&1; sleep 0.3; S=$(st)
chk "ttctl period 20 -> period_ms=20" 'echo "$S" | grep -q "period_ms=20 "' "$S"
if ./ttctl rearm > /dev/null 2>&1 && ./ttctl debug off > /dev/null 2>&1; then ok "ttctl rearm + debug off accepted"; else bad "ttctl rearm + debug off accepted"; fi
E=$(cat /dev/telltale-events | head -1)
chk "cat /dev/telltale-events starts with the incident CSV header" 'echo "$E" | grep -q "^id,verdict,cause,shown,truth,"' "$E"
S=$(st)
chk "status reports capture_errors=0 (every composed read succeeded)" 'echo "$S" | grep -q "capture_errors=0"' "$S"
# TRUTH_LOST: the feeder dies; the truth goes stale; the monitor opens TRUTH_LOST and shows dashes on the safe layer
slay -f -Q vehfeed > /dev/null 2>&1
waitst TRUTH-LOST 50; S=$(st)
chk "feeder killed -> state TRUTH-LOST" 'echo "$S" | grep -q "state=TRUTH-LOST "' "$S"
O=$(cat /dev/telltale | grep '^open=')
chk "cat /dev/telltale shows the incident in progress (open=TT-TRL-*)" 'echo "$O" | grep -q "^open=TT-TRL-"' "$O"
./vehfeed trace-hero.csv --hold 30 $SC > log/vehfeed2.log 2>&1 < /dev/null &
waitst NORMAL 100; S=$(st)
chk "feeder back -> verified hand-back, state NORMAL" 'echo "$S" | grep -q "state=NORMAL "' "$S"
L=$(cat /dev/telltale | tail -1)
chk "TRUTH_LOST incident closed: last=TT-TRL-* verdict=TRUTH_LOST recovered=yes in /dev/telltale" 'echo "$L" | grep -Eq "last=TT-TRL-[0-9]+ verdict=TRUTH_LOST .*recovered=yes"' "$L"
chk "the TRUTH_LOST row is in /dev/telltale-events" 'cat /dev/telltale-events | grep -q ",TRUTH_LOST,"' "$(cat /dev/telltale-events | tail -1)"
S=$(slog2info 2>/dev/null | grep "INCIDENT TT-TRL-" | tail -1)
chk "the TRUTH_LOST incident is in slog2 (slog2info, buffer set telltale)" '[ -n "$S" ]' "$(slog2info 2>&1 | grep -i telltale | tail -2)"
TS=$(sed -n 's/^tick_stall_ms=\([0-9]*\).*/\1/p' telltale.conf | head -1); S=$(st)
chk "period knob kept the configured tick_stall_ms=$TS (telltale.conf, not the 57 ms rule floor)" 'echo "$S" | grep -q "tick_stall_ms=$TS "' "$S"
# DISARM in the middle of an incident: observe-only in EVERY state, not only NORMAL (no safe layer, no kill, no new incident)
RP=$(field renderer_pid)
slay -f -Q vehfeed > /dev/null 2>&1
waitst TRUTH-LOST 50
./ttctl disarm > /dev/null 2>&1; sleep 0.5; S=$(st)
chk "disarm during TRUTH-LOST -> armed=0, observe-only NORMAL" 'echo "$S" | grep -q "armed=0 state=NORMAL "' "$S"
chk "the open incident is recorded with cause=disarmed" 'cat /dev/telltale-events | grep -q ",TRUTH_LOST,disarmed,"' "$(cat /dev/telltale-events | tail -1)"
./vehfeed trace-hero.csv --hold 30 $SC > log/vehfeed3.log 2>&1 < /dev/null &
sleep 3; S=$(st)
chk "disarmed: no kill, same renderer pid $RP, still NORMAL" 'echo "$S" | grep -q "state=NORMAL .*renderer_pid=$RP "' "$S"
./ttctl arm > /dev/null 2>&1
waitst NORMAL 100; S=$(st)
chk "arm again -> armed=1 NORMAL" 'echo "$S" | grep -q "armed=1 state=NORMAL "' "$S"
# vehicled restart: the monitor drops the dead connection, reconnects and resets its truth window (new seq numbering)
slay -f -Q vehfeed vehicled > /dev/null 2>&1
waitst TRUTH-LOST 50; S=$(st)
chk "vehicled gone -> TRUTH-LOST" 'echo "$S" | grep -q "state=TRUTH-LOST "' "$S"
./vehicled > log/vehicled2.log 2>&1 < /dev/null &
waitfor /dev/vehicle 5
./vehfeed trace-hero.csv --hold 30 $SC > log/vehfeed4.log 2>&1 < /dev/null &
waitst NORMAL 400; S=$(st)
chk "vehicled + feeder restarted -> verified hand-back, NORMAL" 'echo "$S" | grep -q "state=NORMAL "' "$S"
chk "the monitor reconnected to /dev/vehicle (truth window reset)" 'grep -q "reconnected to /dev/vehicle" log/telltale.log' "$(grep -i 'vehicle' log/telltale.log | tail -2)"
# NO_DISPLAY: stop the respawn loop, kill the renderer; nothing registers within restart_timeout_ms
touch stop; slay -f -Q cluster > /dev/null 2>&1
waitst NO-DISPLAY 120; S=$(st)
chk "renderer gone and not restarted -> state NO-DISPLAY (safe layer keeps the truth up)" 'echo "$S" | grep -q "state=NO-DISPLAY "' "$S"
# the hidden incident card is INVISIBLE, not only under the cluster: disarmed (the safe layer is off) and no renderer
# window, the glass at the card's rect must not be the card's fill (PAL_CARD 1a1916; debug is off, so the card was never shown)
./ttctl disarm > /dev/null 2>&1; sleep 0.5
./ttshot log/card-hidden.ppm 1262 322 4 4 > /dev/null 2>&1
H=$(tail -c 48 log/card-hidden.ppm 2>/dev/null | od -An -v -tx1 | tr -d ' \n')
chk "no renderer window, safe layer off: the hidden incident card is not composed (card rect != its fill)" '[ ${#H} -eq 96 ] && ! echo "$H" | grep -q 1a1916' "$H"
./ttctl arm > /dev/null 2>&1
cp log/telltale.log log/core-telltale-a.log
rm -f stop
# (a crash after earlier incidents is checked at the end)
# startup without truth: fresh vehicled (no sample yet), no feeder -> after arm_grace_ms TRUTH-LOST, never WAIT forever
sh tt_up.sh trace-hero.csv 30 0 - all script 0 > log/core-up2.txt 2>&1
sleep 7; S=$(st)
chk "no feeder at startup -> TRUTH-LOST after arm_grace_ms (not WAIT forever)" 'echo "$S" | grep -q "state=TRUTH-LOST "' "$S"
./vehfeed trace-hero.csv --hold 60 $SC > log/vehfeed5.log 2>&1 < /dev/null &
waitst NORMAL 150; S=$(st)
chk "feeder starts -> verified frame, NORMAL" 'echo "$S" | grep -q "state=NORMAL "' "$S"
# restart cap: a renderer that is wrong on EVERY start (test hook fault-on-start = blank) is killed max_restarts times
# inside restart_window_s, then TT-GIVEUP (record written at once); fault removed + rearm -> restart -> NORMAL
R0=$(field restarts)
echo blank > fault-on-start
./ttinject crash > /dev/null 2>&1
waitst GIVEUP 600; S=$(st)
chk "renderer wrong on every start -> TT-GIVEUP (the restart cap holds)" 'echo "$S" | grep -q "state=GIVEUP "' "$S"
R1=$(field restarts)
chk "exactly max_restarts=3 kills before TT-GIVEUP (restarts $R0 -> $R1)" '[ -n "$R1" ] && [ $((R1 - R0)) -eq 3 ]' "$S"
G=$(cat /dev/telltale-events | grep ',TT-GIVEUP,' | tail -1)
chk "the TT-GIVEUP incident is in /dev/telltale-events at once, restarts=3" 'echo "$G" | cut -d, -f13 | grep -qx 3' "$G"
chk "the correction was verified on glass during it (t_safe_true_ns set: safe layer read back = truth)" 'echo "$G" | cut -d, -f19 | grep -Eq "^[1-9][0-9]*$"' "$G"
O=$(cat /dev/telltale | grep '^open=')
chk "cat /dev/telltale keeps the unrecovered incident open (cause=TT-GIVEUP)" 'echo "$O" | grep -q "cause=TT-GIVEUP"' "$O"
rm -f fault-on-start
./ttctl rearm > /dev/null 2>&1
waitst NORMAL 300; S=$(st)
chk "fault removed + rearm -> renderer restarted, verified hand-back, NORMAL" 'echo "$S" | grep -q "state=NORMAL "' "$S"
GID=$(echo "$G" | cut -d, -f1); GN=$(cat /dev/telltale-events | grep -c "^$GID,")
chk "one record per incident: $GID appears once in /dev/telltale-events after the recovery" '[ -n "$GID" ] && [ "$GN" -eq 1 ]' "$GID x $GN"
GX=$(echo "$G" | cut -d, -f14)
chk "the TT-GIVEUP record carries its exposure (last verified frame -> truth verified on glass > 0 ms)" 'echo "$GX" | grep -Eq "^[1-9][0-9]*\.[0-9]$|^0\.[1-9]$"' "$GX"
# a renderer that is blank from boot (never verified, never NORMAL) is not parked in NO-DISPLAY: restart_timeout_ms after it
# registered it goes to SAFE-HOLD, is killed under the cap, and TT-GIVEUP is recorded
cp log/telltale.log log/core-telltale-a2.log
echo blank > fault-on-start
sh tt_up.sh trace-hero.csv 30 0 - all script > log/core-up3.txt 2>&1
waitst GIVEUP 600; S=$(st)
chk "renderer blank from boot -> NO-DISPLAY -> SAFE-HOLD -> restarts -> TT-GIVEUP (not NO-DISPLAY forever)" 'echo "$S" | grep -q "state=GIVEUP "' "$S"
rm -f fault-on-start
./ttctl rearm > /dev/null 2>&1
waitst NORMAL 300; S=$(st)
chk "blank-from-boot fault removed + rearm -> NORMAL" 'echo "$S" | grep -q "state=NORMAL "' "$S"
cp log/telltale.log log/core-telltale-b.log
# a crash AFTER earlier incidents in the same session: the hidden safe layer still holds an earlier truth; it must stay
# invisible when the renderer's window vanishes, so the composed read sees empty glass (BLANK), never a stale value
F0=$(field faults); L0=$(cat /dev/telltale | tail -1 | sed -n 's/^last=\([^ ]*\).*/\1/p'); ./ttinject crash > /dev/null 2>&1
n=0; while { [ "$(field faults)" -le "$F0" ] || ! st | grep -q "state=NORMAL "; } && [ $n -lt 300 ]; do sleep 0.1; n=$((n+1)); done
L=$(cat /dev/telltale | tail -1)
chk "crash after earlier incidents -> a NEW incident, BLANK (no stale safe layer exposed), restarted, NORMAL" '[ "$(field faults)" -gt "$F0" ] && ! echo "$L" | grep -q "last=$L0 " && echo "$L" | grep -Eq "last=TT-BLK-[0-9]+ verdict=BLANK .*recovered=yes"' "$L"
# a restarted monitor relearns the renderer (the cluster re-registers every second) and arms again; nothing else restarts
slay -f -Q telltale > /dev/null 2>&1; sleep 0.5; T0=$(date +%s)
TT_CONF=/tmp/tt/telltale-run.conf TT_CHECKLOG=/tmp/tt/log/checks2.csv ./telltale > log/telltale2.log 2>&1 < /dev/null &
waitfor /dev/telltale 5
waitst NORMAL 300; S=$(st); T1=$(date +%s)
chk "monitor restarted -> relearns the renderer, arms again (NORMAL after $((T1 - T0)) s)" 'echo "$S" | grep -q "armed=1 state=NORMAL "' "$S"
{ grep -hE "TRUTH|NO_DISPLAY|NO-DISPLAY|restart timeout|DETECT|INCIDENT|->|reconnect|disarmed|GIVEUP|SIGKILL|not verified" log/core-telltale-a.log log/core-telltale-a2.log log/core-telltale-b.log log/telltale.log log/telltale2.log; } > log/core-telltale.txt 2>/dev/null
rm -f stop fault-on-start
echo "core: $P/$N passed"
