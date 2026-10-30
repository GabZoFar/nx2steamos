#!/usr/bin/env python3
# A game profile's SteamOS keys: dm/games/<id>/steamos.tsv (key <TAB> value, # comments), beside the profile's game.tsv (nx2android's android.tsv is the model).
# The SteamOS host replays NVN with the Android port's Vulkan backend (dm/android/gfx_vk_*.c), so a profile brought up on Android (an android.tsv) is offered on SteamOS
# too, with status untested and the Android profile's setting and env rows, until it has a steamos.tsv of its own.
# Keys (docs/ARCHITECTURE.md "Game profiles on SteamOS"):
#   status       playable | wip (plays, but parts are missing: docs/PORT_STATUS.md says which) | boots | untested | experimental
#   tested       <version> <device>: what the status was checked on
#   requires     device features, separated by spaces: bcn (BC texture compression; every Adreno SteamOS runs on has it)
#   setting      <name>=<value>, one row each: the pause menu's defaults on first launch (docked, fps, rumble)
#   env          <KEY>=<VALUE>, one row each: host switches the game needs on SteamOS (the game folder's env.txt)
# Usage: steamos_profile.py header <id> <out.h> (steamos_profile.h for the host, written only when it changes); steamos_profile.py list;
#   steamos_profile.py <id> [<key>] (the profile's keys, or one key's value)
# From Python: import steamos_profile; p = steamos_profile.load(<id>) (None when the game has neither steamos.tsv nor android.tsv); p.status, p.settings, p.env, p.source
import re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEAMOS = HERE.parent
sys.path.insert(0, str(STEAMOS.parent / 'games'))
import game   # noqa: E402  (the profile folders, DM_GAMES_DIR included)

STATUS = ('playable', 'wip', 'boots', 'untested', 'experimental')
SETTINGS = {'docked': ('0', '1'), 'fps': ('0', '1'), 'rumble': ('0', '1')}   # the pause menu's settings (steamos/front_sdl.c settings_load)
REQUIRES = ('bcn',)
SINGLE = ('status', 'tested', 'requires')
REPEAT = ('setting', 'env')
ANDROID_SETTINGS = ('docked', 'fps', 'rumble')   # the android.tsv settings that mean the same here (overlay is Android's on-screen gamepad)


class ProfileError(Exception):
    pass


def _rows(path):
    rows = []
    for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'): continue
        k, _, v = line.partition('\t'); rows.append((n, k.strip(), v.strip()))
    return rows


class SteamosProfile:
    def __init__(self, g):
        self.game, self.id = g, g.id
        own = g.dir / 'steamos.tsv'
        self.rows = []
        if own.is_file():
            self.path, self.source = own, 'steamos.tsv'
            for n, k, v in _rows(own):
                if k not in SINGLE + REPEAT: raise ProfileError(f'{own}:{n}: unknown key {k!r} (keys: {", ".join(SINGLE + REPEAT)})')
                if k in SINGLE and any(r[0] == k for r in self.rows): raise ProfileError(f'{own}:{n}: {k} given twice')
                self.rows.append((k, v))
        else:   # brought up on Android only: the same Vulkan backend, not checked on SteamOS yet
            self.path, self.source = g.dir / 'android.tsv', 'android.tsv'
            self.rows.append(('status', 'untested'))
            for _, k, v in _rows(self.path):
                if k == 'requires': self.rows.append((k, ' '.join(r for r in v.split() if r in REQUIRES)))
                elif k == 'env': self.rows.append((k, v))
                elif k == 'setting' and v.partition('=')[0] in ANDROID_SETTINGS: self.rows.append((k, v))
        self.check()

    def get(self, key, default=''):
        return next((v for k, v in self.rows if k == key), default)

    def values(self, key):
        return [v for k, v in self.rows if k == key]

    @property
    def name(self): return self.game.get('name', self.id)
    @property
    def status(self): return self.get('status', 'experimental')
    @property
    def tested(self): return self.get('tested')
    @property
    def requires(self): return self.get('requires').split()

    @property
    def settings(self):   # {'docked': '1', ...}: the defaults the game folder's settings.txt starts with
        return dict(v.split('=', 1) for v in self.values('setting'))

    @property
    def env(self):   # ['KEY=VALUE', ...]
        return self.values('env')

    def check(self):
        bad = []
        if self.status not in STATUS: bad.append(f'status {self.status!r}: one of {", ".join(STATUS)}')
        if self.source == 'steamos.tsv' and self.status in ('playable', 'wip') and not self.tested: bad.append(f'status {self.status} needs a tested row')
        bad += [f'requires {r!r}: one of {", ".join(REQUIRES)}' for r in self.requires if r not in REQUIRES]
        for v in self.values('setting'):
            k, eq, val = v.partition('=')
            if not eq or k not in SETTINGS or val not in SETTINGS[k]: bad.append(f'setting {v!r}: ' + ', '.join(f'{n}={"|".join(vs)}' for n, vs in SETTINGS.items()))
        bad += [f'env {v!r}: KEY=VALUE' for v in self.values('env') if not re.fullmatch(r'[A-Z_][A-Z0-9_]*=.*', v)]
        if bad: raise ProfileError(f'{self.path}: ' + '; '.join(bad))


def has_profile(g): return (g.dir / 'steamos.tsv').is_file() or (g.dir / 'android.tsv').is_file()


def load(gid):
    """The SteamOS profile of a game profile (a title ID or a folder), None when the game has neither steamos.tsv nor android.tsv"""
    g = game.load(gid)
    return SteamosProfile(g) if has_profile(g) else None


def all_profiles():   # every game profile, with its SteamOS profile or None
    return [(g, SteamosProfile(g) if has_profile(g) else None) for g in game.all_games()]


def header(p):
    """steamos_profile.h: what the profile says about itself, for the host's log"""
    src = f'dm/games/{p.id}/{p.source}' if p else 'no SteamOS or Android profile'
    return (f'// Generated by dm/steamos/tools/steamos_profile.py from {src}; do not edit\n#pragma once\n'
            f'#define DM_STEAMOS_STATUS "{p.status if p else "none"}"\n')


if __name__ == '__main__':
    a = sys.argv[1:]
    try:
        if len(a) == 3 and a[0] == 'header':
            out = Path(a[2]); g = game.load(a[1])
            text = header(SteamosProfile(g) if has_profile(g) else None)
            out.parent.mkdir(parents=True, exist_ok=True)
            if not (out.exists() and out.read_text() == text):   # written only when it changes, as game.py's header: an unchanged header must not rebuild the host
                tmp = out.with_name(out.name + '.tmp'); tmp.write_text(text); tmp.replace(out)
        elif a == ['list']:
            for g, p in all_profiles():
                if p: print(f'{g.id}\t{p.status}\t{p.source}\t{g.get("name", "")}')
        elif len(a) in (1, 2):
            p = load(a[0])
            if not p: sys.exit(f'{a[0]} has neither steamos.tsv nor android.tsv: not ported to SteamOS')
            if len(a) == 2: print(getattr(p, a[1]) if a[1] in ('status', 'tested', 'source') else ' '.join(p.values(a[1])))
            else:
                for k, v in p.rows: print(f'{k}\t{v}')
        else:
            sys.exit('usage: steamos_profile.py header <id> <out.h> | list | <id> [<key>]')
    except ProfileError as e:
        sys.exit(f'[RED] {e}')
