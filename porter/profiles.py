# The games the tool knows: every game profile in nxnative/dm/games, and whether it can be ported to SteamOS (a steamos.tsv, or an android.tsv: dm/steamos/tools/steamos_profile.py).
from dataclasses import dataclass, asdict

from porter.upstream import game, steamos_profile


@dataclass
class Entry:
    id: str
    name: str
    steamos: bool        # has a steamos.tsv or an android.tsv
    status: str          # playable / wip / boots / untested / experimental; 'ios only' without either
    tested: str
    source: str          # steamos.tsv / android.tsv / ''

    def as_dict(self): return asdict(self)


def entry(g, p):
    if p: return Entry(g.id, g.get('name', g.id), True, p.status, p.tested, p.source)
    return Entry(g.id, g.get('name', g.id), False, 'ios only', '', '')


def all_entries(steamos_only=True):
    rows = [entry(g, p) for g, p in steamos_profile.all_profiles()]
    return [r for r in rows if r.steamos or not steamos_only]


def by_title(tid):
    """(game profile, SteamOS profile or None) of a base game's title ID; (None, None) when no profile matches"""
    g = game.by_title(tid)
    if not g: return None, None
    return g, (steamos_profile.SteamosProfile(g) if steamos_profile.has_profile(g) else None)
