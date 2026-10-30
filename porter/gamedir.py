# The game folder: everything a ported game is on the SteamOS device, one folder that Steam's shortcut starts (docs/ARCHITECTURE.md "The game folder"):
#   nx2steamos-run   the launcher Steam starts (a shell script): the host with the folder's mods/, the arguments Steam passes after --
#   bin/nx2s-host    the host, built for this game's profile (dm/steamos/build.py)
#   lib/             libSDL3.so.0 when the port has one to bundle (SteamOS's base image has SDL2 only; the host opens lib/ first: dm/steamos/sdl_dyn.c)
#   mods/            the rewritten code modules (patch_nso.py's outputs, DM_TARGET=linux)
#   RomFS/           the game's files (the packed version's merged RomFS)
#   aoc/<index>/     the DLC the player has
#   shaders.dmsv     the Vulkan shaders (dm/android/tools/shader_vk.py: the Vulkan backend is the Android port's)
#   env.txt          host switches: the game language, the DLC folder, the profile's env rows; ${DM_GAME_DIR} in a value is this folder, ${DM_ROOT} the game's
#                    state folder (dm/steamos/front_sdl.c reads it)
#   settings.txt     the pause menu's first-launch defaults (the profile's setting rows); an install never replaces the player's own
#   art/             the Steam library artwork made from the game's icon (porter/artwork.py)
#   nx2s-game.json   what the folder is: the game, the version, the device features the profile requires; written last, so a folder with it is complete
# Saves, logs and caches live outside it ($XDG_DATA_HOME / $XDG_STATE_HOME / $XDG_CACHE_HOME/nx2steamos/<title ID>), so porting the game again keeps them.
# Big trees are hard links into the extraction when the volume allows, so the folder takes no extra space. Adapted from nx2android's porter/datapack.py.
# The Steam Deck engine's folder (build_deck: recomp/, Savage Wind Recomp) is laid out for its runtime instead:
#   nx2steamos-run   the launcher: Savage Wind's AppImage with the folder's game/ as its game files and the game's state folder as its data root
#   bin/<AppImage>   Savage Wind's runtime (porter/deck.py RUNTIMES)
#   game/exefs/      the game's code modules, as extracted (the runtime loads them where the game code was generated for them)
#   game/romfs/      the game's files (the packed version's merged RomFS); game/romfs.bin, the data image the game reads, is built from it on the device
#   game/swgame.so   the game code library recompiled for x86-64 (porter/deck.py build_game_code), and its id file swgame.so.id
#   art/  nx2s-game.json   as above
import json, os, re, shutil, stat, time
from pathlib import Path

MANIFEST = 'nx2s-game.json'
LAUNCHER = 'nx2steamos-run'
HOST = 'bin/nx2s-host'
KEEP_ON_DEVICE = ('settings.txt',)          # the player's choices win over the folder's defaults
DECK_KEEP = ('game/romfs.bin', 'game/romfs.bin.key')   # the data image the device built (minutes, ~the RomFS's size) and what it was built from: kept until the RomFS changes
PREPARE = '--nx2s-prepare'                  # the Steam Deck launcher's argument that builds the data image and exits (porter/install.py runs it after copying)


def keep_on_device(man):   # the files of a game folder on the device that an install leaves as they are
    return DECK_KEEP if man.get('engine') == 'recomp' else KEEP_ON_DEVICE


