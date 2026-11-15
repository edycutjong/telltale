#!/usr/bin/env bash
# Copy the Telltale binaries, policy, traces and guest scripts to the running QNX guest (/tmp/tt).
#   scripts/deploy.sh            (after `make target`)
set -euo pipefail
cd "$(dirname "$0")/.."
B=out/${QNX_ARCH:-x86_64}
scripts/guest.sh sudo 'touch /tmp/tt/stop 2>/dev/null; slay -f -Q telltale cluster vehfeed vehicled hog >/dev/null 2>&1; mkdir -p /tmp/tt; chmod 1777 /tmp/tt; cd /tmp/tt && rm -f vehicled vehfeed cluster telltale ttinject ttctl hog ttshot; true'
scripts/guest.sh put $B/vehicled $B/vehfeed $B/cluster $B/telltale $B/ttinject $B/ttctl $B/hog $B/ttshot $B/telltale.bin \
  data/trace-hero.csv data/trace-bench.csv scripts/target/tt_up.sh scripts/target/telltale.conf
scripts/guest.sh run 'cd /tmp/tt && chmod +x tt_up.sh && ls'
