#!/usr/bin/env bash
# A/B of the safe-layer timing: current monitor vs the 985ab29 build, same bench slice (FREEZE, onset 106.9), per-check logs kept.
set -uo pipefail
# out/ab/telltale_985 = `git show 985ab29:src/telltale/telltale.c` built with the Makefile's qcc flags against src/common (unchanged
# since 985ab29 except the new handback.h, which that file does not include): 251232 bytes, the size of the 985ab29 binary in
# bench-runs/r04-regress/manifest.txt, though not byte-identical to it (cksum in ck-985-*.txt).
cd "$(git rev-parse --show-toplevel)"   # the repository root (build/)
AB=${AB:-/tmp/tt-ab}; mkdir -p $AB   # was a scratch dir; the six run dirs and ck-*.txt were copied here afterwards
scripts/guest.sh put out/ab/telltale_985 out/x86_64/telltale >/dev/null 2>&1
scripts/guest.sh sudo 'cd /tmp/tt; cp telltale telltale_new' >/dev/null 2>&1
for i in 1 2 3; do for v in new 985; do
  scripts/guest.sh sudo "touch /tmp/tt/stop; slay -f -Q telltale cluster vehfeed vehicled hog >/dev/null 2>&1; sleep 1; cd /tmp/tt; cp telltale_$v telltale; cksum telltale" > $AB/ck-$v-$i.txt 2>&1
  TT_TRACE=/tmp/tt-bench/r05regress-015.csv TT_HOLD=3 TT_EVROOT=$AB TT_RUN_ID=$v-$i scripts/scenario.sh freeze 3.2 8 > /dev/null 2>&1
  echo "$v-$i $(grep -h 'SAFE verified' $AB/$v-$i-freeze/telltale.log | cut -c1-100) load $(sysctl -n vm.loadavg)"
done; done
scripts/guest.sh sudo 'touch /tmp/tt/stop; slay -f -Q telltale cluster vehfeed vehicled hog >/dev/null 2>&1; sleep 1; cd /tmp/tt; cp telltale_new telltale; cksum telltale'
echo DONE
