#!/usr/bin/env bash
# Host-side replay of the committed on-target run that README.md cites (its "On-target test summary" row). Needs no QNX SDP
# and no guest; CI Stage 4 runs it on every push. It fails closed:
#  1. the cited summary.txt was recorded at a clean commit (no "uncommitted"), ends "<N> passed, 0 failed", holds exactly N
#     PASS lines, and README.md states "<N> passed / 0 failed at <sha>" for that commit;
#  2. the system under test is unchanged since <sha> (FROZEN below; needs full git history);
#  3. every case directory scripts/test_target.sh writes (the list below, checked against its run_case lines) is present,
#     including the secpol cases, and scripts/check_run.py passes on each with the incident schema declared (--width). The
#     checker is the one test_target.sh runs, at HEAD, so it may assert more than it did when the run was recorded. A checker
#     crash, a missing incident log or a case with no assertion is a failure, never a skip;
#  4. core.txt and secpol.txt (core_items.sh, secpol_denies.sh output) have no FAIL line and reach their own "n/n passed".
#   scripts/replay_target_run.sh [evidence/test-target/<stamp>]     (default: the run README.md cites)
# Only by hand, for an older run: TT_REPLAY_WIDTH=N (its incident schema; default 19 = telltale.c EV_HEADER) and
# TT_REPLAY_POLICY=0 (a run made with test_target.sh --no-policy). CI uses the defaults: the cited run must carry both.
set -euo pipefail
cd "$(dirname "$0")/.."
die() { echo "FAIL $*"; exit 1; }
WIDTH=${TT_REPLAY_WIDTH:-19}; POLICY=${TT_REPLAY_POLICY:-1}
cited=$(sed -n 's#.*On-target test summary.*evidence/test-target/\([0-9TZ]*\)/summary.txt.*#\1#p' README.md | head -1)
root=${1:-evidence/test-target/$cited}; root=${root%/}
s=$root/summary.txt
[ -f "$s" ] || die "no summary at $s (README cites '${cited:-nothing}')"

# 1. the recorded claim
sha=$(sed -n '1s/^git: \([0-9a-f]*\).*/\1/p' "$s")
[ -n "$sha" ] || die "$s: first line is not 'git: <sha>'"
head -1 "$s" | grep -q uncommitted && die "$s was recorded with uncommitted source changes"
last=$(tail -n 1 "$s")
n=$(sed -n 's/^test-target: \([0-9]*\) passed, 0 failed.*/\1/p' <<<"$last")
[ -n "$n" ] || die "$s: last line is not '<N> passed, 0 failed': $last"
rec=$(grep -c '^PASS' "$s" || true)
[ "$rec" = "$n" ] || die "$s: $rec PASS lines recorded, the summary says $n"
if [ -z "${1:-}" ]; then
  grep -Eq "^\| On-target suite \| $n passed / 0 failed at \`$sha\` \|\$" README.md \
    || die "README.md's 'On-target suite' row does not state '$n passed / 0 failed at \`$sha\`'"
fi
echo "claim: $n passed, 0 failed at $sha ($root)"

# 2. the system under test is the code and the input that ran. Not frozen: the harness (scripts/test_target.sh, scenario.sh,
#    check_run.py, deploy.sh, guest.sh), the Makefile (changed since 985ab29 only to add the ttrec target) and
#    src/tools/ttrec.c (demo footage). The binaries on the guest are not hashed by the recorded run; their source is.
FROZEN="src/telltale src/cluster src/vehicled src/vehfeed src/common src/tools/ttinject.c src/tools/ttctl.c src/tools/hog.c
        src/tools/ttshot.c secpol scripts/target tests/target data/trace-hero.csv data/scenario-hero.csv"
# shellcheck disable=SC2086
if ! git diff --quiet "$sha" HEAD -- $FROZEN; then
  git diff --stat "$sha" HEAD -- $FROZEN; die "code under test changed since $sha: re-run make test-target before citing it"
fi
echo "code and input under test unchanged since $sha"

# 3. every case test_target.sh writes, re-asserted. The list must equal the one derived from its run_case lines (the
#    for-loop over the five classes plus each literal run_case), so a case added there cannot go unchecked here.
want="t-freeze t-blank t-stale t-misdraw t-crash t-lampdrop t-starve t-none t-accept t-debug slm-freeze pol-freeze"
derived=$(awk '/^ *for c in .*run_case/ { for (i = 4; i <= NF && $i != "do"; i++) { gsub(/;/, "", $i); printf "t-%s ", $i } }
               /^ *run_case +[a-z0-9]+ +[a-z]+/ { printf "%s-%s ", $2, $3 }' scripts/test_target.sh)
# shellcheck disable=SC2086
[ "$(printf '%s\n' $want | sort)" = "$(printf '%s\n' $derived | sort)" ] \
  || die "case list drifted from scripts/test_target.sh: here [$want], there [$derived]"
if [ "$POLICY" = 1 ]; then
  grep -q '^secpol: ' "$s" || die "$s has no secpol result: the policy cases are required (TT_REPLAY_POLICY=0 only for a --no-policy run)"
else
  want=${want% pol-freeze}
fi
pass=0; fail=0; cases=0
for c in $want; do
  d=$root/$c
  [ -d "$d" ] || { echo "$c: FAIL case directory missing"; fail=$((fail + 1)); continue; }
  rc=0; out=$(python3 scripts/check_run.py "$d" "${c##*-}" --width "$WIDTH" 2>&1) || rc=$?
  p=$(grep -c '^PASS' <<<"$out" || true); f=$(grep -c '^FAIL' <<<"$out" || true)
  if [ "$f" -eq 0 ] && { [ "$p" -eq 0 ] || [ "$rc" -ne 0 ]; }; then   # no PASS and no FAIL, or a crash
    echo "$out" | tail -3; echo "$c: FAIL checker gave no verdict (exit $rc)"; f=$((f + 1))
  fi
  grep '^FAIL' <<<"$out" || true
  echo "$c: $p pass, $f fail"
  pass=$((pass + p)); fail=$((fail + f)); cases=$((cases + 1))
done

# 4. the target scripts' own output, recounted
for k in core secpol; do
  [ "$k" = secpol ] && [ "$POLICY" != 1 ] && continue
  t=$root/$k.txt
  [ -f "$t" ] || { echo "$k: FAIL $t missing"; fail=$((fail + 1)); continue; }
  p=$(grep -c '^PASS' "$t" || true); f=$(grep -c '^FAIL' "$t" || true)
  grep -Eq "^$k: $p/$p passed\$" "$t" || { echo "$k: FAIL no '$k: $p/$p passed' line (the script did not finish)"; f=$((f + 1)); }
  echo "$k: $p pass, $f fail"
  pass=$((pass + p)); fail=$((fail + f))
done
echo "replayed $cases recorded target cases + core + secpol from $root: $pass assertions passed, $fail failed"
[ "$fail" -eq 0 ]