def link_or_copy(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    try: os.link(src, dst)
    except OSError: shutil.copy2(src, dst)


def link_tree(src, dst):   # (files, bytes)
    n = b = 0
    for root, _, files in os.walk(src):
        for f in files:
            s = Path(root) / f
            link_or_copy(s, dst / s.relative_to(src)); n += 1; b += s.stat().st_size
    return n, b


def launcher(name, title_id):
    """The script Steam's shortcut starts. The host finds its folder from its own path and sets everything else up itself (steamos/front_sdl.c), so this stays a one-liner
    the player can read; extra arguments from Steam's launch options reach the game after --"""
    return (f'#!/bin/sh\n# {name} ({title_id}), ported by nx2steamos: the launcher of a non-Steam shortcut. Launch options go to the game as its arguments.\n'
            'here=$(dirname "$(readlink -f "$0")")\n'
            'if [ "$#" -gt 0 ]; then exec "$here/bin/nx2s-host" "$here/mods" -- "$@"; fi\n'
            'exec "$here/bin/nx2s-host" "$here/mods"\n')


def build(dst, *, profile, version, display, name, host, patched, romfs, shaders, aoc=None, language='en-US', env=(), files=None, art=None, libs=(), progress=None):
    """Write the game folder to dst (replacing it). profile: the SteamosProfile; host: the built host executable; env: more env.txt rows, after the profile's;
    files: {name in the folder: source} (the 60 fps memory writes, fpslock.tsv); art: {kind: png} (porter/artwork.py). Returns the manifest"""
    tmp = dst.with_name(dst.name + '.partial')
    if tmp.exists(): shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    n = b = 0
    for what, src, sub in (('the rewritten modules', patched, 'mods'), ('the RomFS', romfs, 'RomFS'), ('the DLC', aoc, 'aoc')):
        if not src: continue
        if progress: progress(what)
        dn, db = link_tree(src, tmp / sub); n += dn; b += db
    link_or_copy(shaders, tmp / 'shaders.dmsv'); n += 1; b += shaders.stat().st_size
    (tmp / 'bin').mkdir(); shutil.copy2(host, tmp / HOST); (tmp / HOST).chmod(0o755); n += 1; b += Path(host).stat().st_size   # a copy: the build folder's host is rebuilt in place
    (tmp / LAUNCHER).write_text(launcher(name, profile.id), encoding='utf-8', newline='\n')
    (tmp / LAUNCHER).chmod((tmp / LAUNCHER).stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    for nm, src in (files or {}).items(): shutil.copyfile(src, tmp / nm); n += 1; b += Path(src).stat().st_size
    for kind, src in (art or {}).items(): link_or_copy(Path(src), tmp / 'art' / Path(src).name)
    for src in libs: (tmp / 'lib').mkdir(exist_ok=True); shutil.copy2(src, tmp / 'lib' / Path(src).name); n += 1; b += Path(src).stat().st_size
    rows = [f'# Written by nx2steamos\'s porter for {profile.name} ({profile.id}); KEY=VALUE per line, read by the host at launch (${{DM_GAME_DIR}} is this folder)',
            f'DM_LANG={language}']
    if aoc: rows.append('DM_AOC_DIR=${DM_GAME_DIR}/aoc')
    rows += profile.env + list(env)
    (tmp / 'env.txt').write_text('\n'.join(rows) + '\n', encoding='utf-8', newline='\n')
    if profile.settings: (tmp / 'settings.txt').write_text(''.join(f'{k}={v}\n' for k, v in profile.settings.items()), encoding='utf-8', newline='\n')
    man = dict(engine='nxnative', title_id=profile.id, name=name, version=version, display=display, language=language, status=profile.status, requires=profile.requires,
               files=n, bytes=b, dlc=sorted(int(p.name) for p in aoc.iterdir() if p.is_dir() and p.name.isdigit()) if aoc else [],
               art=sorted(Path(p).name for p in (art or {}).values()), libs=[Path(p).name for p in libs], made=time.strftime('%Y-%m-%d %H:%M:%S'))
    (tmp / MANIFEST).write_text(json.dumps(man, indent=2), encoding='utf-8')
    if dst.exists(): shutil.rmtree(dst)
    tmp.rename(dst)
    return man


def safe_name(n):   # a file name the launcher can hold between double quotes as it is (a player's own AppImage may be named anything)
    return re.sub(r'[^A-Za-z0-9._+-]', '_', n)


def deck_launcher(name, title_id, appimage, romfs_key, env=()):
    """The Steam Deck folder's launcher: Savage Wind's runtime with the folder's game files (its first argument) and the game's own state folder as its data root (saves,
    settings, shader caches, savagewind.log: nothing the player makes lives in the folder). The data image (game/romfs.bin) is built here when it is missing or older than
    the RomFS (an install builds it first: PREPARE); without FUSE the AppImage unpacks itself to /tmp at each start instead of mounting. Steam's launch options go to the
    runtime after the game folder (--config: its settings screen). appimage: a file name safe in the script (safe_name); env: the profile's (NAME, VALUE) runtime settings
    (deck.tsv env), exported before the runtime starts"""
    q = safe_name(appimage)
    exports = ''.join(f"export {k}='{v}'\n" for k, v in env)
    return (f'#!/bin/sh\n# {name} ({title_id}), ported by nx2steamos for the Steam Deck: Savage Wind Recomp\'s runtime with the game\'s code recompiled from the player\'s files.\n'
            f'# Its saves, settings, shader caches and log (savagewind.log) are in ~/.local/share/nx2steamos/{title_id}. Launch options go to the runtime (--config: its settings).\n'
            'here=$(dirname "$(readlink -f "$0")")\n'
            f'run="$here/bin/{q}"\n'
            f'export SAVAGEWIND_DATA_ROOT="${{XDG_DATA_HOME:-$HOME/.local/share}}/nx2steamos/{title_id}"\n'
            'mkdir -p "$SAVAGEWIND_DATA_ROOT"\n'
            + (f'# the game profile\'s runtime settings (deck.tsv env)\n{exports}' if exports else '') +
            'command -v fusermount3 >/dev/null 2>&1 || command -v fusermount >/dev/null 2>&1 || export APPIMAGE_EXTRACT_AND_RUN=1\n'
            f'if [ "$(cat "$here/game/romfs.bin.key" 2>/dev/null)" != "{romfs_key}" ]; then\n'
            '  rm -f "$here/game/romfs.bin.key"\n'
            '  "$run" --build-romfs "$here/game" || exit $?\n'
            f'  printf \'%s\\n\' "{romfs_key}" > "$here/game/romfs.bin.key"\n'
            'fi\n'
            f'[ "${{1:-}}" = {PREPARE} ] && exit 0\n'
            'exec "$run" "$here/game" "$@"\n')


def build_deck(dst, *, profile, version, display, name, engine_version, exefs, romfs, gamelib, appimage, runtime, art=None, romfs_key='', progress=None):
    """Write the Steam Deck engine's game folder to dst (replacing it). profile: the game's DeckProfile (porter/deck.py); gamelib: swgame.so, its id file beside it;
    appimage: Savage Wind's runtime; engine_version: the game version the engine knows the code as (1.9.0). Returns the manifest"""
    tmp = dst.with_name(dst.name + '.partial')
    if tmp.exists(): shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    n = b = 0
    for what, src, sub in (('the code modules', exefs, 'game/exefs'), ('the RomFS', romfs, 'game/romfs')):
        if progress: progress(what)
        dn, db = link_tree(src, tmp / sub); n += dn; b += db
    romfs_b = db   # (the device writes about as much again: romfs.bin)
    gamelib = Path(gamelib)
    for src in (gamelib, gamelib.with_name(gamelib.name + '.id')): shutil.copy2(src, tmp / 'game' / src.name); n += 1; b += src.stat().st_size   # copies: rebuilt in place
    app = tmp / 'bin' / safe_name(Path(appimage).name)
    (tmp / 'bin').mkdir(); shutil.copy2(appimage, app); app.chmod(0o755); n += 1; b += app.stat().st_size
    (tmp / LAUNCHER).write_text(deck_launcher(name, profile.id, app.name, romfs_key, getattr(profile, 'env', ())), encoding='utf-8', newline='\n')
    (tmp / LAUNCHER).chmod((tmp / LAUNCHER).stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    for kind, src in (art or {}).items(): link_or_copy(Path(src), tmp / 'art' / Path(src).name)
    gid = gamelib.with_name(gamelib.name + '.id').read_text().strip()
    man = dict(engine='recomp', title_id=profile.id, name=name, version=version, display=display, engine_version=engine_version, status=profile.status, requires=[],
               runtime=runtime, appimage=app.name, gamelib_id=gid, romfs_key=romfs_key, files=n, bytes=b, romfs_bytes=romfs_b,
               art=sorted(Path(p).name for p in (art or {}).values()), made=time.strftime('%Y-%m-%d %H:%M:%S'))
    (tmp / MANIFEST).write_text(json.dumps(man, indent=2), encoding='utf-8')
    if dst.exists(): shutil.rmtree(dst)
    tmp.rename(dst)
    return man


def read_manifest(d):
    try: return json.loads((Path(d) / MANIFEST).read_text(encoding='utf-8'))
    except (OSError, ValueError): return None
