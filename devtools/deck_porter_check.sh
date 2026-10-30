#!/bin/sh
# The porter's Steam Deck engine path, in the amd64 dev container after recomp/'s make (devtools/check.sh deck runs it): porter/deck.py's game code id is the one make
# computes, and the game code the porter builds from recomp/'s made-up modules (tools/test_layout.py: no game code) loads in the runtime built from recomp/.
set -eu
cd /repo/recomp
ids=$(printf 'nx2s-ids:\n\t@echo $(GAMELIB_ID) $(GAMELIB_ID_COMPAT)\n' | make -s -f Makefile -f - nx2s-ids)
mine=$(cd /repo && NX2S_HOME=/tmp/nx2s-home python3 -c 'from porter import deck; print(deck.gamelib_id(), deck.gamelib_id("x86-64-v2"))')
if [ "$ids" != "$mine" ]; then echo "   [RED] game code id: make says $ids, porter/deck.py $mine"; exit 1; fi
echo "   game code id $mine (x86-64-v3, x86-64-v2): make's"
rm -rf /tmp/nx2s-game /tmp/nx2s-data && mkdir -p /tmp/nx2s-data
(cd tools && python3 -c 'import sys, test_layout; test_layout.make_exefs(sys.argv[1])' /tmp/nx2s-game/exefs)
(cd /repo && NX2S_HOME=/tmp/nx2s-home python3 -c '
import os, time
from pathlib import Path
from porter import deck, toolchain
t0 = time.time()
lib = deck.build_game_code(Path("/tmp/nx2s-game/exefs"), Path("/tmp/nx2s-game"), zig=toolchain.find_zig(), jobs=os.cpu_count(), env=dict(os.environ),
                           logs=Path("/tmp/nx2s-logs"), sites=False)
print(f"   built {lib.name} for x86_64-linux-gnu with Zig {toolchain.zig_version(toolchain.find_zig())} in {time.time() - t0:.0f} s, {lib.stat().st_size:,} bytes")
')
SAVAGEWIND_NO_LAUNCHER=1 SAVAGEWIND_DATA_ROOT=/tmp/nx2s-data "${SAVAGEWIND_TEST_RUNTIME:-build/savagewind}" /tmp/nx2s-game >/dev/null 2>&1 || true
if grep -q '\[gamelib\].*0 sites)' /tmp/nx2s-data/savagewind.log; then echo '   the runtime loads game code without game sites'
else echo '   [RED] the runtime did not load it:'; tail -5 /tmp/nx2s-data/savagewind.log; exit 1; fi
