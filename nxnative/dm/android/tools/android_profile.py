#!/usr/bin/env python3
# A game profile's Android keys: dm/games/<id>/android.tsv (key <TAB> value, # comments), beside the profile's game.tsv. A profile without one has not been ported to Android.
# Keys (docs/ARCHITECTURE.md "Game profiles on Android"):
#   status       playable | wip (plays, but parts are missing: docs/PORT_STATUS.md says which) | boots | experimental
#   tested       <version> <device>: what the status was checked on
#   package      the app's package name (default org.nx2a.t<title ID in lowercase>: a package name's parts start with a letter)
#   orientation  landscape (default) | any
#   requires     device features, separated by spaces: bcn (BC texture compression)
#   setting      <name>=<value>, one row each: the pause menu's defaults on first launch (overlay, rumble, docked, fps)
#   env          <KEY>=<VALUE>, one row each: host switches the game needs on Android (the data pack's env.txt)
#   overlay      <control> <x> <y> [<r>]: moves one control of the default layout (dm/android/overlay_default.tsv)
# Usage: android_profile.py header <id> <out.h> (android_profile.h for the host: the gamepad layout, written only when it changes); android_profile.py list;
#   android_profile.py <id> [<key>] (the profile's keys, or one key's value)
# From Python: import android_profile; p = android_profile.load(<id>) (None without android.tsv); p.status, p.package, p.settings, p.env, p.overlay_table()
import os, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ANDROID = HERE.parent
sys.path.insert(0, str(ANDROID.parent / 'games'))
import game   # noqa: E402  (the profile folders, DM_GAMES_DIR included)

STATUS = ('playable', 'wip', 'boots', 'experimental')
SETTINGS = {'overlay': ('0', '1'), 'rumble': ('0', '1'), 'docked': ('0', '1'), 'fps': ('0', '1')}   # the pause menu's settings (front_android.c settings_load)
REQUIRES = ('bcn',)
KINDS = {'stick_l': 'OV_STICK_L', 'stick_r': 'OV_STICK_R', 'button': 'OV_BUTTON', 'dpad': 'OV_DPAD', 'menu': 'OV_MENU'}
SINGLE = ('status', 'tested', 'package', 'orientation', 'requires')
REPEAT = ('setting', 'env', 'overlay')


class ProfileError(Exception):
    pass


def default_layout():   # [{'name', 'kind', 'x', 'y', 'r', 'button', 'label'}] in drawing order
    rows = []
    for line in (ANDROID / 'overlay_default.tsv').read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.startswith('#'): continue
        c = line.split('\t')
        if len(c) != 7 or c[1] not in KINDS: raise ProfileError(f'overlay_default.tsv: bad row {line!r}')
        rows.append(dict(name=c[0], kind=c[1], x=c[2], y=c[3], r=c[4], button=c[5], label='' if c[6] == '~' else c[6]))
    return rows


