#!/usr/bin/env python3
"""A benchmark of a Steam Deck port on the device, over SSH (docs/BENCHMARKS.md).

The run loads a fixed scene: the game folder's launcher starts with its data folder pointed at a benchmark copy
(XDG_DATA_HOME), whose save is put back from a reference before every run, presses A three times at the title screen
(Continue: the reference's latest save), then measures a window of play without input: the frame rate, the time the
game measures for its frames, the CPU time of its threads (SAVAGEWIND_PROF_THREADS), their instructions per cycle
(perf stat) and the GPU queue thread's CPU time. With --force60 the game runs at 60 whatever its frames take
(SAVAGEWIND_FORCE_60): the frame rate it reaches is what the game code and the runtime sustain.

  python3 devtools/deck_bench.py --device deck@steamdeck.local --set-reference   # once: the player's save and caches as the scene
  python3 devtools/deck_bench.py --device deck@steamdeck.local [--force60] [--runs 2]

The player's own save and caches are only read (by --set-reference). The device must be in Desktop Mode, its screen on.
Standard library only.
"""
import argparse
import datetime
import re
import shlex
import subprocess
import sys

INPUT = '600:A:4,700:A:4,800:A:4'               # the title screen's Continue, then the save it offers

FIND = r'''
B=$HOME/.local/share/nx2steamos-bench   # reference/<title ID>/{save,cache}; run/nx2steamos/<title ID>: the runs' data folder
G=""
for m in "$HOME"/Games/nx2steamos/*/nx2s-game.json; do
  grep -q "\"$T\"" "$m" 2>/dev/null && G=${m%/*} && break
done
[ -n "$G" ] || { echo "no game folder of $T under ~/Games/nx2steamos"; exit 3; }
'''

SET_REFERENCE = FIND + r'''
S=${XDG_DATA_HOME:-$HOME/.local/share}/nx2steamos/$T
R=$B/reference/$T
[ -d "$S/save" ] || { echo "no save in $S"; exit 3; }
rm -rf "$R" && mkdir -p "$R" && cp -a "$S/save" "$R/save" && cp -a "$S/cache" "$R/cache"
echo "reference: $R, from $S (latest save file $(find "$R/save" -type f -printf '%TF %TH:%TM %p\n' | sort | tail -1))"
'''

RUN = FIND + r'''
R=$B/reference/$T; D=$B/run/nx2steamos/$T
[ -d "$R/save" ] || { echo "no reference scene: run deck_bench.py --set-reference first"; exit 3; }
mkdir -p "$D" && rm -rf "$D/save" && cp -a "$R/save" "$D/save"
[ -d "$D/cache" ] || cp -a "$R/cache" "$D/cache"
rm -f "$D/savagewind.log"
export XDG_DATA_HOME=$B/run XDG_RUNTIME_DIR=/run/user/$(id -u) WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-0} DISPLAY=${DISPLAY:-:0}
export SAVAGEWIND_NO_FATAL_DIALOG=1 SAVAGEWIND_INPUT=$INPUT SAVAGEWIND_PROF_THREADS=2 SAVAGEWIND_LOG_FRAME_TIMING=1
[ "$FORCE60" = 1 ] && export SAVAGEWIND_FORCE_60=1
timeout -s INT $((FROM + LEN + 10)) "$G/nx2steamos-run" > /tmp/nx2s-bench.out 2>&1 &
sleep "$FROM"
P=$(pgrep -x savagewind | head -1)
[ -n "$P" ] || { echo "the game is not running"; tail -5 /tmp/nx2s-bench.out; exit 4; }
snap() { for d in /proc/$P/task/*; do printf '%s %s %s\n' "${d##*/}" "$(cut -d' ' -f1 "$d/schedstat")" "$(tr ' ' _ < "$d/comm")"; done; }
snap > /tmp/nx2s-bench.s0
perf stat --per-thread -x, -e cycles:u,instructions:u -p "$P" -- sleep "$LEN" 2> /tmp/nx2s-bench.stat
snap > /tmp/nx2s-bench.s1
wait
echo "=== id"; cat "$G/game/swgame.so.id" 2>/dev/null; echo
echo "=== s0"; cat /tmp/nx2s-bench.s0
echo "=== s1"; cat /tmp/nx2s-bench.s1
echo "=== stat"; cat /tmp/nx2s-bench.stat
echo "=== log"; cat "$D/savagewind.log"
'''


def remote(device, script, env):
    pre = ''.join(f'{k}={shlex.quote(str(v))}\n' for k, v in env.items())
    return subprocess.run(['ssh', device, 'sh -s'], input=(pre + script).encode(), capture_output=True, timeout=900)


def sections(text):
    out, cur = {}, None
    for line in text.splitlines():
        if line.startswith('=== '):
            cur = line[4:].strip(); out[cur] = []
        elif cur:
            out[cur].append(line)
    return out


