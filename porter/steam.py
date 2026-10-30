"""Steam's library on the device: a ported game becomes a non-Steam shortcut (userdata/<account>/config/shortcuts.vdf) with artwork in config/grid/.

shortcuts.vdf is Valve's binary KeyValues: each field is a type byte, a NUL-terminated name and a value; 0x00 opens a map, 0x01 is a NUL-terminated UTF-8
string, 0x02 a little-endian int32, 0x07 a uint64, 0x08 closes the current map. The file is one map, "shortcuts", whose entries are maps named "0", "1", ...

Steam keeps its shortcuts in memory and writes the file itself, so a change made while Steam runs is lost the next time Steam saves it: the file is written
only while Steam is closed. With Steam running (always, in Game Mode) Steam is asked to add the game itself, as Valve's own steamos-add-to-steam does
(steam://addnonsteamgame/ with a .desktop file), and the artwork follows under the id Steam gave it; --restart-steam stops Steam, writes, and starts it again.
This module needs nothing but Python's standard library: install.py runs it on the device over SSH (python3 - register ...).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import urllib.parse
import zlib
from dataclasses import dataclass, field
from pathlib import Path

MAP, STRING, INT32, FLOAT32, PTR, WSTRING, COLOR, UINT64, END = 0, 1, 2, 3, 4, 5, 6, 7, 8
TAG = 'nx2steamos'   # every shortcut this tool makes carries it, which is how it finds its own


class VdfError(ValueError):
    pass


@dataclass
class Int32:
    """A KeyValues int32 (Python ints alone would not say which width to write back)."""
    value: int


@dataclass
class UInt64:
    value: int


@dataclass
class Float32:
    value: float


def _cstr(b: bytes, i: int) -> tuple[str, int]:
    j = b.find(b'\0', i)
    if j < 0: raise VdfError(f'unterminated string at {i}')
    return b[i:j].decode('utf-8', errors='surrogateescape'), j + 1


def _parse_map(b: bytes, i: int) -> tuple[dict, int]:
    out: dict = {}
    while True:
        if i >= len(b): raise VdfError('missing end of map')
        t = b[i]; i += 1
        if t == END: return out, i
        name, i = _cstr(b, i)
        if t == MAP: out[name], i = _parse_map(b, i)
        elif t == STRING: out[name], i = _cstr(b, i)
        elif t == INT32: out[name] = Int32(struct.unpack_from('<i', b, i)[0]); i += 4
        elif t == UINT64: out[name] = UInt64(struct.unpack_from('<Q', b, i)[0]); i += 8
        elif t == FLOAT32: out[name] = Float32(struct.unpack_from('<f', b, i)[0]); i += 4
        else: raise VdfError(f'unknown field type {t:#x} at {i - 1}')


def loads(b: bytes) -> dict:
    """The whole file as nested dicts; values are str, Int32, UInt64, Float32 or dict, in file order."""
    if not b: return {'shortcuts': {}}
    out, i = _parse_map(b, 0)
    if i < len(b) and b[i:] != b'\x08': raise VdfError(f'{len(b) - i} bytes after the root map')
    return out


def _dump_map(m: dict, out: bytearray) -> None:
    for k, v in m.items():
        name = str(k).encode('utf-8', errors='surrogateescape') + b'\0'
        if isinstance(v, dict): out += bytes([MAP]) + name; _dump_map(v, out)
        elif isinstance(v, str): out += bytes([STRING]) + name + v.encode('utf-8', errors='surrogateescape') + b'\0'
        elif isinstance(v, Int32): out += bytes([INT32]) + name + struct.pack('<i', v.value)
        elif isinstance(v, bool) or isinstance(v, int): out += bytes([INT32]) + name + struct.pack('<i', int(v))
        elif isinstance(v, UInt64): out += bytes([UINT64]) + name + struct.pack('<Q', v.value)
        elif isinstance(v, Float32): out += bytes([FLOAT32]) + name + struct.pack('<f', v.value)
        else: raise VdfError(f'{k}: cannot write a {type(v).__name__}')
    out.append(END)


def dumps(root: dict) -> bytes:
    out = bytearray(); _dump_map(root, out); out.append(END)   # Steam's own files end with the root map's end and one more 0x08
    return bytes(out)


def shortcut_appid(exe: str, name: str) -> int:
    """The id Steam gives a shortcut (unsigned 32 bits): CRC-32 of the quoted Exe field followed by the name, with the top bit set. Grid artwork is named after it,
    and steam://rungameid/ takes (appid << 32) | 0x02000000."""
    return (zlib.crc32((exe + name).encode('utf-8')) & 0xFFFFFFFF) | 0x80000000


