# The Steam Deck engine (recomp/, Savage Wind Recomp: docs/RECOMP.md) driven by the porter: `porter port --engine recomp`. The Deck's CPU is x86-64, so the game's Arm code
# is recompiled ahead of time, on the player's PC, Mac or the Deck itself: the player's files are extracted (nxnative's nx_extract, as for the ARM64 engine), Savage Wind's
# generator turns each module into C (recomp/tools/gen.py) and Zig compiles that for x86_64-linux-gnu into the game code library, swgame.so (the steps of the start screen's
# own build, recomp/runtime/gamebuild.c). The library is built with the id of this checkout's runtime
# (gamelib_id(): recomp/Makefile's GAMELIB_ID, a hash of the generator and the headers the game code is compiled against).
# What the engine runs is its own knowledge, by the main module's build ID (recomp/runtime/game_versions.h); what nx2steamos says about a game on it is the game profile's
# deck.tsv (status, tested) beside its game.tsv in nxnative/dm/games/<title ID>/.
import hashlib, os, re, shutil, subprocess, sys, time
from collections import namedtuple
from dataclasses import dataclass
from pathlib import Path

from porter import artwork, config, gamedir, install, toolchain
from porter.paths import NXNATIVE, REPO, player_tools
from porter.upstream import BuildError, PROG, game, nacp, nsp, say, steamos_profile

PY = sys.executable
RECOMP = REPO / 'recomp'
TOOLS = RECOMP / 'tools'
HLE = RECOMP / 'runtime/hle/hle_symbols.txt'
DECK_TSV = 'deck.tsv'
ARCHES = ('x86_64', 'amd64')
ZIG_TARGET = 'x86_64-linux-gnu.2.35'   # the AppImage's own base (Ubuntu 22.04): it runs on glibc 2.35 or later
MIN_GLIBC = (2, 35)
TARGET = 'x86-64-v3'                   # AVX2, FMA, BMI2: the Deck's Zen 2 has them (recomp/runtime/gamelib.h; the compatible target, x86-64-v2, is the start screen's to build)
# The generator's Python modules, as Savage Wind's own helper freezes them (recomp/tools/linux/gen_helper.sh): others work, but their output is not the one Savage Wind tests
GEN_MODULES = {'capstone': '5.0.9', 'unicorn': '2.1.4', 'lz4': '4.4.5'}

# GAMELIB_ID's inputs as recomp/Makefile has them for x86-64 Linux: GAMELIB_SRCS, then GENFLAGS and GENFLAGS_ZIG as make expands them (whitespace included: the
# GENARCH values keep the spaces before their comments). devtools/check.sh deck compares gamelib_id() with make's
GAMELIB_SRCS = ('tools/gen.py', 'tools/lifter.py', 'tools/analyze.py', 'tools/nso.py', 'tools/passes.py', 'tools/patches.py', 'tools/simd_c.py', 'tools/gamelib_entry.c',
                'runtime/hle/hle_symbols.txt', 'runtime/cpu.h', 'runtime/simd.h', 'runtime/simd_fp.h', 'runtime/simd_softfma.h', 'runtime/gamelib.h',
                'tools/layout.py', 'tools/game_sites.py')
GENFLAGS = '-O1 -fno-strict-aliasing -Wno-return-type -Wno-unused-label -fvisibility=hidden -Iruntime -march={arch}   -fPIC'
GENFLAGS_ZIG = '-Xclang -O1 -fno-vectorize -fno-slp-vectorize -g0 -fomit-frame-pointer'


@dataclass(frozen=True)
class Runtime:
    """A Savage Wind release whose x86-64 AppImage game folders can carry: its runtime loads game code libraries with gamelib_id / gamelib_id_compat"""
    version: str
    url: str
    size: int
    sha256: str
    gamelib_id: str
    gamelib_id_compat: str

    @property
    def name(self): return self.url.rsplit('/', 1)[1]


