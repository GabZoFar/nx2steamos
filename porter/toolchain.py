# The tools a port needs on the player's machine, found and checked (`porter doctor`). Nothing here is shipped with the tool: the player installs Zig (0.17: the C toolchain
# the host is built with, for aarch64 Linux from any machine, the SteamOS device itself included) and the .NET SDK (10), or has `porter tools --download` (porter.fetch)
# fetch them from ziglang.org and Microsoft into their nx2steamos folder (paths.player_tools()), which the finders here also search. To install from another machine:
# ssh (and rsync, else tar over ssh). Adapted from nx2android's porter/toolchain.py.
import importlib.util, os, platform, re, shutil, subprocess, sys
from dataclasses import dataclass
from pathlib import Path

from porter import config
from porter.paths import LOCAL_TOOLS, player_tools

ZIG_VERSION = '0.17.0'   # the Zig the SteamOS host is built and tested with
ZIG_MIN = (0, 15)        # older ones lack flags build.py passes
DOTNET_MAJOR = 10
PY_MODULES = {'lz4.block': 'lz4', 'capstone': 'capstone'}   # the rewriter (capstone) and the NSO reader (lz4)
EXE = '.exe' if os.name == 'nt' else ''
DISK_GB = 20   # the setup check's free space: a NEO-sized port (7 GB of build folder) with room to spare


class ToolError(Exception):
    pass


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True
    path: str = ''
    fix: str = ''

    def as_dict(self): return dict(self.__dict__)


def zig_version(zig):   # '0.17.0', None when it does not run
    try: r = subprocess.run([str(zig), 'version'], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired): return None
    v = r.stdout.strip()
    return v if r.returncode == 0 and re.match(r'\d+\.\d+', v) else None


def _vtuple(v): return tuple(int(x) for x in re.findall(r'\d+', v)[:2])


def find_zig(cfg=None):
    """The zig to build the host with: the configured one as it is; otherwise the player's download, a developer's tools/, then PATH. The tested version is preferred"""
    cfg = cfg if cfg is not None else config.load()
    if cfg.get('zig') and Path(cfg['zig']).is_file(): return Path(cfg['zig'])
    cands = [str(player_tools() / 'zig' / f'zig{EXE}'), str(LOCAL_TOOLS / 'zig' / f'zig{EXE}'), shutil.which('zig')]
    found = list(dict.fromkeys(Path(c) for c in cands if c and Path(c).is_file()))
    return next((z for z in found if zig_version(z) == ZIG_VERSION), found[0] if found else None)


def find_dotnet(cfg=None):
    """The dotnet to use: the configured one as it is; otherwise the first of DOTNET_ROOT, PATH and the usual places that has SDK 10 (a runtime-only dotnet cannot build
    nx_extract), else the first found"""
    cfg = cfg if cfg is not None else config.load()
    if cfg.get('dotnet') and Path(cfg['dotnet']).is_file(): return Path(cfg['dotnet'])
    progs = [os.environ.get(k) for k in ('ProgramFiles', 'ProgramW6432')] if os.name == 'nt' else []
    cands = [os.environ.get('DOTNET_ROOT') and str(Path(os.environ['DOTNET_ROOT']) / f'dotnet{EXE}'), shutil.which('dotnet'), str(LOCAL_TOOLS / 'dotnet' / f'dotnet{EXE}'),
             str(player_tools() / 'dotnet' / f'dotnet{EXE}'), str(Path.home() / '.dotnet' / f'dotnet{EXE}'), *[str(Path(p) / 'dotnet' / f'dotnet{EXE}') for p in progs if p],
             '/usr/local/share/dotnet/dotnet', '/usr/share/dotnet/dotnet', '/usr/lib/dotnet/dotnet']
    found = list(dict.fromkeys(Path(c) for c in cands if c and Path(c).is_file()))
    return next((d for d in found if any(int(s.split('.')[0]) >= DOTNET_MAJOR for s in dotnet_sdks(d))), found[0] if found else None)


def dotnet_sdks(dotnet):   # ['10.0.402', ...]
    try: r = subprocess.run([str(dotnet), '--list-sdks'], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired): return []
    return re.findall(r'^(\d+\.\d+\.\d+)', r.stdout, re.M)