def rungameid(appid: int) -> int:
    return (appid << 32) | 0x02000000


def _signed(u: int) -> int:
    return u - (1 << 32) if u & 0x80000000 else u


def quote(path: str) -> str:
    return '"' + path + '"'


@dataclass
class Shortcut:
    name: str                       # AppName: the game's own title (NACP)
    exe: str                        # the launcher's absolute path, unquoted
    start_dir: str                  # its folder
    icon: str = ''                  # an absolute PNG path
    launch_options: str = ''
    tags: list[str] = field(default_factory=lambda: [TAG])

    @property
    def appid(self) -> int:
        return shortcut_appid(quote(self.exe), self.name)

    def fields(self, old: dict | None = None) -> dict:
        """The entry Steam writes, in its own order; fields Steam added to an existing entry (last play time, hidden, overlay) are kept."""
        old = old or {}
        def keep(k, default): return old.get(k, default)
        tags = {str(i): t for i, t in enumerate(dict.fromkeys(list(self.tags) + [t for t in (old.get('tags') or {}).values() if isinstance(t, str)]))}
        return {
            'appid': Int32(_signed(self.appid)), 'AppName': self.name, 'Exe': quote(self.exe), 'StartDir': quote(self.start_dir), 'icon': self.icon,
            'ShortcutPath': keep('ShortcutPath', ''), 'LaunchOptions': self.launch_options, 'IsHidden': keep('IsHidden', Int32(0)),
            'AllowDesktopConfig': keep('AllowDesktopConfig', Int32(1)), 'AllowOverlay': keep('AllowOverlay', Int32(1)), 'OpenVR': keep('OpenVR', Int32(0)),
            'Devkit': keep('Devkit', Int32(0)), 'DevkitGameID': keep('DevkitGameID', ''), 'DevkitOverrideAppID': keep('DevkitOverrideAppID', Int32(0)),
            'LastPlayTime': keep('LastPlayTime', Int32(0)), 'FlatpakAppID': keep('FlatpakAppID', ''), 'tags': tags,
        }


def _is_ours(entry: dict, sc: Shortcut) -> bool:
    """The entry this tool made for the same game folder: same launcher, or our tag on an entry whose launcher lived in a folder of the same name (a moved port)."""
    exe = entry.get('Exe', '')
    if isinstance(exe, str) and exe.strip('"') == sc.exe: return True
    tags = entry.get('tags') or {}
    return TAG in [t for t in tags.values() if isinstance(t, str)] and isinstance(exe, str) and Path(exe.strip('"')).parent.name == Path(sc.exe).parent.name


def upsert(root: dict, sc: Shortcut) -> tuple[dict, bool]:
    """root (a loaded shortcuts.vdf) with sc added, or the entry already made for it updated in place. Returns (root, added)."""
    shortcuts = root.setdefault('shortcuts', {})
    for k, e in shortcuts.items():
        if isinstance(e, dict) and _is_ours(e, sc):
            shortcuts[k] = sc.fields(e); return root, False
    n = 0
    while str(n) in shortcuts: n += 1
    shortcuts[str(n)] = sc.fields(); return root, True


def remove(root: dict, exe: str) -> bool:
    """Drops the shortcut whose launcher is exe; the remaining entries are numbered again from 0, as Steam numbers them."""
    shortcuts = root.get('shortcuts', {})
    kept = [e for e in shortcuts.values() if not (isinstance(e, dict) and str(e.get('Exe', '')).strip('"') == exe)]
    if len(kept) == len(shortcuts): return False
    root['shortcuts'] = {str(i): e for i, e in enumerate(kept)}; return True


# ---------------- where Steam lives ----------------

def steam_roots(home: Path | None = None) -> list[Path]:
    """Steam installations under a home folder: the native client (SteamOS's), then Flatpak's."""
    home = home or Path.home()
    cands = [home / '.local/share/Steam', home / '.steam/steam', home / '.steam/root', home / '.var/app/com.valvesoftware.Steam/.local/share/Steam']
    seen, out = set(), []
    for c in cands:
        try: r = c.resolve()
        except OSError: continue
        if r in seen or not (r / 'userdata').is_dir(): continue
        seen.add(r); out.append(r)
    return out


