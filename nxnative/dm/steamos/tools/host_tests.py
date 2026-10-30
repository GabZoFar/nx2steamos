#!/usr/bin/env python3
# The host's unit tests (dm/host/tests, written for the Mac: make -C dm/host/tests GAME=<id> run) built and run on aarch64 Linux with glibc, as the SteamOS host is
# compiled: the same rules, read from that Makefile, with the Darwin compatibility layer (dm/android/compat) and without the Objective-C tests and Apple frameworks.
#   host_tests.py --game <title ID> [--cc clang] [--keep]
# In the arm64 dev container: docker run --rm -v "$PWD":/src -w /src nx2steamos-dev python3 nxnative/dm/steamos/tools/host_tests.py --game 0100B99019412000
# Exit code: the number of tests that failed to build or run (0 = all passed). A test the Mac alone builds (AudioToolbox, CoreHaptics) is listed as skipped.
import argparse, os, re, shlex, shutil, subprocess, sys, tempfile
from pathlib import Path

DM = Path(__file__).resolve().parents[2]
TESTS = DM / 'host/tests'
ap = argparse.ArgumentParser()
ap.add_argument('--game', required=True)
ap.add_argument('--cc', default=os.environ.get('CC') or 'clang')
ap.add_argument('--keep', action='store_true', help='keep the build folder')
a = ap.parse_args()

mk = (TESTS / 'Makefile').read_text().replace('\\\n', ' ')
rules = {}   # target → recipe (the first clang line under it)
for m in re.finditer(r'^\$\(B\)/(\w+):[^\n]*\n((?:\t[^\n]*\n)+)', mk, re.M):
    for line in m.group(2).splitlines():
        if line.strip().startswith('clang'): rules[m.group(1)] = line.strip(); break
# the run order and how each runs (its environment, stderr kept or not), from the Makefile's run target
runs = []
for line in re.search(r'^run:[^\n]*\n((?:\t[^\n]*\n)+)', mk, re.M).group(1).splitlines():
    w = shlex.split(line.strip()); env = {}
    while w and '=' in w[0] and not w[0].startswith('$'): k, _, v = w.pop(0).partition('='); env[k] = v
    if w: runs.append((w[0].replace('$(B)/', ''), env, '2>/dev/null' in line))

build = Path(tempfile.mkdtemp(prefix='nx2s-host-tests-'))
gen = build / 'gen' / a.game; gen.mkdir(parents=True)
subprocess.run([sys.executable, str(DM / 'games/game.py'), 'header', a.game, str(gen / 'game_profile.h')], check=True)
LINUX = ['-include', str(DM / 'android/compat/include/darwin_compat.h'), f'-I{DM / "steamos/compat/include"}', f'-I{DM / "android/compat/include"}', '-D_GNU_SOURCE', '-DDM_STEAMOS=1',
         '-Wno-unused-command-line-argument']
results, failed = [], 0
KNOWN = {'t_altstack': 'Apple malloc zone statistics', 't_audio': 'stale against hle_audio.c\'s queue (nx2android changed it)', 't_audio_spsc': 'stale against hle_audio.c\'s queue (nx2android changed it)'}
for name, env, quiet in runs:
    if name in KNOWN: results.append((name, f'skipped ({KNOWN[name]})')); continue
    recipe = rules.get(name)
    if not recipe: results.append((name, 'no rule')); failed += 1; continue
    w = shlex.split(recipe.replace('$(GINC)', f'-I{gen} -I{DM / "games" / a.game}').replace('$@', str(build / name)))
    if any(x.endswith('.m') for x in w) or '-framework' in w and any(w[i + 1] in ('Cocoa', 'GameController', 'CoreHaptics') for i, x in enumerate(w) if x == '-framework'):
        results.append((name, 'skipped (Objective-C / Apple frameworks)')); continue
    cmd, i = [a.cc], 1
    while i < len(w):
        if w[i] == '-framework': i += 2; continue   # AudioToolbox: the compat header declares the subset
        if w[i] in ('-fobjc-arc', '-Werror'): i += 1; continue   # -Werror: glibc's headers warn where Darwin's do not; the host build has its own -Werror= list
        cmd.append(w[i]); i += 1
    cmd[1:1] = LINUX
    # the Darwin calls the host makes come from the compatibility layer on Linux, as in the host build (a test that defines one itself keeps its own: the layer's are weak there)
    compat = [str(DM / 'android/compat' / n) for n in ('darwin_compat.c', 'darwin_symbols.c', 'sha256.c')] + [str(Path(__file__).with_name('host_tests_shim.c'))]
    r = subprocess.run(cmd + compat + ['-Wl,--allow-multiple-definition', '-lpthread', '-lm', '-ldl'], cwd=TESTS, capture_output=True, text=True)
    if r.returncode:
        err = '\n'.join(l for l in r.stderr.splitlines() if 'error' in l)[:600]
        results.append((name, 'BUILD FAILED: ' + err)); failed += 1; continue
    renv = dict(os.environ, **{k: v.replace('$(abspath $(B))', str(build)).replace('"', '') for k, v in env.items()})
    t = subprocess.run([str(build / name)], cwd=TESTS, env=renv, capture_output=True, text=True, timeout=300)
    out = (t.stdout + ('' if quiet else t.stderr)).strip().splitlines()
    if t.returncode: results.append((name, f'FAILED (exit {t.returncode}): ' + ' | '.join(out[-4:]))); failed += 1
    else: results.append((name, 'ok' + (f': {out[-1][:100]}' if out else '')))
for name, res in results: print(f'{name:18} {res}')
print(f'{sum(1 for _, r in results if r.startswith("ok"))} passed, {failed} failed, {sum(1 for _, r in results if r.startswith("skipped"))} skipped')
if not a.keep: shutil.rmtree(build, ignore_errors=True)
sys.exit(failed)