# The ids were read from each AppImage's savagewind (2026-10-29): 0.3.0-alpha.3 was built from 5173d27, whose generator and headers are recomp/'s (860f372 changed
# runtime/nvn/vulkan/device.c, a Windows fix, and the start screen's version line since)
RUNTIMES = (
    Runtime('0.3.0-alpha.3', 'https://github.com/Revivedx/SavageWindRecomp/releases/download/v0.3.0-alpha.3/Savage_Wind-0.3.0-alpha.3-x86_64.AppImage', 85215736,
            'addf70100c95d99ce618b3a1e97690b0b5b4ff9b65b72e4c374c6bb618f491ac', 'f0cd698371c0a703', 'ff06ab652b335fb4'),
)
LICENSE = 'https://github.com/Revivedx/SavageWindRecomp/blob/main/LICENSE'

GameVersion = namedtuple('GameVersion', 'name build_id supported romfs_files romfs_marker sites', defaults=('System/Version.txt', True))


def gamelib_id(target=TARGET, root=RECOMP):
    """The id of a game code library built from root's generator and headers for target (recomp/Makefile GAMELIB_ID; GAMELIB_ID_COMPAT for x86-64-v2)"""
    h = hashlib.sha256()
    for s in GAMELIB_SRCS: h.update((root / s).read_bytes())
    h.update(f'{GENFLAGS.format(arch=target)}\n{GENFLAGS_ZIG}\n'.encode())
    return h.hexdigest()[:16]


def zig_cflags(target=TARGET):
    """What the start screen compiles the game code with when its compiler is zig cc (runtime/gamebuild.c add_cflags): GENFLAGS without -Iruntime, -march as zig's
    -mcpu, then GENFLAGS_ZIG (its last -O is the one clang takes)"""
    flags = [f for f in GENFLAGS.format(arch=target).split() if f != '-Iruntime']
    return ['-mcpu=' + f[7:].replace('-', '_') if f.startswith('-march=') else f for f in flags] + GENFLAGS_ZIG.split()


def runtime_for(gid):
    return next((r for r in RUNTIMES if gid in (r.gamelib_id, r.gamelib_id_compat)), None)


def game_versions(path=RECOMP / 'runtime/game_versions.h'):
    """The game versions the engine knows: [GameVersion(name, main build id (8 bytes, hex), supported, RomFS files)] (X(version, build id, supported, RomFS files) rows)"""
    text = path.read_text(encoding='utf-8')
    return [GameVersion(n, b, s != '0', int(f), marker, sites != '0') for n, b, s, f, marker, sites in
            re.findall(r'X\(\s*"([^"]+)"\s*,\s*"([0-9a-f]{16})"\s*,\s*(\d)\s*,\s*(\d+)\s*,\s*"([^"]+)"\s*,\s*(\d)\s*\)', text)]


def romfs_version(romfs):   # System/Version.txt, trimmed (a byte order mark, spaces, line ends), as the start screen reads it (runtime/ui/launcher.mm check_romfs)
    try: b = (Path(romfs) / 'System/Version.txt').read_bytes()[:63]
    except OSError: return ''
    return b.decode('utf-8', errors='replace').removeprefix('﻿').strip()


