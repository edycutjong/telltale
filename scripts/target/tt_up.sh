#!/bin/sh
# Start the Telltale stack on the QNX guest (run as root from /tmp/tt; scripts/deploy.sh copies it there).
#   tt_up.sh [trace.csv] [hold_s] [policy] [cpu] policy=1: push telltale.bin (once per boot) and run every
#                                              process in its secpol type; cpu=N: pin every process to CPU N
#                                              (`on -C N`; the STARVE runs pin Screen there too, scripts/guest_screen.sh)
#   5th arg scope: all (default) | renderer = pin only the cluster ("STARVE (renderer CPU only)")
#   6th arg supervisor: script (default: a shell respawn loop, "restarted by script") | slm (telltale-slm.xml)
#   7th arg feed: 1 (default) starts vehfeed | 0 = no feeder (vehicled has no sample: the startup-without-truth check)
# Order: vehicled -> telltale (monitor) -> cluster under a respawn loop -> vehfeed.
# Default supervisor = a shell respawn loop ("restarted by script"); 6th arg slm = the QNX slm (telltale-slm.xml).
cd /tmp/tt || exit 1
TRACE=${1:-trace-hero.csv}; HOLD=${2:-5}; POL=${3:-0}; CPU=${4:-}; SCOPE=${5:-all}; SUP=${6:-script}; FEED=${7:-1}
[ "$CPU" = - ] && CPU=
PIN=; [ -n "$CPU" ] && PIN="on -C $CPU"
RPIN=$PIN; [ "$SCOPE" = renderer ] && PIN=
mkdir -p ev log
touch stop; [ -f slm.pid ] && kill -9 $(cat slm.pid) 2>/dev/null; rm -f slm.pid; slay -f -Q telltale cluster vehfeed vehicled hog >/dev/null 2>&1; sleep 1; rm -f stop
rm -f ev/*.csv log/*.log log/checks.csv
if [ "$POL" = 1 ]; then
  secpol -Q | grep -q 'policy id:0' && secpolpush /tmp/tt/telltale.bin
  secpol -Q | head -2
  export TT_POL=/tmp/tt/telltale.bin
  V="env TT_TYPE=vehicle_t"; M="env TT_TYPE=monitor_t"; R="env TT_TYPE=renderer_t TT_UID=1000"; F="env TT_TYPE=feeder_t"
else
  V=env; M=env; R="env TT_UID=1000"; F=env
fi
$PIN $V ./vehicled > log/vehicled.log 2>&1 < /dev/null &
waitfor /dev/vehicle 5 || { echo "vehicled did not come up"; cat log/vehicled.log; exit 1; }
CONF=/tmp/tt/telltale.conf; [ -f /tmp/tt/telltale-run.conf ] && CONF=/tmp/tt/telltale-run.conf
$PIN $M TT_CONF=$CONF TT_CHECKLOG=/tmp/tt/log/checks.csv ./telltale > log/telltale.log 2>&1 < /dev/null &
waitfor /dev/telltale 5 || { echo "telltale did not come up"; cat log/telltale.log; exit 1; }
chmod 1777 /tmp/tt/ev /tmp/tt/log
if [ "$SUP" = slm ]; then
  $RPIN $R slm -b 0 /tmp/tt/telltale-slm.xml > log/super.log 2>&1 < /dev/null &
  echo $! > slm.pid   # only our slm is ever stopped (the image may run its own)
else
  ( while [ ! -f /tmp/tt/stop ]; do $RPIN $R ./cluster >> log/cluster.log 2>&1; echo "respawn $(date +%T)" >> log/super.log; sleep 0.1; done ) > /dev/null 2>&1 < /dev/null &
fi
sleep 2
SC=; [ "$TRACE" = trace-hero.csv ] && [ -f scenario-hero.csv ] && SC="--scenario scenario-hero.csv"
[ "$FEED" = 1 ] && $PIN $F ./vehfeed "$TRACE" --hold "$HOLD" $SC > log/vehfeed.log 2>&1 < /dev/null &
sleep 1
pidin -F '%a %b %N %p %J %B %i' | grep -E 'vehicled|vehfeed|telltale|cluster|slm' | grep -v grep
