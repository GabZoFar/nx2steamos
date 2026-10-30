# The player's settings, kept in <user dir>/config.json: toolchain paths, prod.keys, the output folder, the device. `porter config` (and a GUI's setup screen) write them;
# command-line options win over them.
import json

from porter.paths import user_dir

KEYS = {
    'keys': 'path of prod.keys',
    'zig': 'zig executable (the C toolchain the host is built with, 0.17)',
    'dotnet': 'dotnet executable (.NET SDK 10)',
    'out': 'folder the game folders are made in (empty = the game files\' folder)',
    'device': 'the SteamOS device to install on over SSH, user@host (empty = this machine, when it runs SteamOS)',
    'device_dir': 'where games go on the device (empty = ~/Games/nx2steamos)',
    'steam_user': 'the Steam account whose library gets the games, its userdata number (empty = the last one logged in)',
    'language': 'game language code (empty = English)',
    'sdl3': 'libSDL3.so.0 for arm64 to put in each game folder (empty = dist/sdl3/ from devtools/build_sdl3.sh; SteamOS has no SDL3)',
    'engine': 'the engine `porter port` uses: nxnative (ARM64 SteamOS devices) or recomp (the Steam Deck: x86-64) (empty = nxnative)',
    'savagewind': 'Savage Wind\'s x86-64 AppImage for the Steam Deck engine\'s game folders (empty = the release `porter tools --download` fetched)',
}


def path():
    return user_dir() / 'config.json'


def load():
    try: c = json.loads(path().read_text(encoding='utf-8'))
    except (OSError, ValueError): c = {}
    return {k: c.get(k, '') for k in KEYS}


def save(c):
    p = path(); tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps({k: c.get(k, '') for k in KEYS}, indent=2), encoding='utf-8'); tmp.replace(p)


CHOICES = {'engine': ('', 'nxnative', 'recomp')}


def set_value(key, value):
    if key not in KEYS: raise KeyError(f'unknown setting {key!r}: one of {", ".join(KEYS)}')
    if key in CHOICES and value not in CHOICES[key]: raise KeyError(f'{key} is one of {", ".join(c for c in CHOICES[key] if c)}')
    c = load(); c[key] = value; save(c)