def check_build(exefs, romfs, versions=None):
    """The GameVersion of extracted game files, checked as Savage Wind's setup checks a folder (runtime/ui/launcher.mm): a main module of a supported version, and its
    RomFS with the update applied (it says that version in System/Version.txt and has at least 95% of the version's files). BuildError says what is wrong"""
    versions = game_versions() if versions is None else versions
    known = ', '.join(v.name for v in versions if v.supported)
    try: bid = nsp.build_id(Path(exefs) / 'main')[:16]
    except OSError: raise BuildError(f'no main module in {exefs}') from None
    v = next((v for v in versions if v.build_id == bid), None)
    if not v: raise BuildError(f'this build of the game (main {bid}) is not one the Steam Deck engine knows: it runs version {known} (recomp/runtime/game_versions.h)')
    if not v.supported: raise BuildError(f'version {v.name} (main {bid}) is known to the Steam Deck engine but not supported yet: it runs version {known}')
    if v.romfs_marker == 'System/Version.txt':
        found = romfs_version(romfs)
        if found != v.name:
            raise BuildError(f'the RomFS is not version {v.name} with the update applied: its System/Version.txt says {found!r}' if found else
                             f'the RomFS has no System/Version.txt: it is not version {v.name}\'s')
    elif not (Path(romfs) / v.romfs_marker).is_file():
        raise BuildError(f'the RomFS has no {v.romfs_marker}: it is not version {v.name}\'s')
    if v.romfs_files:
        n = sum(len(fs) for _, _, fs in os.walk(romfs))
        if n < int(v.romfs_files * 0.95): raise BuildError(f'the RomFS has {n:,} files; version {v.name} with the update applied has {v.romfs_files:,}')
    return v


# ---- the game profile's Steam Deck keys ----
class DeckProfile:
    """deck.tsv (key <TAB> value, # comments): status, tested (<version> <device>), sites (yes for game-specific patches, no for a site-free build), and env
    (NAME=VALUE, repeatable: a runtime setting the folder's launcher exports, e.g. SAVAGEWIND_NO_SCALE=1)."""
    KEYS = ('status', 'tested', 'sites', 'env')
    ENV_NAME = re.compile(r'[A-Z_][A-Z0-9_]*')
    ENV_VALUE = re.compile(r'[A-Za-z0-9_.,:/=+-]*')   # (written between single quotes in the launcher as it is)

    def __init__(self, g):
        self.game, self.id, self.path = g, g.id, g.dir / DECK_TSV
        rows, self.env = {}, []
        for n, line in enumerate(self.path.read_text(encoding='utf-8').splitlines(), 1):
            if not line.strip() or line.lstrip().startswith('#'): continue
            k, _, v = line.partition('\t'); k, v = k.strip(), v.strip()
            if k not in self.KEYS: raise steamos_profile.ProfileError(f'{self.path}:{n}: unknown key {k!r} (keys: {", ".join(self.KEYS)})')
            if k == 'env':
                name, eq, value = v.partition('=')
                if not eq or not self.ENV_NAME.fullmatch(name) or not self.ENV_VALUE.fullmatch(value):
                    raise steamos_profile.ProfileError(f'{self.path}:{n}: env {v!r}: NAME=VALUE (NAME in capitals, VALUE letters, digits and _.,:/=+-)')
                self.env.append((name, value))
                continue
            if k in rows: raise steamos_profile.ProfileError(f'{self.path}:{n}: {k} given twice')
            rows[k] = v
        self.status, self.tested = rows.get('status', 'experimental'), rows.get('tested', '')
        if rows.get('sites', 'yes') not in ('yes', 'no'): raise steamos_profile.ProfileError(f'{self.path}: sites must be yes or no')
        self.sites = rows.get('sites', 'yes') == 'yes'
        if self.status not in steamos_profile.STATUS: raise steamos_profile.ProfileError(f'{self.path}: status {self.status!r}: one of {", ".join(steamos_profile.STATUS)}')
        if self.status in ('playable', 'wip') and not self.tested: raise steamos_profile.ProfileError(f'{self.path}: status {self.status} needs a tested row')

    @property
    def name(self): return self.game.get('name', self.id)

    def as_dict(self): return {'id': self.id, 'name': self.name, 'status': self.status, 'tested': self.tested}


def profile(g):   # the Steam Deck profile of a game profile, None without a deck.tsv
    return DeckProfile(g) if g and (g.dir / DECK_TSV).is_file() else None


def all_profiles():
    return [p for p in (profile(g) for g in game.all_games()) if p]