def _text_vdf_users(text: str) -> list[tuple[str, dict]]:
    """loginusers.vdf (text KeyValues): [(SteamID64, {key: value})], enough of the format for this one file."""
    users, cur, depth, key = [], None, 0, None
    for tok in re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text):
        s, brace = tok
        if brace == '{':
            depth += 1
            if depth == 2 and key: cur = {}; users.append((key, cur))
            key = None
        elif brace == '}':
            depth -= 1
            if depth < 2: cur = None
        elif key is None: key = s
        else:
            if cur is not None and depth == 2: cur[key] = s
            key = None
    return users


def account_dirs(root: Path) -> list[Path]:
    """The userdata folders, the most recent login first (loginusers.vdf's MostRecent, then the newest folder)."""
    dirs = [d for d in (root / 'userdata').iterdir() if d.is_dir() and d.name.isdigit() and d.name != '0']
    recent = None
    lu = root / 'config/loginusers.vdf'
    if lu.is_file():
        for sid, u in _text_vdf_users(lu.read_text(errors='replace')):
            if u.get('MostRecent') == '1' and sid.isdigit(): recent = str(int(sid) & 0xFFFFFFFF)
    dirs.sort(key=lambda d: (d.name != recent, -d.stat().st_mtime))
    return dirs


def shortcuts_path(account: Path) -> Path:
    return account / 'config/shortcuts.vdf'


def grid_dir(account: Path) -> Path:
    return account / 'config/grid'


def grid_names(appid: int) -> dict[str, str]:
    """The artwork files Steam's library reads for a shortcut, by kind: portrait capsule (600x900), wide capsule (920x430), hero (1920x620), logo, icon."""
    return {'portrait': f'{appid}p.png', 'wide': f'{appid}.png', 'hero': f'{appid}_hero.png', 'logo': f'{appid}_logo.png', 'icon': f'{appid}_icon.png'}


def steam_running() -> bool:
    """Whether a Steam client runs for this user (its pid file names a live steam process)."""
    for pid_file in (Path.home() / '.steam/steam.pid', Path.home() / '.steam/steam/steam.pid'):
        try: pid = int(pid_file.read_text().strip())
        except (OSError, ValueError): continue
        try: comm = Path(f'/proc/{pid}/comm').read_text().strip()
        except OSError: continue
        if comm.startswith('steam'): return True
    if os.path.isdir('/proc'):
        for p in Path('/proc').iterdir():
            if not p.name.isdigit(): continue
            try:
                if p.joinpath('comm').read_text().strip() in ('steam', 'steamwebhelper') and p.stat().st_uid == os.getuid(): return True
            except OSError: continue
    return False


# ---------------- putting a game in the library ----------------

class SteamError(RuntimeError):
    pass


def desktop_entry(sc: Shortcut) -> str:
    """The .desktop file Steam's addnonsteamgame takes: its Exec, name, start folder and icon become the shortcut's"""
    return ('[Desktop Entry]\nType=Application\n'
            f'Name={sc.name}\nExec={quote(sc.exe)}{(" " + sc.launch_options) if sc.launch_options else ""}\nPath={sc.start_dir}\n'
            + (f'Icon={sc.icon}\n' if sc.icon else '') + 'Categories=Game;\nComment=Ported with nx2steamos\n')


def _ours_in(path: Path, sc: Shortcut):
    """The entry of shortcuts.vdf that starts sc's launcher, and its appid (unsigned), or (None, None)"""
    try: root = loads(path.read_bytes())
    except (OSError, VdfError): return None, None
    for e in (root.get('shortcuts') or {}).values():
        if isinstance(e, dict) and str(e.get('Exe', '')).strip('"') == sc.exe:
            a = e.get('appid'); return e, (a.value & 0xFFFFFFFF) if isinstance(a, Int32) else None
    return None, None


def _steam_pids():
    out = []
    if not os.path.isdir('/proc'): return out
    for p in Path('/proc').iterdir():
        if not p.name.isdigit(): continue
        try:
            if p.joinpath('comm').read_text().strip() == 'steam' and p.stat().st_uid == os.getuid(): out.append(int(p.name))
        except OSError: continue
    return out


