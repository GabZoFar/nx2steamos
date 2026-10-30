# Installing a game folder on the SteamOS device (this machine, or one over SSH: porter/remote.py) and putting it in Steam's library (porter/steam.py, run on the device).
# Order, as nx2android's install: check the device can run it, check the space, copy the folder (the manifest last, so a folder with it is complete), then the shortcut.
# The Steam Deck engine's folder then has its data image built on the device by its own launcher (gamedir.PREPARE), before the shortcut.
# The player's state (saves, settings, caches) is outside the folder, in ~/.local/share/nx2steamos/<title ID>, and an install never touches it.
import json, re, shlex
from pathlib import Path

from porter import gamedir, remote
from porter.upstream import BuildError, PROG, say

STEAM_PY = Path(__file__).with_name('steam.py')
MIN_GLIBC = (2, 38)   # what nxnative/dm/steamos/build.py links against (--glibc)
DECK_GLIBC = (2, 35)  # what Savage Wind's AppImage is built against (Ubuntu 22.04: recomp/docs/linux.md)
X86_64_V3 = {'avx2', 'fma', 'bmi2'}   # the game code's target on the Steam Deck engine (porter/deck.py TARGET)


def _ver(s): return tuple(int(x) for x in re.findall(r'\d+', s or '')[:2])


def check_deck(info, man):
    """The Steam Deck engine's folder: an x86-64 Linux device with glibc 2.35 or later; the game code's CPU features and FUSE are checked when known"""
    arch = info.get('arch')
    if arch in ('aarch64', 'arm64'):
        raise BuildError('the device is ARM64: this game folder is the Steam Deck engine\'s, recompiled for x86-64. An ARM64 device runs the game\'s own Arm code: port it '
                         'without --engine recomp')
    if arch not in ('x86_64', 'amd64'): raise BuildError(f'the device is {arch or "unknown"}, not x86-64: the Steam Deck engine\'s game code cannot run on it')
    g = _ver(info.get('glibc'))
    if g and g < DECK_GLIBC: raise BuildError(f'the device has glibc {info["glibc"]}; Savage Wind\'s runtime needs {".".join(map(str, DECK_GLIBC))} or later')
    cpu = set(info['cpu'].split()) if 'cpu' in info else None
    if cpu is not None and not X86_64_V3 <= cpu:
        say(f'[RED] the device\'s CPU has no {", ".join(sorted(X86_64_V3 - cpu))}: the game code was built for x86-64-v3 (AVX2, FMA, BMI2, as the Steam Deck has) '
            'and will not load on it')
    if not info.get('lib.libvulkan.so.1'): say('[RED] the device has no libvulkan.so.1 (Vulkan loader): the game cannot draw')
    if 'fuse' in info and not info['fuse']: say('the device has no FUSE (fusermount3): the AppImage unpacks itself to /tmp at each start (a slower start)')


def check_device(info, man):
    """Refuse a device the game folder cannot run on: for the ARM64 engine not arm64 Linux, a glibc older than the build's (GPU features the profile requires are checked
    when known); for the Steam Deck engine (man['engine'] recomp) check_deck"""
    if man.get('engine') == 'recomp': return check_deck(info, man)
    if info.get('arch') in ('x86_64', 'amd64'):
        raise BuildError('the device is x86-64 (a Steam Deck?): this game folder runs the game\'s own Arm code, which needs an ARM64 device. '
                         'The Steam Deck has its own engine, which recompiles the code: port the game with --engine recomp (docs/RECOMP.md)')
    if info.get('arch') not in ('aarch64', 'arm64'): raise BuildError(f'the device is {info.get("arch") or "unknown"}, not arm64: the game\'s own Arm code cannot run on it natively')
    g = _ver(info.get('glibc'))
    if g and g < MIN_GLIBC: raise BuildError(f'the device has glibc {info["glibc"]}; the host needs {".".join(map(str, MIN_GLIBC))} or later')
    gpu = info.get('gpu', '')
    if 'bcn' in man.get('requires', []) and gpu and not re.search(r'adreno|qcom|amd|radeon|nvidia|apple|mali-g7|immortalis', gpu, re.I):
        say(f'[RED] {man["name"]} needs BC-compressed textures; whether this GPU ({gpu.strip()}) reads them is not known')
    if not info.get('lib.libvulkan.so.1'): say('[RED] the device has no libvulkan.so.1 (Vulkan loader): the game cannot draw')
    if not info.get('lib.libavcodec.so'): say('the device has no FFmpeg (libavcodec): the game\'s movies play without pictures or AAC sound')


