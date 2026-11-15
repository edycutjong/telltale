#!/bin/sh
# Start the Telltale stack on the QNX guest (run as root from /tmp/tt; scripts/deploy.sh copies it there).
#   tt_up.sh [trace.csv] [hold_s] [policy] [cpu] policy=1: push telltale.bin (once per boot) and run every
#                                              process in its secpol type; cpu=N: pin every process to CPU N
#                                              (`on -C N`; the STARVE runs pin Screen there too, scripts/guest_screen.sh)
#   5th arg scope: all (default) | renderer = pin only the cluster ("STARVE (renderer CPU only)")
# Order: vehicled -> telltale (monitor) -> cluster under a respawn loop -> vehfeed.
# The respawn loop stands in for slm until slm is wired in: a killed renderer is "restarted by script".
cd /tmp/tt || exit 1
TRACE=${1:-trace-hero.csv}; HOLD=${2:-5}; POL=${3:-0}; CPU=${4:-}; SCOPE=${5:-all}
PIN=; [ -n "$CPU" ] && PIN="on -C $CPU"
RPIN=$PIN; [ "$SCOPE" = renderer ] && PIN=
mkdir -p ev log
touch stop; slay -f -Q telltale cluster vehfeed vehicled hog >/dev/null 2>&1; sleep 1; rm -f stop
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
$PIN $M TT_CONF=/tmp/tt/telltale.conf TT_CHECKLOG=/tmp/tt/log/checks.csv ./telltale > log/telltale.log 2>&1 < /dev/null &
waitfor /dev/telltale 5 || { echo "telltale did not come up"; cat log/telltale.log; exit 1; }
chmod 1777 /tmp/tt/ev /tmp/tt/log
( while [ ! -f /tmp/tt/stop ]; do $RPIN $R ./cluster >> log/cluster.log 2>&1; echo "respawn $(date +%T)" >> log/super.log; sleep 0.1; done ) > /dev/null 2>&1 < /dev/null &
sleep 2
$PIN $F ./vehfeed "$TRACE" --hold "$HOLD" > log/vehfeed.log 2>&1 < /dev/null &
sleep 1
pidin -F '%a %b %N %p %J %B %i' | grep -E 'vehicled|vehfeed|telltale|cluster' | grep -v grep