class AndroidProfile:
    def __init__(self, g):
        self.game, self.id, self.path = g, g.id, g.dir / 'android.tsv'
        self.rows = []
        for n, line in enumerate(self.path.read_text(encoding='utf-8').splitlines(), 1):
            if not line.strip() or line.lstrip().startswith('#'): continue
            k, _, v = line.partition('\t'); k, v = k.strip(), v.strip()
            if k not in SINGLE + REPEAT: raise ProfileError(f'{self.path}:{n}: unknown key {k!r} (keys: {", ".join(SINGLE + REPEAT)})')
            if k in SINGLE and any(r[0] == k for r in self.rows): raise ProfileError(f'{self.path}:{n}: {k} given twice')
            self.rows.append((k, v))
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
    def package(self): return self.get('package') or f'org.nx2a.t{self.id.lower()}'
    @property
    def landscape(self): return self.get('orientation', 'landscape') == 'landscape'
    @property
    def requires(self): return self.get('requires').split()

    @property
    def settings(self):   # {'overlay': '1', ...}: the defaults the data pack's settings.txt starts with
        return dict(v.split('=', 1) for v in self.values('setting'))

    @property
    def env(self):   # ['KEY=VALUE', ...]
        return self.values('env')

    def overlay_table(self):
        """The gamepad layout: the default one with this profile's moves"""
        rows = default_layout(); by = {r['name']: r for r in rows}
        for v in self.values('overlay'):
            c = v.split()
            by[c[0]].update(x=c[1], y=c[2], **({'r': c[3]} if len(c) > 3 else {}))
        return rows

    def check(self):
        bad = []
        if self.status not in STATUS: bad.append(f'status {self.status!r}: one of {", ".join(STATUS)}')
        if not re.fullmatch(r'[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+', self.package): bad.append(f'package {self.package!r} is not a package name')
        if self.get('orientation', 'landscape') not in ('landscape', 'any'): bad.append('orientation: landscape or any')
        bad += [f'requires {r!r}: one of {", ".join(REQUIRES)}' for r in self.requires if r not in REQUIRES]
        for v in self.values('setting'):
            k, eq, val = v.partition('=')
            if not eq or k not in SETTINGS or val not in SETTINGS[k]: bad.append(f'setting {v!r}: ' + ', '.join(f'{n}={"|".join(vs)}' for n, vs in SETTINGS.items()))
        bad += [f'env {v!r}: KEY=VALUE' for v in self.values('env') if not re.fullmatch(r'[A-Z_][A-Z0-9_]*=.*', v)]
        names = {r['name'] for r in default_layout()}
        for v in self.values('overlay'):
            c = v.split()
            if len(c) not in (3, 4) or c[0] not in names or not all(re.fullmatch(r'\d*\.?\d+', x) for x in c[1:]):
                bad.append(f'overlay {v!r}: <control> <x> <y> [<r>], control one of {", ".join(sorted(names))}')
        if bad: raise ProfileError(f'{self.path}: ' + '; '.join(bad))


def load(gid):
    """The Android profile of a game profile (a title ID or a folder), None when the game has no android.tsv"""
    g = game.load(gid)
    return AndroidProfile(g) if (g.dir / 'android.tsv').is_file() else None


def all_profiles():   # every game profile, with its Android profile or None
    return [(g, AndroidProfile(g) if (g.dir / 'android.tsv').is_file() else None) for g in game.all_games()]


def cfloat(s): return s + ('' if '.' in s else '.0') + 'f'


def header(p):
    """android_profile.h: the gamepad layout as DM_AOV_TABLE (overlay_layout.h's OV_CTLS), and what the profile says about itself"""
    rows = p.overlay_table() if p else default_layout()
    ctl = ',\n    '.join(f'{{{KINDS[r["kind"]]}, {cfloat(r["x"])}, {cfloat(r["y"])}, {cfloat(r["r"])}, {r["button"] if r["button"] != "-" else "0"}, "{r["label"]}"}}' for r in rows)
    src = f'dm/games/{p.id}/android.tsv and dm/android/overlay_default.tsv' if p else 'dm/android/overlay_default.tsv (the profile has no android.tsv)'
    return (f'// Generated by dm/android/tools/android_profile.py from {src}; do not edit\n#pragma once\n'
            f'#define DM_ANDROID_STATUS "{p.status if p else "none"}"\n'
            f'#define DM_AOV_TABLE \\\n    {ctl.replace(chr(10), " " + chr(92) + chr(10))}\n')


if __name__ == '__main__':
    a = sys.argv[1:]
    try:
        if len(a) == 3 and a[0] == 'header':
            out = Path(a[2]); g = game.load(a[1])
            text = header(AndroidProfile(g) if (g.dir / 'android.tsv').is_file() else None)
            out.parent.mkdir(parents=True, exist_ok=True)
            if not (out.exists() and out.read_text() == text):   # written only when it changes, as game.py's header: an unchanged header must not rebuild the host
                tmp = out.with_name(out.name + '.tmp'); tmp.write_text(text); tmp.replace(out)
        elif a == ['list']:
            for g, p in all_profiles():
                if p: print(f'{g.id}\t{p.status}\t{p.package}\t{g.get("name", "")}')
        elif len(a) in (1, 2):
            p = load(a[0])
            if not p: sys.exit(f'{a[0]} has no android.tsv: not ported to Android')
            if len(a) == 2: print(getattr(p, a[1]) if a[1] in ('status', 'package', 'tested') else ' '.join(p.values(a[1])))
            else:
                for k, v in p.rows: print(f'{k}\t{v}')
        else:
            sys.exit('usage: android_profile.py header <id> <out.h> | list | <id> [<key>]')
    except ProfileError as e:
        sys.exit(f'[RED] {e}')
