#!/bin/sh
# On-target secpol checks (run as root from /tmp/tt on the QNX guest, with telltale.bin pushed and the stack
# running in its types: tt_up.sh <trace> <hold> 1). Each line: PASS|FAIL <what>. Last line: secpol: <n>/<m> passed.
# Every write attempt uses a one-row trace so a permitted write would only repeat the parked sample.
cd /tmp/tt || exit 1
export TT_POL=/tmp/tt/telltale.bin
P=0; N=0
ok()   { N=$((N+1)); P=$((P+1)); echo "PASS $*"; }
bad()  { N=$((N+1)); echo "FAIL $*"; }
secpol -Q | grep -q 'policy id:0' && { echo "FAIL no policy loaded"; echo "secpol: 0/1 passed"; exit 1; }
{ echo "t_s,speed_mps"; tail -1 trace-hero.csv | sed 's/^[^,]*,/0.0,/'; } > one.csv
DENY='DCMD_VEH_SET: Operation not permitted|/dev/vehicle: (Permission denied|Operation not permitted)'

# 1. only feeder_t may write /dev/vehicle (vehicle:write checked in vehicled)
# try_write <label> [env...]: a vehfeed that is denied exits (DCMD_VEH_SET EPERM at once, or /dev/vehicle open refused
# after its 5 s open retries); one still running after 7 s is writing -> not denied. Only this pid is ever killed.
try_write() {
  lab=$1; shift
  env "$@" ./vehfeed one.csv > wr.out 2>&1 &
  p=$!; i=0
  while kill -0 $p 2>/dev/null && [ $i -lt 70 ]; do sleep 0.1; i=$((i+1)); done
  if kill -0 $p 2>/dev/null; then kill -9 $p; bad "vehicle write NOT denied for $lab: $(tail -1 wr.out)"
  elif grep -Eq "$DENY" wr.out; then ok "vehicle write denied for $lab ($(grep -Eo "$DENY" wr.out | head -1))"
  else bad "vehicle writer for $lab exited without a deny: $(tail -1 wr.out)"; fi
}
for t in monitor_t renderer_t injector_t hog_t; do try_write $t TT_TYPE=$t; done
try_write "untyped root"
rm -f wr.out
# the running feeder (feeder_t) is the positive control: the sample counter advances
s1=$(sed -n 's/.*seq=\([0-9]*\).*/\1/p' /dev/vehicle | head -1); sleep 0.5; s2=$(sed -n 's/.*seq=\([0-9]*\).*/\1/p' /dev/vehicle | head -1)
[ -n "$s1" ] && [ "$s2" -gt "$s1" ] 2>/dev/null && ok "feeder_t writes ($s1 -> $s2)" || bad "feeder_t not writing ($s1 -> $s2)"

# 2. only injector_t may control the monitor (telltale:control)
for t in renderer_t feeder_t; do
  out=$(env TT_TYPE=$t ./ttctl period 20 2>&1); echo "$out" | grep -Eq 'ttctl period: (Operation not permitted|Permission denied)|/dev/telltale: (Permission denied|Operation not permitted)' && ok "monitor control denied for $t" || bad "monitor control NOT denied for $t: $out"
done
out=$(./ttctl period 20 2>&1); echo "$out" | grep -Eq 'ttctl period: (Operation not permitted|Permission denied)' && ok "monitor control denied for untyped root" || bad "monitor control NOT denied for untyped root: $out"
out=$(env TT_TYPE=injector_t ./ttctl period 20 2>&1); case "$out" in *ok*) ok "injector_t controls the monitor";; *) bad "injector_t control failed: $out";; esac

# 2b. only injector_t may use the renderer's fault test hook (cluster:fault checked in cluster); `heal` is harmless
FDENY='DCMD_CL_FAULT: (Operation not permitted|Permission denied)|/dev/cluster-ctl: (Permission denied|Operation not permitted)'
for t in renderer_t feeder_t monitor_t; do
  out=$(env TT_TYPE=$t ./ttinject heal 2>&1); echo "$out" | grep -Eq "$FDENY" && ok "fault hook denied for $t" || bad "fault hook NOT denied for $t: $out"
done
out=$(./ttinject heal 2>&1); echo "$out" | grep -Eq "$FDENY" && ok "fault hook denied for untyped root" || bad "fault hook NOT denied for untyped root: $out"
out=$(env TT_TYPE=injector_t ./ttinject heal 2>&1); case "$out" in *t_command_ns=*) ok "injector_t may use the fault hook";; *) bad "injector_t fault hook failed: $out";; esac

# 3. hog_t is capped at priority 40
out=$(env TT_TYPE=hog_t ./hog 10 50 2>&1); echo "$out" | grep -q 'setschedparam(50) as hog_t' && ok "hog_t refused prio 50" || bad "hog_t not refused at prio 50: $(echo "$out" | tail -1)"
out=$(env TT_TYPE=hog_t ./hog 10 40 2>&1); case "$out" in *t_hog_start*) ok "hog_t runs at prio 40";; *) bad "hog_t could not run at 40: $out";; esac

# 4. every Telltale process runs in its type
for pr in vehicled:vehicle_t telltale:monitor_t cluster:renderer_t vehfeed:feeder_t; do
  n=${pr%%:*}; t=${pr##*:}
  grep -q "$n: .*type=$t" log/$n.log 2>/dev/null && ok "$n runs as $t" || bad "$n not in $t"
done
rm -f one.csv
echo "secpol: $P/$N passed"
[ "$P" = "$N" ]