# ---- the runtime ----
def find_runtime(cfg=None):
    """(AppImage, Runtime) for game folders: the configured one (`porter config savagewind <AppImage>`: one built from this repository's recomp/ with `make appimage`,
    its Runtime None: its ids are not known here), else the release RUNTIMES pairs with recomp/'s generator, in the player's tools folder (`porter tools --download`);
    (None, that Runtime or None) when there is none to use"""
    cfg = cfg if cfg is not None else config.load()
    if cfg.get('savagewind'): return (Path(cfg['savagewind']) if Path(cfg['savagewind']).is_file() else None), None
    try: rt = runtime_for(gamelib_id())
    except OSError: return None, None   # (no recomp/ beside the porter)
    p = player_tools() / 'savagewind' / rt.name if rt else None
    return (p if p and p.is_file() else None), rt


def runtime_fix(rt):   # what to do when there is no runtime to put in game folders
    if rt: return f'porter tools --download --only savagewind (Savage Wind {rt.version}, {rt.size / 2**20:.0f} MB from GitHub), or porter config savagewind <its AppImage>'
    return ('no Savage Wind release runs game code from this repository\'s recomp/ (it was changed): build its AppImage (cd recomp && make appimage) and '
            'porter config savagewind <build/dist/Savage_Wind-…-x86_64.AppImage>')


def module_versions():   # {'capstone': '5.0.7', ...}: the generator's modules in this Python ('' when missing)
    import importlib.metadata as md
    out = {}
    for m in GEN_MODULES:
        try: out[m] = md.version(m)
        except md.PackageNotFoundError: out[m] = ''
    return out


# ---- the game code library ----
def _gen_progress(log):   # (functions written, all, chunk files complete) from a generator's log ('<module>: progress <ci>/<n> files <k>')
    try:
        with open(log, 'rb') as f:
            f.seek(0, 2); f.seek(max(0, f.tell() - 65536)); tail = f.read().decode(errors='replace')
    except OSError: return 0, 0, 0
    m = re.findall(r': progress (\d+)/(\d+) files (\d+)', tail)
    return tuple(map(int, m[-1])) if m else (0, 0, 0)


def read_layout(path):   # [(module, image size)] of layout.txt, in load order
    out = []
    for line in Path(path).read_text().splitlines():
        p = line.split()
        if len(p) >= 3 and not line.startswith('#'): out.append((p[0], int(p[2], 16)))
    return out


