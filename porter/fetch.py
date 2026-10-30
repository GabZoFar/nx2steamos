# Downloading the toolchains a port needs, for a player who has none (`porter tools --download`): Zig from ziglang.org's download index, the newest .NET SDK 10 from
# Microsoft's release list. Each archive is checked against the checksum its list gives, then unpacked into the player's nx2steamos folder (paths.player_tools()), where
# toolchain.find_* look, and the settings point at it. The tool ships neither: they come from their makers, under their licenses (LICENSES). Adapted from nx2android's
# porter/fetch.py, whose .NET part this is. For the Steam Deck engine also Savage Wind's x86-64 AppImage from its GitHub release, checked against the SHA-256 pinned in
# porter/deck.py RUNTIMES (the release game code from this repository's recomp/ loads in), into the same folder, where porter/deck.py find_runtime looks.
import hashlib, json, os, platform, shutil, stat, sys, tarfile, urllib.request, zipfile
from dataclasses import dataclass
from pathlib import Path

from porter import config, toolchain
from porter.paths import player_tools
from porter.upstream import BuildError, PROG

ZIG_INDEX = 'https://ziglang.org/download/index.json'   # every release's archives per host, with size and SHA-256
DOTNET_INDEX = f'https://builds.dotnet.microsoft.com/dotnet/release-metadata/{toolchain.DOTNET_MAJOR}.0/releases.json'   # every release's files with SHA-512
LICENSES = {'Zig': 'https://github.com/ziglang/zig/blob/master/LICENSE', '.NET SDK': 'https://github.com/dotnet/core/blob/main/license-information.md'}
UNPACKED = {'Zig': 0.4 * 2**30, '.NET SDK': 0.9 * 2**30}   # disk space unpacked, for the free-space check
SAVAGEWIND = 'Savage Wind runtime'   # (the doctor's row: toolchain.deck_checks)


@dataclass
class Item:
    name: str      # the doctor's row: 'Zig', '.NET SDK'
    version: str
    url: str
    size: int
    hash: str
    algo: str      # 'sha256' / 'sha512'
    folder: str    # under player_tools(): zig, dotnet
    license: str

    def as_dict(self): return dict(self.__dict__)


def _get(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'nx2steamos'}), timeout=timeout) as r: return r.read()


def _arm(): return platform.machine().lower() in ('arm64', 'aarch64')


def zig_host():   # ziglang.org's name for this machine: aarch64-linux, x86_64-macos, x86_64-windows, ...
    os_ = {'win32': 'windows', 'darwin': 'macos'}.get(sys.platform, 'linux')
    return f'{"aarch64" if _arm() else "x86_64"}-{os_}'


def _dotnet_rid():
    os_ = {'win32': 'win', 'darwin': 'osx'}.get(sys.platform, 'linux')
    return f'{os_}-{"arm64" if _arm() else "x64"}'


def _zig(index=None):
    """The Zig release the host is built with, for this machine"""
    index = index if index is not None else json.loads(_get(ZIG_INDEX))
    rel = index.get(toolchain.ZIG_VERSION)
    if not rel: raise BuildError(f'Zig {toolchain.ZIG_VERSION} is not in {ZIG_INDEX}')
    a = rel.get(zig_host())
    if not a: raise BuildError(f'Zig {toolchain.ZIG_VERSION} has no archive for {zig_host()} ({ZIG_INDEX})')
    return Item('Zig', toolchain.ZIG_VERSION, a['tarball'], int(a.get('size') or 0), a['shasum'], 'sha256', 'zig', LICENSES['Zig'])


def _dotnet():
    """The newest .NET SDK of the major the port builds with, for this OS and CPU"""
    rel = json.loads(_get(DOTNET_INDEX))
    rid, kind = _dotnet_rid(), ('.zip' if sys.platform == 'win32' else '.tar.gz')
    for r in rel['releases']:   # newest first
        for sdk in [r.get('sdk')] + r.get('sdks', []):
            for f in (sdk or {}).get('files', []):
                if f.get('rid') == rid and f['name'] == f'dotnet-sdk-{rid}{kind}':
                    return Item('.NET SDK', sdk['version'], f['url'], 0, f['hash'], 'sha512', 'dotnet', LICENSES['.NET SDK'])
    raise BuildError(f'no .NET SDK {toolchain.DOTNET_MAJOR} for {rid} in {DOTNET_INDEX}')


def _savagewind():
    """The Savage Wind release whose runtime loads game code from this repository's recomp/ (porter/deck.py RUNTIMES)"""
    from porter import deck
    rt = deck.runtime_for(deck.gamelib_id())
    if not rt: raise BuildError(deck.runtime_fix(None))
    return Item(SAVAGEWIND, rt.version, rt.url, rt.size, rt.sha256, 'sha256', 'savagewind', deck.LICENSE)


def missing(cfg=None):
    """The doctor's rows a download can fix: Zig, .NET SDK; Savage Wind's runtime when the configured engine is the Steam Deck's"""
    cfg = cfg if cfg is not None else config.load()
    rows = {r.name: r for r in toolchain.checks(cfg)}
    return [n for n in ('Zig', '.NET SDK') + ((SAVAGEWIND,) if cfg.get('engine') == 'recomp' else ()) if not rows[n].ok]


