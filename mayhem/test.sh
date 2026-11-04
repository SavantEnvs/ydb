#!/usr/bin/env bash
# mayhem/test.sh: RUN the upstream unit tests (library/cpp/*/ut) that mayhem/build.sh built into
# out/test, and map their results to CTRF. Every binary must exit 0 and report >=1 passing
# test with no failures; the pass counts are asserted per binary.
set -uo pipefail
[ -n "${SOURCE_DATE_EPOCH:-}" ] || unset SOURCE_DATE_EPOCH
: "${SRC:=/mayhem}"
cd "$SRC"

# emit_ctrf <tool> <passed> <failed> [skipped] [pending] [other]
# Writes a CTRF report (file + stdout `CTRF {...}` marker) and returns non-zero iff failed>0.
emit_ctrf() {
  local tool="$1" passed="$2" failed="$3" skipped="${4:-0}" pending="${5:-0}" other="${6:-0}"
  local tests=$(( passed + failed + skipped + pending + other ))
  cat > "${CTRF_REPORT:-$SRC/ctrf-report.json}" <<JSON
{
  "results": {
    "tool": { "name": "$tool" },
    "summary": {
      "tests": $tests,
      "passed": $passed,
      "failed": $failed,
      "pending": $pending,
      "skipped": $skipped,
      "other": $other
    }
  }
}
JSON
  printf 'CTRF {"results":{"tool":{"name":"%s"},"summary":{"tests":%d,"passed":%d,"failed":%d,"pending":%d,"skipped":%d,"other":%d}}}\n' \
    "$tool" "$tests" "$passed" "$failed" "$pending" "$skipped" "$other"
  [ "$failed" -eq 0 ]
}


declare -A MIN=( [ut_base32]=4 [ut_json]=35 [ut_cgiparam]=27 [ut_http_io]=55 [ut_rb_tree]=10 )
passed=0; failed=0
for t in ut_base32 ut_json ut_cgiparam ut_http_io ut_rb_tree; do
  bin="$SRC/out/test/$t"
  if [ ! -x "$bin" ]; then echo "missing test runner $bin (build.sh bug)" >&2; failed=$((failed + 1)); continue; fi
  out="$(cd "$SRC/out" && timeout 900 "$bin" 2>&1)"; rc=$?
  n="$(printf '%s\n' "$out" | sed -n 's/^\[DONE\] ok: \([0-9]*\).*/\1/p; s/^\[  PASSED  \] \([0-9]*\) tests\?\./\1/p' | tail -1)"
  f="$(printf '%s\n' "$out" | grep -c '^\[FAIL\]\|^\[  FAILED  \]')"
  n="${n:-0}"
  echo "$t: rc=$rc passed=$n failed-markers=$f (min ${MIN[$t]})"
  if [ "$rc" -ne 0 ] || [ "$f" -ne 0 ] || [ "$n" -lt "${MIN[$t]}" ]; then
    printf '%s\n' "$out" | tail -20 >&2
    failed=$((failed + 1))
  else
    passed=$((passed + n))
  fi
done
emit_ctrf "ydb-unittest" "$passed" "$failed" 0
