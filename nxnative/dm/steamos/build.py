#!/usr/bin/env python3
# SteamOS build of the host (nx2steamos), the counterpart of dm/android/build.py: the same host and graphics sources, the Darwin compatibility layer (dm/android/compat),
# the Android port's Vulkan backend (dm/android/gfx_vk_*.c, perf_android.c) and the SteamOS front end and platform (dm/steamos/*.c), for aarch64 Linux with glibc.
#   build.py --game <title ID> [--cc zig|clang] [--zig <zig>] [--glibc 2.38] [--out <dir>] [--jobs N] [--release] [--only-compile] [--keep-going]
# --cc zig (default when a zig is found): Zig's clang, from any machine (a PC, a Mac, the SteamOS device itself), against the glibc version given (2.38: strlcpy,
#   arc4random); --cc clang: a clang that targets aarch64 Linux natively (the arm64 dev container, devtools/container).
# --release: no debug information, symbols stripped, DM_RELEASE defined (the host ignores DM_CTL), outputs in dm/steamos/out/<title ID>-release.
# Output: <out>/nx2s-host, an executable needing glibc alone: SDL3, Vulkan and FFmpeg are opened at run time (sdl_dyn.c, gfx_vk_core.c, av_dyn.c).
# Objects are rebuilt when a source or a header it includes changed (-MMD dependency files), all of them when the flags change.
import argparse, concurrent.futures as cf, hashlib, os, platform, re, shutil, subprocess, sys
from pathlib import Path

DM = Path(__file__).resolve().parent.parent          # dm/
ap = argparse.ArgumentParser()
ap.add_argument('--game', required=True)
ap.add_argument('--cc', choices=('zig', 'clang'), help='the compiler (default: zig when found, else clang)')
ap.add_argument('--zig', default=os.environ.get('ZIG') or shutil.which('zig') or '')
ap.add_argument('--glibc', default='2.38', help='the oldest glibc the host runs on (Zig only)')
ap.add_argument('--out')
ap.add_argument('--jobs', type=int, default=os.cpu_count() or 8)
ap.add_argument('--only-compile', action='store_true', help='compile every source and report errors, no link')
ap.add_argument('--keep-going', action='store_true')
ap.add_argument('-O', default='2')
ap.add_argument('--release', action='store_true', help='a build to keep installed: no debug information, stripped, no DM_CTL')
ap.add_argument('--verbose', action='store_true')
a = ap.parse_args()
cc = a.cc or ('zig' if a.zig and Path(a.zig).exists() else 'clang')
if cc == 'zig' and not (a.zig and Path(a.zig).exists()): sys.exit('build.py: no zig (give --zig or ZIG, or --cc clang on an aarch64 Linux machine)')
if cc == 'zig': CC = [a.zig, 'cc', '-target', f'aarch64-linux-gnu.{a.glibc}']
else:
    CC = [os.environ.get('CC') or 'clang']
    if not (sys.platform.startswith('linux') and platform.machine().lower() in ('aarch64', 'arm64')): CC.append('--target=aarch64-linux-gnu')   # a cross clang needs a sysroot too (--sysroot in CFLAGS)
    CC += os.environ.get('CFLAGS_EXTRA', '').split()
out = Path(a.out) if a.out else DM / 'steamos/out' / (a.game + ('-release' if a.release else ''))
DBG = [] if a.release else ['-g']
obj = out / 'obj'; gen = out / 'gen'; obj.mkdir(parents=True, exist_ok=True); (gen / a.game).mkdir(parents=True, exist_ok=True)

# the game profile's headers: game_profile.h (game.py), the Android layout the overlay code is compiled with (android_profile.h), the SteamOS keys (steamos_profile.h)
for tool, name in (('games/game.py', 'game_profile.h'), ('android/tools/android_profile.py', 'android_profile.h'), ('steamos/tools/steamos_profile.py', 'steamos_profile.h')):
    subprocess.run([sys.executable, str(DM / tool), 'header', a.game, str(gen / a.game / name)], check=True)
# the backend's own shaders, compiled once and committed (tools/gen_shaders.py): a header older than its source stops the build
r = subprocess.run([sys.executable, str(DM / 'steamos/tools/gen_shaders.py'), '--check'], capture_output=True, text=True)
if r.returncode: sys.exit(f'build.py: the overlay shaders changed ({r.stderr.strip() or r.stdout.strip()}): run python3 dm/steamos/tools/gen_shaders.py')

def srcs(*globs): return [p for g in globs for p in sorted(DM.glob(g))]
HOST = [p for p in srcs('host/*.c') if p.name not in ('hle_movie_avc.c',)]   # VideoToolbox: steamos/movie_av.c instead
GFX = srcs('gfx/*.c', 'gfx/nvn/*.c', 'gfx/tex/*.c')
ANDROID_SHARED = [p for p in srcs('android/compat/*.c') if p.name not in ('audiotoolbox_aaudio.c', 'audioconverter_mediacodec.c')] + \
                 [DM / 'android' / n for n in ('gfx_common.c', 'gfx_vk_core.c', 'gfx_vk_exec.c', 'gfx_vk_present.c', 'gfx_vk_shader.c', 'gfx_vk_tex.c', 'perf_android.c')]
STEAMOS = srcs('steamos/*.c')
ASM = srcs('host/entry.S', 'gfx/nvn/nvn_entry.S', 'gfx/nvn/nvn_thunks.S')
VPX = srcs('third_party/libvpx/**/*.c')

