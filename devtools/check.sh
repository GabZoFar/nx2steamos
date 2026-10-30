#!/bin/sh
# Every check that runs without a device or game files, in the dev containers (devtools/container):
#   arm64 (the ARM64 engine): the host built with clang and with Zig, the host's unit tests on glibc, the rewriter's tests, the porter's tests, movies through FFmpeg,
#                             and the probes on lavapipe
#   amd64 (the Steam Deck engine, recomp/): the runtime built for x86-64, and its game-free tests: module layout, site patterns, the FP / SIMD lowering against Unicorn,
#                             the software FMA, the shader translator through shaderc and SPIRV-Tools; then the porter's game code for it, loaded in that runtime
#                             (devtools/deck_porter_check.sh)
# Run from the repository on any machine with Docker (on an Apple-silicon Mac the amd64 half runs under Docker Desktop's x86-64 emulation):
#   sh devtools/check.sh [<title ID>]     both halves (the ARM64 host built for that game, Mario vs. Donkey Kong by default)
#   sh devtools/check.sh arm [<title ID>] the arm64 half only
#   sh devtools/check.sh deck             the amd64 half only
set -eu
mode=both
case "${1:-}" in arm) mode=arm; shift ;; deck) mode=deck; shift ;; esac
game=${1:-0100B99019412000}
here=$(cd "$(dirname "$0")" && pwd); repo=$(dirname "$here")

if [ "$mode" != deck ]; then
docker build -q -t nx2steamos-dev "$here/container" >/dev/null
docker run --rm -v "$repo":/src -w /src nx2steamos-dev sh -c "
set -e
echo '== host, clang';  python3 nxnative/dm/steamos/build.py --game $game --cc clang --out /tmp/host-clang | tail -2
echo '== host, Zig';    python3 nxnative/dm/steamos/build.py --game $game --cc zig --release --out /tmp/host-zig | tail -2
echo '   needs:' \$(objdump -T /tmp/host-zig/nx2s-host | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -1) \$(readelf -d /tmp/host-zig/nx2s-host | sed -n 's/.*NEEDED.*\[\(.*\)\]/\1/p' | tr '\n' ' ')
echo '== host unit tests on glibc'; python3 nxnative/dm/steamos/tools/host_tests.py --game $game | tail -1 || true
echo '== rewriter tests'; (cd nxnative/dm/tools/tests && python3 -m unittest test_target_slots test_jump_copies 2>&1 | tail -1)
echo '== porter tests';   python3 -m unittest discover -s tests 2>&1 | tail -1
echo '== movies through FFmpeg (H.264, AAC)'; sh nxnative/dm/steamos/tools/media_test.sh $game 2>&1 | tail -3
echo '== probes on lavapipe'; mkdir -p /tmp/p && cc -O1 -o /tmp/p/tlsprobe probes/tlsprobe.c -pthread -ldl && /tmp/p/tlsprobe | tail -1
cc -O1 -o /tmp/p/vkzc probes/vkzc.c -ldl && /tmp/p/vkzc | grep RESULT
"
fi

if [ "$mode" != arm ]; then
docker build -q --platform linux/amd64 -t nx2steamos-dev:amd64 "$here/container" >/dev/null
# recomp/ builds into its own build/ (ignored by recomp/.gitignore); test_translate.sh links MSYS2's library names, so its two steps are run here with Debian's
docker run --rm --platform linux/amd64 -v "$repo":/repo -w /repo/recomp nx2steamos-dev:amd64 sh -c "
set -e
echo '== Steam Deck engine: runtime for x86-64'; make -j\$(nproc) >/tmp/make.log 2>&1 || { tail -30 /tmp/make.log; exit 1; }; ls -la build/savagewind | awk '{print \"   \" \$5 \" bytes \" \$9}'
echo '== layout';     python3 tools/test_layout.py 2>&1 | tail -1
echo '== sites';      python3 tools/test_game_sites.py 2>&1 | tail -1
echo '== FP / SIMD against Unicorn'; python3 tools/test_simd.py --jobs \$(nproc) 2>&1 | tail -1
echo '== software FMA'; clang -O1 -mfma -Iruntime tools/test_softfma.c -o build/test_softfma -lm && build/test_softfma 2>&1 | tail -1
echo '== shader translator'; T=build/test_translate; mkdir -p \$T/dump; rm -f \$T/dump/* \$T/log_*.txt
clang -O2 -Wall -c runtime/nvn/maxwell/translate.c -o \$T/new.o && clang -O2 -Wall -DWITH_GLSL -c tools/test_translate.c -o \$T/t.o
clang++ \$T/t.o \$T/new.o -o \$T/test_translate -lshaderc -lSPIRV-Tools -lpthread -lm
for i in \$(seq 0 11); do \$T/test_translate runtime/nvn/maxwell/translate.c 1667 \$((i * 7919 + 1)) glsl \$T/dump > \$T/log_\$i.txt 2>&1 & done; wait
cat \$T/log_*.txt | sed -n 's/^GLSL: \([0-9]*\) compiled+validated.*, \([0-9]*\) compile failures, \([0-9]*\) validation failures, \([0-9]*\) translation failures.*/\1 \2 \3 \4/p' |
    awk '{ok += \$1; cf += \$2; vf += \$3; tf += \$4} END {print \"   \" ok \" translated, compiled and validated; \" cf \" compile failures, \" vf \" validation failures; \" tf \" random programs refused\"; exit (cf + vf > 0)}'
echo '== porter: game code for the Steam Deck engine'; sh /repo/devtools/deck_porter_check.sh
"
fi