def build_game_code(exefs, out, *, zig, jobs, env, logs, target=TARGET, sites=True, progress=None):
    """<out>/swgame.so and swgame.so.id from the modules in exefs: the module layout, Savage Wind's generator for every module at once, each chunk of C compiled with
    zig cc for x86_64-linux-gnu as soon as it is written, then the link (runtime/gamebuild.c's steps, with the porter's Python and Zig). sites=False: no game sites
    (the made-up modules of the tests). progress(fraction, step). The generated C is the game's code translated: it is deleted as it is compiled, and its folder at the end"""
    exefs, out = Path(exefs), Path(out)
    work = out / 'gamebuild'; nsp.rmtree(work); work.mkdir(parents=True); logs.mkdir(parents=True, exist_ok=True)
    zc = str(work / 'zig-cache')   # zig's caches in the work folder too: hundreds of MB, used once
    env = dict(env, PYTHONHASHSEED='0', ZIG_GLOBAL_CACHE_DIR=zc, ZIG_LOCAL_CACHE_DIR=zc)   # (Savage Wind freezes its generator with hash_seed=0: the same output every run)
    gid, ns = gamelib_id(target), ([] if sites else ['--no-sites'])
    progress = progress or (lambda f, step: None)
    progress(0, 'the module layout')
    nsp.run([PY, TOOLS / 'gen.py', 'layout', exefs, work] + ns, logs / 'deck_gen_layout.log', env=env)
    mods = read_layout(work / 'layout.txt')
    total = sum(s for _, s in mods) or 1
    gens, procs, files, running = {}, [], [], []
    units, queued_b = [], 0   # (source, object, delete the source once compiled); the bytes of C queued so far

    def queue(src, obj, rm):
        nonlocal queued_b
        units.append((src, obj, rm)); queued_b += src.stat().st_size

    try:
        for name, size in mods:
            f = open(logs / f'deck_gen_{name}.log', 'w'); files.append(f)
            cmd = [PY, TOOLS / 'gen.py', name, exefs / name, work / 'layout.txt', work / name, '--simd', 'c'] + (['--hle', HLE] if name != 'main' else []) + ns
            p = subprocess.Popen([str(c) for c in cmd], stdout=f, stderr=subprocess.STDOUT, env=env, cwd=TOOLS); procs.append(p)
            gens[name] = {'p': p, 'log': logs / f'deck_gen_{name}.log', 'w': size / total, 'queued': 0, 'exit': None}
        queue(TOOLS / 'gamelib_entry.c', work / 'gamelib_entry.o', False); queue(work / 'layout.c', work / 'layout.o', True)
        nxt, done_b = 0, 0
        cmd0 = [str(zig), 'cc', '-target', ZIG_TARGET] + zig_cflags(target) + [f'-I{RECOMP / "runtime"}', f'-DGAMELIB_ID="{gid}"']
        while True:
            frac = 0.0
            for name, g in gens.items():
                ended = g['exit'] is None and g['p'].poll() is not None   # (its exit seen before its log is read: that read is the last one)
                if ended:
                    g['exit'] = g['p'].returncode
                    if g['exit']: raise BuildError(f'the generator failed on {name} (see {g["log"]})')
                ci, n, k = _gen_progress(g['log'])
                for i in range(g['queued'], k): queue(work / name / f'{name}_{i:03d}.c', work / f'{name}_{i:03d}.o', True)   # the chunk files complete so far
                g['queued'] = max(g['queued'], k)
                if ended: queue(work / name / f'{name}_table.c', work / f'{name}_table.o', True)
                frac += g['w'] * (1 if g['exit'] == 0 else ci / n if n else 0)
            while len(running) < max(1, jobs) and nxt < len(units):
                src, obj, rm = units[nxt]; nxt += 1
                lf = open(work / (obj.name + '.log'), 'w')
                running.append((subprocess.Popen(cmd0 + ['-c', str(src), '-o', str(obj)], stdout=lf, stderr=subprocess.STDOUT, env=env), lf, src, obj, rm))
            for item in list(running):
                p, lf, src, obj, rm = item
                if p.poll() is None: continue
                lf.close(); running.remove(item)
                if p.returncode:
                    shutil.copy2(work / (obj.name + '.log'), logs / 'deck_compile_failed.log')
                    raise BuildError(f'{src.name} did not compile (see {logs / "deck_compile_failed.log"})')
                done_b += src.stat().st_size
                if rm: src.unlink()
            gens_done = all(g['exit'] == 0 for g in gens.values())
            if gens_done and nxt == len(units) and not running: break
            # as the start screen's bar (runtime/gamebuild.c): the generator's share by functions written, the compiler's by bytes of C compiled, of an estimate of all
            # of it while the generator still writes
            est = queued_b if gens_done else queued_b / frac if frac > 0.02 else 0
            progress(min(0.2 * frac + 0.75 * (min(done_b / est, 1) if est else 0), 0.95), 'compiling' if gens_done else 'generating and compiling')
            time.sleep(0.05)
    finally:
        for p in procs + [r[0] for r in running]:
            if p.poll() is None: p.kill(); p.wait()
        for f in files + [r[1] for r in running]: f.close()
    progress(0.96, 'linking')
    rsp = work / 'link.rsp'
    rsp.write_text(''.join(f'"{o.as_posix()}"\n' for _, o, _ in units), encoding='utf-8')
    lib, tmp = out / 'swgame.so', work / 'swgame.so'
    nsp.run([zig, 'cc', '-target', ZIG_TARGET, '-shared', '-o', tmp, f'@{rsp}'], logs / 'deck_link.log', env=env)
    nsp.rmtree(lib); shutil.move(str(tmp), lib)
    (out / 'swgame.so.id').write_text(gid + '\n', encoding='utf-8')
    nsp.rmtree(work)
    progress(1, 'linked')
    return lib