INC = ['-include', str(DM / 'android/compat/include/darwin_compat.h'), f'-I{DM / "steamos/compat/include"}', f'-I{DM / "android/compat/include"}', f'-I{DM / "host"}',
       f'-I{DM / "gfx/nvn"}', f'-I{DM / "gfx/tex"}', f'-I{DM / "gfx/nvn/include"}', f'-I{DM / "third_party/vulkan-headers/include"}', f'-I{gen / a.game}',
       f'-I{DM / "third_party/libvpx"}', f'-I{DM / "third_party/stb_vorbis"}', f'-I{DM / "android"}', f'-I{DM / "steamos"}', f'-I{DM / "steamos/gen"}',
       f'-I{DM / "steamos/third_party/SDL3/include"}']
CFLAGS = [f'-O{a.O}'] + DBG + ['-fPIE', '-fno-omit-frame-pointer', '-ffixed-x18', '-fno-sanitize=all', '-Wall', '-Wno-unused-function', '-Wno-unused-variable', '-Wno-unused-but-set-variable',
          '-Wno-format', '-Wno-deprecated-declarations', '-Wno-gnu-folding-constant', '-Werror=implicit-function-declaration', '-Werror=return-type',
          '-Werror=incompatible-pointer-types', '-Werror=int-conversion', '-D_GNU_SOURCE', '-DDM_STEAMOS=1', '-fno-strict-aliasing', '-fvisibility=hidden'] + (['-DDM_RELEASE=1'] if a.release else [])
VPX_FLAGS = ['-O2'] + DBG + ['-fPIE', '-fno-sanitize=all', f'-I{DM / "third_party/libvpx"}', f'-I{DM / "third_party/libvpx/config"}', '-w']

_stamp = obj / 'flags.stamp'; _flags = hashlib.sha256(repr((CFLAGS, INC, VPX_FLAGS, CC)).encode()).hexdigest()
if not _stamp.exists() or _stamp.read_text() != _flags:   # compiler flags changed: every object is rebuilt
    shutil.rmtree(obj, ignore_errors=True); obj.mkdir(parents=True, exist_ok=True); _stamp.write_text(_flags)

def dep_stale(o, d, s):
    if not o.exists() or o.stat().st_mtime < s.stat().st_mtime: return True
    if not d.exists(): return True
    t = o.stat().st_mtime
    txt = d.read_text(errors='replace').replace('\\\n', ' ')
    for f in re.split(r'(?<!\\)\s+', txt.split(':', 1)[1] if ':' in txt else ''):
        f = f.replace('\\ ', ' ').strip()
        if f and Path(f).exists() and Path(f).stat().st_mtime > t: return True
    return False

def compile_one(s):
    rel = s.relative_to(DM); o = obj / (str(rel).replace(os.sep, '_').replace('/', '_') + '.o'); d = o.with_suffix('.d')
    src = s
    if s.suffix == '.S':   # Mach-O assembly → ELF (dm/android/tools/darwin2elf.py, as the Android build)
        src = gen / (s.stem + '.elf.S')
        if not src.exists() or src.stat().st_mtime < s.stat().st_mtime:
            subprocess.run([sys.executable, str(DM / 'android/tools/darwin2elf.py'), str(s), str(src)], check=True)
    if not dep_stale(o, d, src): return s, o, 0, ''
    is_vpx = 'third_party/libvpx' in str(rel).replace(os.sep, '/')
    flags = VPX_FLAGS if is_vpx else DBG + ['-fno-sanitize=all'] if s.suffix == '.S' else CFLAGS + INC + (['-Dmain=dm_host_main'] if rel.as_posix() == 'host/main.c' else [])
    r = subprocess.run(CC + flags + ['-MMD', '-MF', str(d), '-c', str(src), '-o', str(o)], capture_output=True, text=True, errors='replace')
    return s, o, r.returncode, r.stderr

def build(files):
    objs, failed = [], []
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        for s, o, rc, err in ex.map(compile_one, files):
            objs.append(o)
            if rc: failed.append(s); print(f'--- {s.relative_to(DM)}\n{err.strip()}\n', file=sys.stderr)
            elif err.strip() and a.verbose: print(err, file=sys.stderr)
    return objs, failed

all_files = HOST + GFX + ANDROID_SHARED + STEAMOS + ASM
objs, failed = build(all_files)
vobjs, vfailed = build(VPX)
failed += vfailed
print(f'compiled {len(all_files) + len(VPX)} sources with {cc}, {len(failed)} failed' + (': ' + ', '.join(str(f.relative_to(DM)) for f in failed) if failed else ''))
if failed and not a.keep_going: sys.exit(1)
if a.only_compile: sys.exit(1 if failed else 0)

LIBS = ['-lm', '-ldl', '-lpthread']
# no section garbage collection: the host finds its tables between __start_ / __stop_ section symbols (darwin_compat.c getsectiondata), so nothing may drop them
LDFLAGS = ['-pie', '-Wl,--build-id', '-Wl,-z,max-page-size=16384', '-Wl,--no-gc-sections'] + ([] if cc == 'zig' else ['-Wl,-z,nostart-stop-gc']) + (['-s'] if a.release else [])
exe = out / 'nx2s-host'
r = subprocess.run(CC + LDFLAGS + ['-o', str(exe)] + [str(o) for o in objs + vobjs] + LIBS, capture_output=True, text=True, errors='replace')
if r.returncode: print(r.stderr.strip(), file=sys.stderr); sys.exit(1)
print(f'linked {exe} ({exe.stat().st_size / 2**20:.1f} MB)')