def find_sdl3(cfg=None):
    """The libSDL3.so.0 to bundle in game folders (SteamOS's base image has SDL2 only): the configured one, then devtools/build_sdl3.sh's dist/sdl3/, then the player's folder"""
    from porter.paths import REPO, data_dir
    cfg = cfg if cfg is not None else config.load()
    for c in (cfg.get('sdl3'), str(REPO / 'dist/sdl3/libSDL3.so.0'), str(data_dir() / 'runtime/libSDL3.so.0')):
        if c and Path(c).is_file(): return Path(c)
    return None


def os_release(root='/'):
    """/etc/os-release as a dict ({} where there is none: Windows, a Mac)"""
    out = {}
    for p in (Path(root) / 'etc/os-release', Path(root) / 'usr/lib/os-release'):
        try: text = p.read_text(errors='replace')
        except OSError: continue
        for line in text.splitlines():
            k, eq, v = line.partition('=')
            if eq: out[k.strip()] = v.strip().strip('"')
        break
    return out


def local_steamos(arches=('aarch64', 'arm64')):
    """This machine is a SteamOS device the port can install on directly: SteamOS (or a derivative that says so) on one of arches (arm64 for the ARM64 engine;
    the Steam Deck engine's are x86-64)"""
    r = os_release()
    ids = {r.get('ID', '')} | set(r.get('ID_LIKE', '').split())
    return sys.platform.startswith('linux') and platform.machine().lower() in arches and ('steamos' in ids or 'holo' in ids)


def _importable(m):
    try: return importlib.util.find_spec(m) is not None
    except (ImportError, ValueError): return False


def checks(cfg=None, keys=None):
    """Every requirement, as Check rows (the order the setup screen shows them)"""
    cfg = cfg if cfg is not None else config.load()
    out = []
    v = sys.version_info
    out.append(Check('Python', v >= (3, 11), f'{v.major}.{v.minor}.{v.micro} ({sys.executable})', fix='Python 3.11 or later'))
    miss = [pip for m, pip in PY_MODULES.items() if not _importable(m)]
    if not (_importable('compression.zstd') or _importable('backports.zstd')): miss.append('backports.zstd')   # zstd: in the standard library from Python 3.14
    out.append(Check('Python modules', not miss, 'missing: ' + ', '.join(miss) if miss else 'lz4, capstone, zstd', fix='pip install ' + ' '.join(miss) if miss else ''))
    out.append(Check('Pillow', _importable('PIL'), 'the Steam library artwork from the game\'s icon' if _importable('PIL') else 'missing: the game gets no artwork in Steam', required=False,
                     fix='pip install pillow'))
    out.append(Check('PyYAML', _importable('yaml'), '--fps60 (FPSLocker configs)' if _importable('yaml') else 'missing: --fps60 cannot read FPSLocker configs', required=False, fix='pip install pyyaml'))
    zig = find_zig(cfg); zv = zig_version(zig) if zig else None
    if not zig or not zv: out.append(Check('Zig', False, 'not found' if not zig else f'{zig} does not run', fix=f'Download missing tools, or install Zig {ZIG_VERSION} (ziglang.org/download) and set its path'))
    else: out.append(Check('Zig', zv == ZIG_VERSION, f'{zv} at {zig}' + ('' if zv == ZIG_VERSION else f': the host is built with {ZIG_VERSION}; others are untested'),
                           required=_vtuple(zv) < (1, 0) and _vtuple(zv) >= ZIG_MIN, path=str(zig), fix='' if zv == ZIG_VERSION else f'install Zig {ZIG_VERSION}'))
    dn = find_dotnet(cfg)
    sdks = dotnet_sdks(dn) if dn else []
    good = [s for s in sdks if int(s.split('.')[0]) >= DOTNET_MAJOR]
    out.append(Check('.NET SDK', bool(good), (f'{", ".join(sdks) or "no SDK"} ({dn})' if dn else 'dotnet not found'), path=str(dn or ''),
                     fix='' if good else f'Download missing tools, or install the .NET SDK {DOTNET_MAJOR} (dotnet.microsoft.com)'))
    sdl = find_sdl3(cfg)
    out.append(Check('SDL3 for the device', bool(sdl), str(sdl) if sdl else 'none to bundle: a game runs only where the device has libSDL3.so.0 (SteamOS has none)', required=False,
                     path=str(sdl or ''), fix='' if sdl else 'devtools/build_sdl3.sh builds one (Docker on an arm64 machine), or porter config sdl3 <libSDL3.so.0>'))
    deck = cfg.get('engine') == 'recomp'
    out += deck_checks(cfg, required=False)
    here = local_steamos(('x86_64', 'amd64') if deck else ('aarch64', 'arm64'))
    ssh, rsync = shutil.which('ssh'), shutil.which('rsync')
    out.append(Check('SteamOS here', here, f'this machine runs SteamOS on {"x86-64" if deck else "arm64"}: ports install here' if here else
                     'not a SteamOS device: ports install on one over SSH (porter config device user@host)', required=False))
    out.append(Check('ssh', bool(ssh), ssh or 'not found: ports cannot be installed on another machine', required=False, path=ssh or '', fix='' if ssh or here else 'install an OpenSSH client'))
    out.append(Check('rsync', bool(rsync), rsync or 'not found: installs over SSH copy whole files with tar (slower to update)', required=False, path=rsync or ''))
    k = keys or cfg.get('keys')
    out.append(Check('prod.keys', bool(k) and Path(k).is_file(), k if k and Path(k).is_file() else (f'no such file: {k}' if k else 'not set'), path=k or '',
                     fix='' if k and Path(k).is_file() else 'dump prod.keys from your own Switch and set its path'))
    where = Path(cfg.get('out') or Path.home())   # the output folder's drive, or the home folder's (a port's build folder goes beside the game files: the port checks that drive itself)
    where = next((p for p in (where, *where.parents) if p.exists()), Path.home())
    free = shutil.disk_usage(where).free / 2**30
    out.append(Check('Disk space', free >= DISK_GB, f'{free:.0f} GB free on {where.anchor}', required=False,
                     fix='' if free >= DISK_GB else f'a port takes about twice its game files on the game folder\'s drive (NEO: 7 GB); keep {DISK_GB} GB free'))
    return out


