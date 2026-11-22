#!/usr/bin/env bash
# Host-side replay of a committed on-target run: re-assert every recorded QNX/QEMU case with scripts/check_run.py.
# Needs no QNX SDP and no guest; it reads the raw logs under evidence/test-target/<stamp>/ (default: the newest).
# Every case dir is <name>-<class>; the run is the real one, the assertion is the one test_target.sh applied live.
set -euo pipefail
cd "$(dirname "$0")/.."
root=${1:-$(ls -d evidence/test-target/*/ | sort | tail -1)}
pass=0; fail=0; cases=0
for d in "$root"/*/; do
  n=$(basename "$d"); [ "$n" = core ] && continue
  [ -f "$d/incidents.csv" ] || continue
  out=$(python3 scripts/check_run.py "$d" "${n##*-}" || true)   # a FAIL line is counted below, not fatal here
  p=$(grep -c '^PASS' <<<"$out" || true); f=$(grep -c '^FAIL' <<<"$out" || true)
  echo "$n: $p pass, $f fail"
  pass=$((pass + p)); fail=$((fail + f)); cases=$((cases + 1))
done
echo "replayed $cases recorded target cases from $root: $pass assertions passed, $fail failed"
[ "$cases" -gt 0 ] && [ "$fail" -eq 0 ]