def measure(s, frm, length):
    log = s.get('log', [])
    vk = [(i, float(m[1]), int(m[2]), float(m[3]), float(m[4])) for i, l in enumerate(log)
          if (m := re.match(r'\[vk\] t=([\d.]+) frame (\d+) ([\d.]+) fps max (\d+)ms', l))]
    win = [v for v in vk if frm <= v[1] <= frm + length]
    if len(win) < 2: raise SystemExit(f'too few frame lines in the window ({len(win)}): did the game reach the scene?')
    first, last = win[0], win[-1]
    frames = last[2] - first[2]
    fps = frames / (last[1] - first[1])
    worst = max(v[4] for v in win)
    lines = log[first[0]:last[0] + 1]
    game = [(float(m[1]), float(m[2])) for l in lines if (m := re.search(r'^\[frame\] .*times ([\d.]+) / [\d.]+ ms average, ([\d.]+) / ', l))]
    main = next((m[1] for l in log if (m := re.match(r'main thread: handle ([0-9a-f]+)', l))), None)
    cpu, n = {}, 0  # (ms a frame, summed over the windows)
    for l in lines:
        if l.startswith('[threads] ') and 'frames;' in l: n += 1
        elif (m := re.match(r'\[threads\]\s+(h[0-9a-f]+) p\d+ c\d+x?:\s+([\d.]+)', l)): cpu[m[1]] = cpu.get(m[1], 0) + float(m[2])
    cyc, ins, name = {}, {}, {}
    for l in s.get('stat', []):
        f = l.split(',')
        if len(f) > 3 and f[1].replace('.', '').isdigit():
            who = f[0].strip(); ev = f[3]
            (cyc if ev.startswith('cycles') else ins if ev.startswith('instructions') else {})[who] = float(f[1])
    guest = [w for w in cyc if re.match(r'h[0-9a-f]+ p\d+-\d+$', w)]
    ipc_all = sum(ins.get(w, 0) for w in guest) / max(1, sum(cyc[w] for w in guest))
    mains = [w for w in guest if main and w.startswith(f'h{main} ')]
    ipc_main = max((ins.get(w, 0) / cyc[w] for w in mains if cyc[w] > 1e8), default=0)
    run = lambda k: {t: (int(ns), c) for t, ns, c in (l.split() for l in s.get(k, []) if len(l.split()) == 3)}
    s0, s1 = run('s0'), run('s1')
    queue_ns = sum(s1[t][0] - s0[t][0] for t in s1 if t in s0 and s1[t][1] == 'nvn_queue')
    return {
        'fps': fps, 'worst_ms': worst,
        'game_ms': sum(g[0] for g in game) / len(game) if game else float('nan'),
        'game_longest_ms': max((g[1] for g in game), default=float('nan')),
        'main_cpu': cpu.get(f'h{main}', 0) / max(n, 1) if main else float('nan'),
        'game_cpu': sum(cpu.values()) / max(n, 1),
        'ipc_main': ipc_main, 'ipc_all': ipc_all,
        'queue_ms': queue_ns / 1e6 / max(frames * length / max(last[1] - first[1], 1e-9), 1),
        'id': (s.get('id') or [''])[0].strip(),
    }


def main(argv):
    ap = argparse.ArgumentParser(description='Benchmark a Steam Deck port on the device (docs/BENCHMARKS.md)')
    ap.add_argument('--device', required=True, help='user@host of the Steam Deck')
    ap.add_argument('--title', default='0100F2C0115B6000', help='the game (title ID); default Tears of the Kingdom')
    ap.add_argument('--set-reference', action='store_true', help="copy the player's save and shader caches as the benchmark's scene")
    ap.add_argument('--force60', action='store_true', help='the game at 60 whatever its frames take (SAVAGEWIND_FORCE_60)')
    ap.add_argument('--from', dest='frm', type=int, default=90, help='seconds after the start the window opens (default 90: the scene is loaded)')
    ap.add_argument('--length', type=int, default=30, help='the window, in seconds (default 30)')
    ap.add_argument('--runs', type=int, default=1)
    ap.add_argument('--label', default='', help='what was measured (a commit, a change)')
    a = ap.parse_args(argv)
    env = {'T': a.title}
    if a.set_reference:
        r = remote(a.device, SET_REFERENCE, env)
        print((r.stdout + r.stderr).decode(errors='replace').strip())
        return r.returncode
    env.update(INPUT=INPUT, FROM=a.frm, LEN=a.length, FORCE60=int(a.force60))
    rows = []
    for i in range(a.runs):
        r = remote(a.device, RUN, env)
        out = r.stdout.decode(errors='replace')
        if r.returncode or '=== log' not in out:
            print(out[-2000:] + r.stderr.decode(errors='replace')[-2000:], file=sys.stderr)
            return r.returncode or 1
        m = measure(sections(out), a.frm, a.length)
        rows.append(m)
        print(f'run {i + 1}: {m["fps"]:.1f} fps (worst frame {m["worst_ms"]:.0f} ms); the game measures {m["game_ms"]:.1f} ms a frame '
              f'(longest {m["game_longest_ms"]:.1f}); main thread {m["main_cpu"]:.1f} ms CPU a frame, game threads {m["game_cpu"]:.1f}; '
              f'IPC {m["ipc_main"]:.2f} main, {m["ipc_all"]:.2f} all; queue thread {m["queue_ms"]:.1f} ms a frame')
    avg = lambda k: sum(x[k] for x in rows) / len(rows)
    if not a.label:  # (the runtime on the device is built from this tree)
        git = lambda *c: subprocess.run(['git', *c], capture_output=True, text=True).stdout.strip()
        a.label = git('rev-parse', '--short', 'HEAD') + (' + changes' if git('status', '--porcelain', '--untracked-files=no') else '')
    mode = 'forced 60' if a.force60 else 'as played'
    print(f'| {datetime.date.today()} | {a.label} | `{rows[0]["id"]}` | {mode} | {avg("fps"):.1f} | '
          f'{avg("game_ms"):.1f} / {max(x["game_longest_ms"] for x in rows):.1f} | {avg("main_cpu"):.1f} | {avg("game_cpu"):.1f} | '
          f'{avg("ipc_main"):.2f} / {avg("ipc_all"):.2f} | {avg("queue_ms"):.1f} |')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