def deck_checks(cfg=None, required=True):
    """The Steam Deck engine's own requirements (porter/deck.py): the generator's Python modules, Savage Wind's runtime to put in game folders. required: for a port
    with that engine (the doctor shows them as optional otherwise)"""
    from porter import deck
    cfg = cfg if cfg is not None else config.load()
    got = deck.module_versions()
    miss = [m for m, v in got.items() if not v]
    off = [f'{m} {got[m]} (Savage Wind\'s: {v})' for m, v in deck.GEN_MODULES.items() if got[m] and got[m] != v]
    out = [Check('Deck generator', not miss, ('missing: ' + ', '.join(miss)) if miss else ', '.join(f'{m} {v}' for m, v in got.items()) + (f'; not Savage Wind\'s: {", ".join(off)}' if off else ''),
                 required=required, fix='pip install -r requirements-deck.txt' if miss or off else '')]
    app, rt = deck.find_runtime(cfg)
    out.append(Check('Savage Wind runtime', bool(app), f'{app}' + (f' (Savage Wind {rt.version})' if rt else ' (your own build)') if app else
                     'none: game folders for the Steam Deck engine carry Savage Wind\'s AppImage', required=required, path=str(app or ''), fix='' if app else deck.runtime_fix(rt)))
    return out


def require(cfg=None, keys=None, remote=False, engine='nxnative'):
    """The checked tools for a port: {'zig', 'dotnet', 'ssh', 'rsync', 'sdl3'}; ToolError naming every missing requirement. engine recomp: the Steam Deck engine's
    requirements too (deck_checks), and no SDL3 (its runtime has its own)"""
    rows = [r for r in checks(cfg, keys) if r.name not in ('Deck generator', 'Savage Wind runtime')]
    if engine == 'recomp': rows += deck_checks(cfg)
    bad = [r for r in rows if not r.ok and (r.required or (remote and r.name == 'ssh'))]
    if bad: raise ToolError('missing requirements: ' + '; '.join(f'{r.name}: {r.detail}' + (f' ({r.fix})' if r.fix else '') for r in bad))
    by = {r.name: r for r in rows}
    return {'zig': Path(by['Zig'].path), 'dotnet': Path(by['.NET SDK'].path), 'ssh': by['ssh'].path or None, 'rsync': by['rsync'].path or None,
            'sdl3': Path(by['SDL3 for the device'].path) if by['SDL3 for the device'].ok else None}