def stop_steam(timeout=40) -> None:
    subprocess.run(['steam', '-shutdown'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    end = time.time() + timeout
    while time.time() < end:
        if not steam_running(): return
        time.sleep(0.5)
    raise SteamError(f'Steam did not quit within {timeout} s')


def start_steam() -> None:
    subprocess.Popen(['steam', '-silent'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def place_art(account: Path, appid: int, art: dict) -> list:
    """The artwork PNGs (portrait, wide, hero) under Steam's names for appid in config/grid; the names written"""
    g = grid_dir(account); g.mkdir(parents=True, exist_ok=True)
    names = grid_names(appid); done = []
    for kind, src in (art or {}).items():
        if kind in names and kind != 'icon' and Path(src).is_file(): shutil.copyfile(src, g / names[kind]); done.append(names[kind])
    return done


def register(sc: Shortcut, art: dict | None = None, account: str = '', restart: bool = False, wait: float = 20, log=print) -> dict:
    """sc in the library of the Steam account (its userdata number; the last one logged in by default), with its artwork. Returns what was done"""
    roots = steam_roots()
    if not roots: raise SteamError('no Steam installation in this home folder (~/.local/share/Steam)')
    root = roots[0]; accts = account_dirs(root)
    acct = next((a for a in accts if a.name == account), None) if account else (accts[0] if accts else None)
    if not acct: raise SteamError(f'no Steam account {account or "logged in"} under {root / "userdata"}')
    path = shortcuts_path(acct); restarted = False
    if steam_running() and restart:
        log('stopping Steam to write its shortcuts'); stop_steam(); restarted = True
    if not steam_running():   # the file itself, under the id this tool computes (Steam keeps a valid id it finds there)
        old = path.read_bytes() if path.exists() else b''
        bak = path.with_name(path.name + '.nx2steamos-backup')
        if old and not bak.exists(): bak.write_bytes(old)   # the player's file as it was before this tool first touched it
        root_vdf = loads(old); root_vdf, added = upsert(root_vdf, sc)
        path.parent.mkdir(parents=True, exist_ok=True); tmp = path.with_name(path.name + '.tmp'); tmp.write_bytes(dumps(root_vdf)); tmp.replace(path)
        appid, how = sc.appid, 'added to shortcuts.vdf' if added else 'updated in shortcuts.vdf'
    else:   # Steam adds it, and gives it an id of its own: read back once Steam has written the file
        entry, appid = _ours_in(path, sc)
        if entry is None:
            desk = Path(sc.start_dir) / 'nx2steamos.desktop'; desk.write_text(desktop_entry(sc), encoding='utf-8')
            Path('/tmp/addnonsteamgamefile').touch()   # the marker Valve's steamos-add-to-steam leaves: add without asking
            subprocess.run(['steam', 'steam://addnonsteamgame/' + urllib.parse.quote(str(desk))], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            end = time.time() + wait
            while time.time() < end and appid is None: time.sleep(0.5); entry, appid = _ours_in(path, sc)
            if appid is None: raise SteamError('Steam was asked to add the game but has not written it to its shortcuts yet: check the library, or run again with --restart-steam')
            how = 'added by Steam (steam://addnonsteamgame)'
        else: how = 'already in the library'
    placed = place_art(acct, appid, art or {})
    if restarted: log('starting Steam again'); start_steam()
    return {'appid': appid, 'rungameid': rungameid(appid), 'account': acct.name, 'how': how, 'art': placed, 'restarted': restarted}


def _main(argv):
    ap = argparse.ArgumentParser(prog='steam.py', description='Put a game in this Steam library as a non-Steam shortcut (nx2steamos).')
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('register'); r.add_argument('--name', required=True); r.add_argument('--exe', required=True); r.add_argument('--start-dir', required=True)
    r.add_argument('--icon', default=''); r.add_argument('--options', default=''); r.add_argument('--art', action='append', default=[], help='kind=path (portrait, wide, hero)')
    r.add_argument('--account', default=''); r.add_argument('--restart-steam', action='store_true')
    sub.add_parser('list')
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'list':
            roots = steam_roots(); rows = []
            for acct in (account_dirs(roots[0]) if roots else []):
                p = shortcuts_path(acct)
                for e in (loads(p.read_bytes()).get('shortcuts') or {}).values() if p.exists() else []:
                    if isinstance(e, dict) and TAG in [t for t in (e.get('tags') or {}).values() if isinstance(t, str)]:
                        rows.append({'account': acct.name, 'name': e.get('AppName', ''), 'exe': str(e.get('Exe', '')).strip('"'), 'appid': e['appid'].value & 0xFFFFFFFF if isinstance(e.get('appid'), Int32) else None})
            print(json.dumps(rows)); return 0
        sc = Shortcut(a.name, a.exe, a.start_dir, a.icon, a.options)
        res = register(sc, dict(x.split('=', 1) for x in a.art), a.account, a.restart_steam, log=lambda m: print(m, file=sys.stderr))
        print(json.dumps(res)); return 0
    except (SteamError, VdfError, OSError, subprocess.SubprocessError) as e:
        print(json.dumps({'error': str(e)})); return 1


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))