def install(game_dir, *, device='', device_dir='', steam=True, restart_steam=False, account='', ssh=None, rsync=None):
    game_dir = Path(game_dir)
    man = gamedir.read_manifest(game_dir)
    if not man: raise BuildError(f'not a game folder (no {gamedir.MANIFEST}): {game_dir}')
    t = remote.target(device, ssh, rsync)
    PROG.stage('install', step=f'checking {t.name}')
    info = remote.probe(t)
    say(f'device: {t.name}: {info.get("os", "?")}, kernel {info.get("kernel", "?")}, glibc {info.get("glibc", "?")}, {info.get("page", "?")}-byte pages, GPU {info.get("gpu", "?").strip() or "?"}')
    check_device(info, man)
    home = t.home()
    root = (device_dir or f'{home}/Games/nx2steamos').replace('~', home, 1)
    dest = f'{root}/{game_dir.name}'
    free = remote.free_bytes(t, root)
    need = man['bytes'] + man.get('romfs_bytes', 0)   # (the Steam Deck engine's data image is built on the device, about the RomFS's size)
    if free is not None and need > free: raise BuildError(f'{t.name} has {free / 2**30:.1f} GB free in {root}; the game needs {need / 2**30:.1f} GB')
    PROG.set(step=f'copying {man["files"]:,} files ({man["bytes"] / 2**30:.1f} GB) to {dest}')
    t.push_tree(game_dir, dest, keep=gamedir.keep_on_device(man), last=(gamedir.MANIFEST,))
    res = {'device': t.name, 'path': dest}
    if man.get('engine') == 'recomp':   # the data image the game reads, from the RomFS just copied (a few minutes; kept until the RomFS changes: gamedir.DECK_KEEP)
        PROG.set(step=f'building the game\'s data image on {t.name} (game/romfs.bin, {man.get("romfs_bytes", 0) / 2**30:.1f} GB)')
        r = t.sh(f'{shlex.quote(dest + "/" + gamedir.LAUNCHER)} {gamedir.PREPARE}', check=False, timeout=4 * 3600)
        if r.returncode:   # (without a terminal the runtime writes to its log, in the game's state folder)
            log = t.sh(f'tail -n 5 "${{XDG_DATA_HOME:-$HOME/.local/share}}/nx2steamos/{man["title_id"]}/savagewind.log"', check=False).stdout
            raise BuildError(f'the data image was not built on {t.name} (exit {r.returncode}): {(r.stderr or r.stdout or log).decode(errors="replace").strip()[-400:]}; '
                             f'starting the game builds it again ({dest}/{gamedir.LAUNCHER})')
    if steam:
        PROG.set(step='adding it to Steam')
        exe, art = f'{dest}/{gamedir.LAUNCHER}', {k: f'{dest}/art/{k}.png' for k in ('portrait', 'wide', 'hero') if (game_dir / 'art' / f'{k}.png').is_file()}
        icon = f'{dest}/art/icon.png' if (game_dir / 'art/icon.png').is_file() else ''
        args = ['register', '--name', man['name'], '--exe', exe, '--start-dir', dest, '--icon', icon] + [f'--art={k}={v}' for k, v in art.items()]
        if account: args += ['--account', account]
        if restart_steam: args.append('--restart-steam')
        r = t.sh('python3 - ' + ' '.join(shlex.quote(x) for x in args), input=STEAM_PY.read_bytes(), check=False, timeout=180)
        try: got = json.loads(r.stdout.decode(errors='replace').strip().splitlines()[-1])
        except (ValueError, IndexError): got = {'error': (r.stderr or r.stdout).decode(errors='replace').strip()[-300:] or f'exit {r.returncode}'}
        if 'error' in got: say(f'[RED] not added to Steam: {got["error"]}'); res['steam'] = None
        else:
            say(f'Steam: {man["name"]} {got["how"]} (account {got["account"]}, shortcut {got["appid"]}; steam://rungameid/{got["rungameid"]})' + (', Steam restarted' if got.get('restarted') else ''))
            res['steam'] = got
    PROG.end(f'{man["name"]} in {dest}')
    say(f'installed: start "{man["name"]}" from Steam\'s library' if res.get('steam') else f'installed in {dest}: add {dest}/{gamedir.LAUNCHER} to Steam as a non-Steam game')
    return res


def pull_captures(device, title_id, into, ssh=None, rsync=None):
    """Shaders the game captured on the device because the bundle lacked them (gfx_vk_shader.c vk_shader_missing, in its state folder), copied into the build folder
    for the next port to translate (as nx2android's pull_captures); the names copied"""
    t = remote.target(device, ssh, rsync)
    src = f'{t.home()}/.local/share/nx2steamos/{title_id}/captured_shaders'
    return t.pull_tree(src, str(into))