# ---- the port ----
class DeckPort:
    """`porter port --engine recomp`: a game folder for the Steam Deck (porter/gamedir.py build_deck), installed and put in Steam's library as the ARM64 engine's are"""

    def __init__(self, opts):
        self.o, self.cfg, self.result = opts, config.load(), {}

    def run(self):
        o = self.o
        if o.experimental: raise BuildError('--experimental is for the ARM64 engine: the Steam Deck engine runs the games and versions it was brought up on (porter profiles --deck)')
        if o.fps60: raise BuildError('--fps60 is for the ARM64 engine: the Steam Deck engine has its own frame-rate setting (its Settings: start the game with the launch option --config)')
        o.folder = Path(o.folder).expanduser().resolve()
        if not o.folder.is_dir(): raise BuildError(f'not a folder: {o.folder}')
        keys = o.keys or self.cfg.get('keys')
        if not keys: raise BuildError('no prod.keys: give -k / --keys, or set it once with `porter config keys <path>`')
        o.keys = Path(keys).expanduser().resolve()
        o.device = o.device or self.cfg.get('device') or ''
        o.device_dir = o.device_dir or self.cfg.get('device_dir') or ''
        if o.install and not o.device and not toolchain.local_steamos(ARCHES):
            raise BuildError('--install needs the Steam Deck: run the port on it, or give --device user@host (porter config device user@host)')
        try: self.tools = toolchain.require(self.cfg, str(o.keys), remote=o.install and bool(o.device), engine='recomp')
        except toolchain.ToolError as e: raise BuildError(str(e)) from e
        self.appimage, self.runtime = find_runtime(self.cfg)
        if not self.appimage: raise BuildError(f'no Savage Wind runtime to put in the game folder: {runtime_fix(self.runtime)}')
        if self.runtime: self.check_runtime()
        else: say(f'[RED] runtime {self.appimage}: an AppImage of your own; its game code id must be {gamelib_id()} (built from this repository\'s recomp/), or it will not load the game code')
        if o.language: say('the Steam Deck engine sets the game language in its own settings (start the game with the launch option --config): -l is left out')
        o.out = Path(o.out or self.cfg.get('out') or o.folder).expanduser().resolve()
        os.environ['PATH'] = os.pathsep.join([str(self.tools['dotnet'].parent), os.environ.get('PATH', '')])   # (as porter/pipeline.py: nx_extract is built with `dotnet`)
        os.environ['DOTNET_ROOT'] = str(self.tools['dotnet'].parent)
        self.env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1', DOTNET_CLI_TELEMETRY_OPTOUT='1')
        self.inputs()
        if self.runtime and not self.dp.sites:
            raise BuildError('this game needs the runtime built from recomp/: Savage Wind\'s release requires Breath of the Wild sites; '
                             'build its AppImage (cd recomp && make appimage) and set porter config savagewind <AppImage>')
        PROG.plan(5 + bool(o.install))   # nx_extract, the extraction, game code, art, folder, install
        self.extract()
        self.game_code()
        self.art()
        self.folder()
        if o.install: self.result['install'] = install.install(self.dst, device=o.device, device_dir=o.device_dir, steam=o.steam, restart_steam=o.restart_steam,
                                                               account=self.cfg.get('steam_user') or '', ssh=self.tools['ssh'], rsync=self.tools['rsync'])
        nsp.rmtree(self.work / 'tmp')
        self.result.update(title_id=self.g.id, name=self.app_name, version=self.pkg_ver, display=self.display, engine='recomp', engine_version=self.ver.name,
                           status=self.dp.status, derived=False, installed=bool(o.install))
        say(f'done: {self.result["folder"]}')
        if self.dp.status in ('untested', 'experimental'): say(f'[RED] {self.app_name} is {self.dp.status} on the Steam Deck engine: please report how it runs (docs/PORT_STATUS.md)')
        return self.result

    def check_runtime(self):   # the downloaded AppImage is the release's (a file changed since the download would run other code than the one paired with recomp/)
        h = hashlib.sha256()
        with open(self.appimage, 'rb') as f:
            while b := f.read(1 << 20): h.update(b)
        if h.hexdigest() != self.runtime.sha256: raise BuildError(f'{self.appimage} is not Savage Wind {self.runtime.version}\'s AppImage (its SHA-256 differs): download it again ({runtime_fix(self.runtime)})')

    def inputs(self):
        o = self.o
        say(f'game folder: {o.folder}')
        self.app, self.fam = nsp.scan_nsps(o.folder)
        tid = f'{self.app:016X}'
        self.work = o.folder / f'{tid}_build'
        self.g = game.by_title(self.app)
        self.dp = profile(self.g)
        if not self.dp:
            plays = ', '.join(p.name for p in all_profiles()) or 'no game yet'
            raise BuildError(f'{self.g.get("name", tid) if self.g else tid} does not run on the Steam Deck engine yet (it plays: {plays}); docs/RECOMP.md says what a new game needs')
        say(f'Steam Deck engine: {self.dp.name} ({self.dp.status}' + (f', tested on {self.dp.tested}' if self.dp.tested else '') + ')')
        builds = self.g.builds()
        self.have = nsp.nsp_versions(self.fam, builds)
        self.pkg_ver = o.version or max(self.have, key=nsp.vnum)
        if self.pkg_ver not in self.have: raise BuildError(f'the version to pack ({self.pkg_ver}) is not in the folder, which holds {", ".join(sorted(self.have, key=nsp.vnum))}')
        disp, runs = builds.get(self.pkg_ver, {}).get('display'), [v.name for v in game_versions() if v.supported]
        if disp and disp not in runs: raise BuildError(f'{self.pkg_ver} is version {disp}: the Steam Deck engine runs version {", ".join(runs)} (the update NSP of one of them, beside the base game)')
        if self.fam['dlc']: say(f'{len(self.fam["dlc"])} DLC NSP(s) left out: the Steam Deck engine runs the game without DLC (recomp/runtime/services/misc.c, aoc:u)')
        self.sw = self.work / 'deck'; self.logs = self.work / 'logs'
        for d in (self.sw, self.logs, self.work / 'tmp'): d.mkdir(parents=True, exist_ok=True)
        # the build folder: the extraction (twice the game files at most while it runs), the game code (~1 GB while it builds); the device builds romfs.bin itself
        game_b = sum(p.stat().st_size for p in (self.fam['base'], self.have[self.pkg_ver]) if p)
        if not (self.work / 'extract' / self.pkg_ver / 'ExeFS/main').exists() and nsp.free_gb(self.work) < game_b * 1.2 / 2**30 + 3:
            raise BuildError(f'not enough disk space on {self.work.anchor}: the port needs about {game_b * 1.2 / 2**30 + 3:.0f} GB, {nsp.free_gb(self.work):.0f} GB is free')

    def extract(self):
        o = self.o
        picks = nsp.pick_nsps(self.fam, self.have, [self.pkg_ver])
        PROG.stage('build nx_extract')
        nx = nsp.build_nx_extract(NXNATIVE, self.work, self.logs, self.env)
        PROG.end()
        self.game = nsp.extract(nx, o.keys, self.work / 'extract', self.pkg_ver, *picks[self.pkg_ver], None, self.logs, self.g)   # the ARM64 engine's extraction, shared
        self.ver = check_build(self.game / 'ExeFS', self.game / 'RomFS')
        if self.dp.sites != self.ver.sites: raise BuildError(f'{self.dp.path}: sites does not match the game version in recomp/runtime/game_versions.h')
        self.app_name = o.app_name or nacp.name(self.game / 'control.nacp') or self.g.get('name', self.g.id)
        self.display = nacp.version(self.game / 'control.nacp') or self.ver.name
        say(f'game: {self.app_name} {self.display}: version {self.ver.name} for the Steam Deck engine')

    def game_code(self):
        PROG.stage('game code', step=f'Savage Wind\'s generator, Zig {toolchain.zig_version(self.tools["zig"]) or "?"} for {ZIG_TARGET}')
        got = module_versions()
        if miss := [m for m, v in got.items() if not v]: raise BuildError(f'the generator needs the Python modules {", ".join(miss)}: pip install -r requirements-deck.txt')
        if off := [f'{m} {got[m]} (Savage Wind\'s: {v})' for m, v in GEN_MODULES.items() if got[m] != v]: say(f'[RED] generator modules not Savage Wind\'s: {", ".join(off)} (pip install -r requirements-deck.txt)')
        exefs = self.game / 'ExeFS'
        mods = sorted(p for p in exefs.iterdir() if p.is_file())
        kf = self.sw / 'swgame.key'
        key = nsp.fingerprint(('files', [RECOMP / s for s in GAMELIB_SRCS]), ('files', mods), ('cmd', [str(self.tools['zig']), 'version']), ZIG_TARGET, zig_cflags(), got)
        if (self.sw / 'swgame.so').is_file() and (note := nsp.reused(kf, key)) is not None:
            PROG.end(f'{note}, reused (inputs unchanged)'); return
        nsp.rmtree(kf)
        t0 = time.time()
        lib = build_game_code(exefs, self.sw, zig=self.tools['zig'], jobs=self.o.jobs, env=self.env, logs=self.logs,
                              sites=self.dp.sites,
                              progress=lambda f, step: PROG.set(step=step, done=round(f * 100), total=100, unit='%'))
        note = f'{lib.stat().st_size / 2**20:,.0f} MB, game code id {gamelib_id()}, built in {time.time() - t0:,.0f} s'
        kf.write_text(f'{key}\n{note}\n')
        PROG.end(note)

    def art(self):
        PROG.stage('Steam artwork')
        src = self.game / 'icon.jpg'
        self.art_files = artwork.make(src, self.sw / 'art') if src.is_file() else {}
        if not src.is_file(): say('[RED] no game icon in the extraction: the game gets no artwork in Steam')
        elif not self.art_files: say('[RED] no Pillow (pip install pillow): the game gets no artwork in Steam')
        PROG.end(', '.join(sorted(self.art_files)) or 'none')

    def folder(self):
        PROG.stage('game folder', step='linking')
        from porter.pipeline import stem
        self.dst = self.o.out / stem(self.g, self.app_name)   # (the ARM64 engine's folder name: a device runs one engine's port of a game)
        man = gamedir.build_deck(self.dst, profile=self.dp, version=self.pkg_ver, display=self.display, name=self.app_name, engine_version=self.ver.name,
                                 exefs=self.game / 'ExeFS', romfs=self.game / 'RomFS', gamelib=self.sw / 'swgame.so', appimage=self.appimage,
                                 runtime=f'Savage Wind {self.runtime.version}' if self.runtime else f'Savage Wind ({self.appimage.name})', art=self.art_files,
                                 romfs_key=nsp.fingerprint(('tree', self.game / 'RomFS')), progress=lambda what: PROG.set(step=f'linking {what}'))
        self.result['folder'] = str(self.dst)
        PROG.end(f'{man["files"]:,} files, {man["bytes"] / 2**30:.1f} GB')
