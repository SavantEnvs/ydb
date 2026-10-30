#!/usr/bin/env bash
# mayhem/build.sh — build the ydb in-tree library fuzz harnesses (offline, no ya) and the functional tests.
set -euo pipefail

[ -n "${SOURCE_DATE_EPOCH:-}" ] || unset SOURCE_DATE_EPOCH

: "${SANITIZER_FLAGS=-fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer}"
: "${DEBUG_FLAGS:=-g -gdwarf-3}"
: "${CC:=clang}" ; : "${CXX:=clang++}" ; : "${LIB_FUZZING_ENGINE:=-fsanitize=fuzzer}"
: "${MAYHEM_JOBS:=$(nproc)}"
: "${COVERAGE_FLAGS=}"
: "${SRC:=/mayhem}"
export SRC

cd "$SRC"
OUT="$SRC/out"
mkdir -p "$OUT/fuzz-obj" "$OUT/bin"

# Targets: <binary name>=<upstream FUZZ module directory>
TARGETS=(
  fuzz_base32=library/cpp/string_utils/base32/fuzz
  fuzz_base64_lib=library/cpp/string_utils/base64/fuzz/lib
  fuzz_base64_uneven=library/cpp/string_utils/base64/fuzz/uneven
  fuzz_rb_tree=library/cpp/containers/intrusive_rb_tree/fuzz
  fuzz_cgiparam=library/cpp/cgiparam/fuzz
  fuzz_json=library/cpp/json/fuzzy_test
  fuzz_monlib_json=library/cpp/monlib/encode/json/fuzz
  fuzz_monlib_prometheus=library/cpp/monlib/encode/prometheus/fuzz
  fuzz_monlib_spack=library/cpp/monlib/encode/spack/fuzz
  fuzz_http_io=library/cpp/http/io/fuzz
)

# Leak detection off at build time (ASan/UBSan stay on); standalone driver compiled as C.
$CXX $SANITIZER_FLAGS $DEBUG_FLAGS -c mayhem/lsan_off.cc -o "$OUT/lsan_off.o"
$CXX $SANITIZER_FLAGS $DEBUG_FLAGS -c mayhem/stubs.cc -o "$OUT/stubs.o"
$CXX -O1 -c mayhem/stubs.cc -o "$OUT/stubs-test.o"
$CC $SANITIZER_FLAGS $DEBUG_FLAGS -c "$STANDALONE_FUZZ_MAIN" -o "$OUT/standalone_main.o"

COMMON="$SANITIZER_FLAGS $DEBUG_FLAGS -O1 -fno-omit-frame-pointer -fsanitize=fuzzer-no-link"
EXE_ARGS=()
for t in "${TARGETS[@]}"; do EXE_ARGS+=(--exe "$t"); done
# upstream unit tests, built with the project's NORMAL flags (no sanitizers) so test.sh only runs them
TESTS=(
  ut_base32=library/cpp/string_utils/base32/ut
  ut_json=library/cpp/json/ut
  ut_cgiparam=library/cpp/cgiparam/ut
  ut_rb_tree=library/cpp/containers/intrusive_rb_tree/ut
  ut_http_io=library/cpp/http/io/ut
)
for t in "${TESTS[@]}"; do EXE_ARGS+=(--test "$t"); done
python3 mayhem/yamk.py --out "$OUT/buildfile" --cc "$CC" --cxx "$CXX" \
  --cflags="$COMMON" --cxxflags=-std=c++20 \
  --test-extra-obj "$OUT/stubs-test.o" --testdir "$OUT/test" --objdir "$OUT/fuzz-obj" --bindir "$OUT/bin" \
  --standalone-obj "$OUT/standalone_main.o" --extra-obj "$OUT/lsan_off.o" --extra-obj "$OUT/stubs.o" \
  --fuzzer="$LIB_FUZZING_ENGINE" "${EXE_ARGS[@]}"
ninja -C "$SRC" -f "$OUT/buildfile" -j "$MAYHEM_JOBS"
