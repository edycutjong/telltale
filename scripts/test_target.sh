#!/usr/bin/env bash
# make test-target: the on-target test suite on the running QNX guest (QEMU), after `make target` + deploy.
# Every case is a real run of the whole stack; scripts/check_run.py asserts each run; raw logs are kept in
# evidence/test-target/<UTC stamp>/ and the summary in summary.txt there.
#   scripts/test_target.sh [--no-policy]
# Cases: one fault run per class (freeze blank stale misdraw crash lampdrop + STARVE renderer-CPU-only),
# a clean hero drive (0 incidents), the hold -> heal -> hand-back acceptance sequence (no kill), the same sequence in
# engineering mode (banner text + incident card, both read back from the composed display by ttshot), FREEZE with slm as the
# renderer supervisor (S-SLM), tests/target/core_items.sh (/dev/vehicle, ttctl, /dev/telltale, TRUTH_LOST, NO_DISPLAY), then
# (unless --no-policy) a FREEZE run with the secpol policy loaded and every process typed + tests/target/secpol_denies.sh.
# NOTE: the policy push is immutable until the guest reboots, and an untyped Screen cannot be restarted under it,
# so the policy cases run last (Screen must already be running, unpinned: scripts/guest_screen.sh '' any).
set -uo pipefail
cd "$(dirname "$0")/.."
TS=$(date -u +%Y%m%dT%H%M%SZ); export TT_EVROOT=evidence/test-target/$TS; mkdir -p "$TT_EVROOT"
SUM="$TT_EVROOT/summary.txt"; : > "$SUM"
hostload() { echo "host load ($1): $(sysctl -n vm.loadavg 2>/dev/null || cat /proc/loadavg)" | tee -a "$SUM"; }   # TCG timing depends on it
hostload start
pass=0; fail=0
run_case() {   # run_case <name> <class> <at> <settle> [env...]
  local name=$1 cls=$2 at=$3 settle=$4; shift 4
  env "$@" TT_RUN_ID="$name" scripts/scenario.sh "$cls" "$at" "$settle" > /dev/null 2>&1
  local out; out=$(python3 scripts/check_run.py "$TT_EVROOT/$name-$cls" "$cls")
  echo "$out" | tee -a "$SUM"
  pass=$((pass + $(echo "$out" | grep -c '^PASS'))); fail=$((fail + $(echo "$out" | grep -c '^FAIL')))
}
AT=12.221   # hero drive: braking through 48 km/h (data/trace-hero.meta inject_at_s)
for c in freeze blank stale misdraw crash; do run_case "t" "$c" "$AT" 12; done
run_case t lampdrop 16.0 12                                   # brake lamp lit from 15.848 s
run_case t starve "$AT" 10 TT_SCOPE=renderer                 # "STARVE (renderer CPU only)"
run_case t none 0 56                                          # whole hero drive + parked tail
run_case t accept "$AT" 10 TT_INJECT=stale TT_HEAL_AT=14.0 TT_CONF_EXTRA=mismatch_n=1000
run_case t debug "$AT" 14 TT_INJECT=stale TT_HEAL_AT=14.0 TT_CONF_EXTRA=mismatch_n=1000 TT_DEBUG=1   # engineering banner + incident card, read back
run_case slm freeze "$AT" 12 TT_SUP=slm                     # S-SLM: slm (not the script loop) restarts the renderer
# Core items the fault runs do not assert: /dev/vehicle, ttctl, /dev/telltale, TRUTH_LOST + recovery, NO_DISPLAY
out=$(scripts/guest.sh sudo 'sh /tmp/tt/core_items.sh' 2>&1)
echo "$out" | tee "$TT_EVROOT/core.txt" | tee -a "$SUM"
mkdir -p "$TT_EVROOT/core"; for f in telltale.log core-telltale.txt core-up.txt; do scripts/guest.sh get "/tmp/tt/log/$f" "$TT_EVROOT/core/" 2>/dev/null || true; done
pass=$((pass + $(echo "$out" | grep -c '^PASS'))); fail=$((fail + $(echo "$out" | grep -c '^FAIL')))
if [ "${1:-}" != --no-policy ]; then
  run_case pol freeze "$AT" 12 TT_POLICY=1
  out=$(scripts/guest.sh sudo 'sh /tmp/tt/secpol_denies.sh' 2>&1)
  echo "$out" | tee "$TT_EVROOT/secpol.txt" | tee -a "$SUM"
  pass=$((pass + $(echo "$out" | grep -c '^PASS'))); fail=$((fail + $(echo "$out" | grep -c '^FAIL')))
fi
hostload end
echo "test-target: $pass passed, $fail failed  (QEMU (TCG) x86_64 guest; logs $TT_EVROOT)" | tee -a "$SUM"
[ "$fail" = 0 ]
