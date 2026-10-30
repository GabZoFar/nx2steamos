# nxnative's own tools, used as libraries: the NSP script's steps (scripts/build_ipa_from_nsp.py: NSP scan, extraction, rewrite, shader split, stage reuse, progress), the game profiles
# (dm/games/game.py), the guesses from game files (dm/games/profile_guess.py), the control data reader (dm/tools/nacp.py) and the SteamOS profile (dm/steamos/tools/steamos_profile.py).
# Importing them instead of copying keeps one implementation of each step for iOS, Android and SteamOS (nx2android's porter does the same); what differs on SteamOS (the rewrite
# target, the host, the game folder, Steam) is in porter.pipeline.
import importlib.util, sys

from porter.paths import DM, NXNATIVE

for d in (DM / 'games', DM / 'tools', DM / 'steamos/tools'):
    if str(d) not in sys.path: sys.path.insert(0, str(d))

import game              # noqa: E402
import nacp              # noqa: E402
import profile_guess     # noqa: E402
import steamos_profile   # noqa: E402

PLATFORM_FILES = ('android.tsv', 'steamos.tsv')   # a platform's profile keys change no shader: they stay out of the shader stages' reuse keys


def _load_nsp():
    spec = importlib.util.spec_from_file_location('build_ipa_from_nsp', NXNATIVE / 'scripts/build_ipa_from_nsp.py')
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    m.nacp, m.pg = nacp, profile_guess   # module globals its main() sets
    if sys.platform != 'darwin': m.can_clone = lambda d: False   # APFS clones (clonefile) exist only on a Mac
    files = m.profile_files
    m.profile_files = lambda g: [p for p in files(g) if p.name not in PLATFORM_FILES]
    return m


nsp = _load_nsp()
BuildError, PROG, say = nsp.BuildError, nsp.PROG, nsp.say