def plan(names=None):
    """The Items to download: the missing tools (or `names`), with their sizes"""
    names = missing() if names is None else names
    make = {'Zig': _zig, '.NET SDK': _dotnet, SAVAGEWIND: _savagewind}
    out = [make[n]() for n in names if n in make]
    for it in out:
        if not it.size:
            with urllib.request.urlopen(urllib.request.Request(it.url, method='HEAD', headers={'User-Agent': 'nx2steamos'}), timeout=60) as r: it.size = int(r.headers.get('Content-Length') or 0)
    return out


def _download(it, dest):   # to dest, counting bytes and hashing as it goes; a wrong checksum deletes the file
    h, part = hashlib.new(it.algo), dest.with_suffix(dest.suffix + '.part')
    with urllib.request.urlopen(urllib.request.Request(it.url, headers={'User-Agent': 'nx2steamos'}), timeout=120) as r, open(part, 'wb') as f:
        total, done = int(r.headers.get('Content-Length') or it.size or 0), 0
        PROG.set(step='downloading', done=0, total=total or None, unit='B')
        while chunk := r.read(1 << 20):
            f.write(chunk); h.update(chunk); done += len(chunk); PROG.set(done=done)
    if h.hexdigest().lower() != it.hash.lower():
        part.unlink(missing_ok=True); raise BuildError(f'{it.url}: the download\'s {it.algo} is not the one its list gives (try again)')
    part.replace(dest)


def _unzip(arc, into):   # zipfile drops Unix modes and symlinks; keep both outside Windows
    with zipfile.ZipFile(arc) as z:
        infos = z.infolist(); PROG.set(step='unpacking', done=0, total=len(infos), unit='files')
        for i, info in enumerate(infos):
            mode = info.external_attr >> 16
            if os.name != 'nt' and stat.S_ISLNK(mode):
                t = into / info.filename; t.parent.mkdir(parents=True, exist_ok=True); t.unlink(missing_ok=True); os.symlink(z.read(info).decode(), t)
            else:
                p = Path(z.extract(info, into))
                if os.name != 'nt' and mode & 0o777 and not info.is_dir(): p.chmod(mode & 0o777)
            if i % 200 == 0: PROG.set(done=i)


def _untar(arc, into):   # .tar.gz (.NET) and .tar.xz (Zig)
    with tarfile.open(arc) as t:
        PROG.set(step='unpacking'); t.extractall(into, **({'filter': 'tar'} if hasattr(tarfile, 'tar_filter') else {}))   # the filter: Python 3.12+ (3.11.4)


def _install(it, arc):
    """Unpack arc into player_tools()/it.folder: an archive with one top folder (Zig's zig-<host>-<version>/) gives that folder, one without (.NET) is the folder;
    an AppImage goes into the folder as it is"""
    root = player_tools(); tmp = root / f'.{it.folder}.unpack'
    if arc.suffix == '.AppImage':
        dest = root / it.folder; dest.mkdir(parents=True, exist_ok=True)
        arc.replace(dest / arc.name); (dest / arc.name).chmod(0o755)
        return dest / arc.name
    shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir(parents=True)
    (_unzip if arc.suffix == '.zip' else _untar)(arc, tmp)
    top = list(tmp.iterdir())
    src = top[0] if len(top) == 1 and top[0].is_dir() and it.folder != 'dotnet' else tmp
    dest = root / it.folder
    shutil.rmtree(dest, ignore_errors=True); src.replace(dest)
    shutil.rmtree(tmp, ignore_errors=True)
    return dest


def download(items):
    """Download, check and unpack every Item; the settings then point at them. One stage per tool"""
    root = player_tools(); root.mkdir(parents=True, exist_ok=True)
    need = sum(it.size + UNPACKED.get(it.name, 0) for it in items); free = shutil.disk_usage(root).free
    if free < need: raise BuildError(f'{need / 2**30:.1f} GB needed on {root.anchor} for the tools, {free / 2**30:.1f} GB free')
    PROG.plan(len(items))
    keys = {'Zig': ('zig', f'zig{toolchain.EXE}'), '.NET SDK': ('dotnet', f'dotnet{toolchain.EXE}')}
    for it in items:
        PROG.stage(f'{it.name} {it.version}', step='downloading')
        arc = root / Path(it.url).name
        try:
            _download(it, arc); dest = _install(it, arc)
        finally: arc.unlink(missing_ok=True)
        if it.name in keys:   # (Savage Wind's is found where it went: the setting is for an AppImage of the player's own)
            key, exe = keys[it.name]
            config.set_value(key, str(dest / exe))
        PROG.end(str(dest))
    PROG.close()
    bad = [n for n in missing() if n in {it.name for it in items}]
    if bad: raise BuildError('downloaded, but the check still fails for: ' + ', '.join(bad) + ' (porter doctor says why)')
