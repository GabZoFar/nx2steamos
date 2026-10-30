# python -m unittest discover tests: the Steam Deck engine's port (porter/deck.py) without game files or a device. The game code build runs on recomp/'s made-up modules
# (recomp/tools/test_layout.py) when Zig and the generator's modules are there (the dev containers), and is skipped otherwise.
import hashlib, json, os, re, shutil, struct, subprocess, sys, tempfile, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault('NX2S_HOME', tempfile.mkdtemp(prefix='nx2s-test-home-'))

from porter import deck, gamedir, install, remote, toolchain   # noqa: E402
from porter.upstream import BuildError, game, steamos_profile  # noqa: E402

BOTW = '01007EF00011E000'


def nso_with_build_id(path, bid):   # an NSO header, enough for its build ID (0x40, 32 bytes): what check_build reads
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'NSO0' + bytes(0x3C) + bytes.fromhex(bid) + bytes(0x100 - 0x40 - len(bytes.fromhex(bid))))


def dyn_symbols(so):   # (e_machine, e_type, names in .dynsym) of a little-endian ELF64
    b = Path(so).read_bytes()
    assert b[:4] == b'\x7fELF' and b[4] == 2
    e_type, e_machine = struct.unpack_from('<HH', b, 0x10)
    shoff, = struct.unpack_from('<Q', b, 0x28); shentsize, shnum = struct.unpack_from('<HH', b, 0x3A)
    secs = [struct.unpack_from('<IIQQQQIIQQ', b, shoff + i * shentsize) for i in range(shnum)]
    names = []
    for s in secs:
        if s[1] != 11: continue   # SHT_DYNSYM
        strtab = secs[s[6]]
        for off in range(s[4], s[4] + s[5], 24):
            n = struct.unpack_from('<I', b, off)[0]
            names.append(b[strtab[4] + n:b.index(b'\0', strtab[4] + n)].decode())
    return e_machine, e_type, names


class Knowledge(unittest.TestCase):
    def test_gamelib_id_requires_custom_runtime(self):
        self.assertIsNone(deck.runtime_for(deck.gamelib_id()))
        self.assertIsNone(deck.runtime_for(deck.gamelib_id('x86-64-v2')))
        self.assertIn('build its AppImage', deck.runtime_fix(None))

    def test_gamelib_srcs_are_the_makefiles(self):   # (the flags are compared with make's expansion in devtools/check.sh deck)
        mk = (REPO / 'recomp/Makefile').read_text()
        got = []
        for m in re.finditer(r'^GAMELIB_SRCS\s*[:+]?=((?:.*\\\n)*.*)$', mk, re.M): got += m.group(1).replace('\\\n', ' ').split()
        self.assertEqual(tuple(got), deck.GAMELIB_SRCS)

    def test_zig_flags_are_the_start_screens(self):   # the release's savagewind has this GAMELIB_CFLAGS: zig cc gets it with -march as -mcpu, then GENFLAGS_ZIG
        flags = deck.zig_cflags()
        self.assertEqual(' '.join(flags[:7]), '-O1 -fno-strict-aliasing -Wno-return-type -Wno-unused-label -fvisibility=hidden -mcpu=x86_64_v3 -fPIC')
        self.assertEqual(flags[7:], deck.GENFLAGS_ZIG.split())

    def test_versions_match_the_generators(self):   # site-based versions are in the runtime; site-free versions are runtime-only
        sys.path.insert(0, str(REPO / 'recomp/tools'))
        import game_sites
        self.assertTrue(all((v.name, v.build_id, v.supported) in [(r.name, r.build_id, r.supported) for r in deck.game_versions()]
                            for v in game_sites.VERSIONS))
        self.assertTrue(all(v.romfs_files > 40000 for v in deck.game_versions()))
        self.assertEqual(deck.game_versions()[-1].romfs_marker, 'Pack/ZsDic.pack.zs')

    def test_profile(self):
        p = deck.profile(game.load(BOTW))
        self.assertEqual((p.id, p.status, p.name), (BOTW, 'untested', 'The Legend of Zelda: Breath of the Wild'))
        self.assertIn(BOTW, [q.id for q in deck.all_profiles()])
        totk = deck.profile(game.load('0100F2C0115B6000'))
        self.assertEqual((totk.status, totk.sites, totk.env), ('experimental', False, [('SAVAGEWIND_NO_SCALE', '1'), ('SAVAGEWIND_HANDHELD', '1')]))
        self.assertIn("export SAVAGEWIND_NO_SCALE='1'\n", gamedir.deck_launcher('Z', totk.id, 'r.AppImage', 'k', totk.env))
        self.assertNotIn('export SAVAGEWIND_NO', gamedir.deck_launcher('B', BOTW, 'r.AppImage', 'k', p.env))
        with tempfile.TemporaryDirectory() as d:
            dst = Path(d) / BOTW; shutil.copytree(game.load(BOTW).dir, dst, ignore=shutil.ignore_patterns('*.tsv.conflicts.tsv'))
            g = game.load(str(dst))
            for bad in ('status\tplayable\n', 'speed\t1\n', 'status\tfine\n', 'status\tboots\nstatus\tboots\n', 'env\tA\n',
                        "env\tA=it's\n", 'env\tlower=1\n'):
                (dst / 'deck.tsv').write_text(bad)
                with self.assertRaises(steamos_profile.ProfileError, msg=bad): deck.DeckProfile(g)
            (dst / 'deck.tsv').write_text('status\tplayable\ntested\t1.9.0 Steam Deck OLED\n')
            self.assertEqual(deck.DeckProfile(g).tested, '1.9.0 Steam Deck OLED')


class CheckBuild(unittest.TestCase):
    V = [deck.GameVersion('1.0.0', '0123456789abcdef', True, 0), deck.GameVersion('2.0.0', 'fedcba9876543210', False, 0),
         deck.GameVersion('3.0.0', '00112233aabbccdd', True, 10)]

    def setUp(self):
        self.d = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d)

    def files(self, bid, version, n=1):
        nso_with_build_id(self.d / 'ExeFS/main', bid + '00' * 24)
        (self.d / 'RomFS/System').mkdir(parents=True, exist_ok=True)
        if version is not None: (self.d / 'RomFS/System/Version.txt').write_bytes(version)
        for i in range(n): (self.d / f'RomFS/f{i}').write_bytes(b'x')

    def check(self): return deck.check_build(self.d / 'ExeFS', self.d / 'RomFS', self.V)

    def test_supported_version(self):
        self.files('0123456789abcdef', '﻿1.0.0\r\n'.encode()); self.assertEqual(self.check().name, '1.0.0')   # (a byte order mark and a line end, as the game's own)

    def test_romfs_marker_without_version_file(self):
        v = deck.GameVersion('1.4.3', '277178b7dba1b6d4', True, 2, 'Pack/ZsDic.pack.zs')
        self.files(v.build_id, None)
        with self.assertRaisesRegex(BuildError, 'no Pack/ZsDic.pack.zs'):
            deck.check_build(self.d / 'ExeFS', self.d / 'RomFS', [v])
        marker = self.d / 'RomFS' / v.romfs_marker
        marker.parent.mkdir(parents=True); marker.write_bytes(b'x')
        self.assertEqual(deck.check_build(self.d / 'ExeFS', self.d / 'RomFS', [v]), v)

    def test_refusals(self):
        for bid, ver, n, why in (('ffffffffffffffff', b'1.0.0', 1, 'not one the Steam Deck engine knows'), ('fedcba9876543210', b'2.0.0', 1, 'not supported yet'),
                                 ('0123456789abcdef', b'0.9.0', 1, "says '0.9.0'"), ('0123456789abcdef', None, 1, 'no System/Version.txt'),
                                 ('00112233aabbccdd', b'3.0.0', 5, 'has 6 files')):
            shutil.rmtree(self.d); self.d.mkdir(); self.files(bid, ver, n)
            with self.assertRaisesRegex(BuildError, why): self.check()


class Folder(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d)
        for f, b in (('ex/main', b'NSO0'), ('ex/main.npdm', b'META'), ('ex/sdk', b'NSO0'), ('rom/System/Version.txt', b'1.9.0'), ('rom/Pack/a.pack', b'pack'),
                     ('lib/swgame.so', b'\x7fELF'), ('lib/swgame.so.id', b'f0cd698371c0a703\n'), ('art/portrait.png', b'p')):
            (self.d / f).parent.mkdir(parents=True, exist_ok=True); (self.d / f).write_bytes(b)
        # a stand-in AppImage: it logs its arguments and environment, and writes the data image when asked to build it
        app = self.d / 'Fake-x86_64.AppImage'
        app.write_text('#!/bin/sh\necho "$* | $SAVAGEWIND_DATA_ROOT | ${APPIMAGE_EXTRACT_AND_RUN:-}" >> "$LOG"\n'
                       'if [ "$1" = --build-romfs ]; then [ -n "$FAIL" ] && exit 3; echo image > "$2/romfs.bin"; fi\n')
        app.chmod(0o755)
        self.man = gamedir.build_deck(self.d / 'out/ZeldaBreathOfTheWild', profile=deck.profile(game.load(BOTW)), version='v1114112', display='1.9.0',
                                      name='The Legend of Zelda: Breath of the Wild', engine_version='1.9.0', exefs=self.d / 'ex', romfs=self.d / 'rom',
                                      gamelib=self.d / 'lib/swgame.so', appimage=app, runtime='Savage Wind test', art={'portrait': self.d / 'art/portrait.png'},
                                      romfs_key='k' * 64)
        self.g = self.d / 'out/ZeldaBreathOfTheWild'

    def test_layout_and_manifest(self):
        g, man = self.g, self.man
        for f in ('game/exefs/main', 'game/exefs/main.npdm', 'game/romfs/System/Version.txt', 'game/swgame.so', 'game/swgame.so.id', 'bin/Fake-x86_64.AppImage', 'art/portrait.png'):
            self.assertTrue((g / f).is_file(), f)
        self.assertTrue(os.access(g / gamedir.LAUNCHER, os.X_OK) and os.access(g / 'bin/Fake-x86_64.AppImage', os.X_OK))
        self.assertEqual(gamedir.read_manifest(g), man)
        self.assertEqual((man['engine'], man['engine_version'], man['gamelib_id'], man['romfs_bytes'], man['requires']), ('recomp', '1.9.0', 'f0cd698371c0a703', 9, []))
        self.assertEqual(gamedir.keep_on_device(man), gamedir.DECK_KEEP); self.assertEqual(gamedir.keep_on_device({}), gamedir.KEEP_ON_DEVICE)

    def launch(self, *args, fail=False):
        home = self.d / 'home'; log = self.d / 'log'
        env = dict(os.environ, HOME=str(home), LOG=str(log), PATH='/usr/bin:/bin'); env.pop('XDG_DATA_HOME', None)
        if fail: env['FAIL'] = '1'
        r = subprocess.run([str(self.g / gamedir.LAUNCHER), *args], env=env, capture_output=True, text=True)
        lines = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return r.returncode, lines

    def test_launcher(self):
        state = f'{self.d}/home/.local/share/nx2steamos/{BOTW}'   # ($HOME as given; the game folder as the launcher resolves its own path)
        game_dir = os.path.realpath(self.g / 'game')
        rc, lines = self.launch(gamedir.PREPARE)   # what an install runs: the data image, then nothing else (no FUSE here: the AppImage unpacks itself)
        self.assertEqual((rc, lines), (0, [f'--build-romfs {game_dir} | {state} | 1']))
        self.assertEqual((self.g / 'game/romfs.bin.key').read_text().strip(), 'k' * 64)
        self.assertTrue(Path(state).is_dir())
        rc, lines = self.launch('--config')   # Steam's start: straight to the runtime, the image already built; launch options after the game folder
        self.assertEqual((rc, lines), (0, [f'{game_dir} --config | {state} | 1']))
        (self.g / 'game/romfs.bin.key').write_text('old\n')   # an older RomFS's image: built again, and a failed build leaves no key (the next start tries again)
        rc, lines = self.launch(fail=True)
        self.assertEqual((rc, len(lines)), (3, 1)); self.assertFalse((self.g / 'game/romfs.bin.key').exists())


class Install(unittest.TestCase):
    MAN = {'name': 'Game', 'engine': 'recomp', 'requires': []}
    DECK = {'arch': 'x86_64', 'glibc': '2.41', 'cpu': 'avx2 fma bmi2 ', 'fuse': '/usr/bin/fusermount3', 'lib.libvulkan.so.1': '/usr/lib/libvulkan.so.1'}

    def test_device(self):
        install.check_device(self.DECK, self.MAN)
        install.check_device(dict(self.DECK, cpu='', fuse=''), self.MAN)   # warnings only: a CPU without the game code's target, no FUSE
        for bad in ({'arch': 'aarch64', 'glibc': '2.41'}, {'arch': 'riscv64'}, dict(self.DECK, glibc='2.31')):
            with self.assertRaises(BuildError, msg=bad): install.check_device(bad, self.MAN)
        with self.assertRaisesRegex(BuildError, '--engine recomp'): install.check_device(self.DECK, {'name': 'Game', 'requires': []})   # an ARM64 folder on a Deck

    def test_local_push_keeps_the_data_image_by_link(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); (d / 'src/game/romfs').mkdir(parents=True); (d / 'src/game/romfs/a').write_text('new')
            (d / 'dst/game').mkdir(parents=True); (d / 'dst/game/romfs.bin').write_text('image'); (d / 'dst/game/romfs.bin.key').write_text('k'); (d / 'dst/game/old').write_text('x')
            ino = (d / 'dst/game/romfs.bin').stat().st_ino
            remote.Local().push_tree(d / 'src', d / 'dst', keep=gamedir.DECK_KEEP)
            self.assertEqual(((d / 'dst/game/romfs.bin').read_text(), (d / 'dst/game/romfs.bin.key').read_text(), (d / 'dst/game/old').exists()), ('image', 'k', False))
            self.assertEqual((d / 'dst/game/romfs.bin').stat().st_ino, ino)   # the same file, not a copy read into memory


class Inputs(unittest.TestCase):
    """What a game folder gets from the Steam Deck engine before anything is extracted (stand-in NSP files: only their names are read, as the folder scan does)"""

    def port(self, *names, version=''):
        d = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, d)
        for n in names: (d / n).write_bytes(b'not a game')
        from porter import pipeline
        p = deck.DeckPort(pipeline.Options(folder=d, version=version)); p.o.folder = d
        p.inputs()
        return p

    def test_totk_site_free_profile(self):
        p = self.port('The Legend of Zelda Tears of the Kingdom [0100F2C0115B6000][v0].nsp',
                      'The Legend of Zelda Tears of the Kingdom [0100F2C0115B6800][v720896].nsp')
        self.assertEqual((p.pkg_ver, p.dp.sites, p.dp.status), ('v720896', False, 'experimental'))

    NAMES = ('Zelda BotW [01007EF00011E000][v0].nsp', 'Zelda BotW [01007EF00011E800][v1114112].nsp', 'Zelda BotW [01007EF00011F001][v196608].nsp')

    def test_versions(self):
        p = self.port(*self.NAMES)   # the newest update; the DLC is left out
        self.assertEqual((p.pkg_ver, p.dp.status, p.have['v1114112'].name), ('v1114112', 'untested', self.NAMES[1]))
        self.assertEqual(self.port(*self.NAMES, version='v0').pkg_ver, 'v0')   # a version the profile does not register: its main module decides, once extracted (check_build)
        with self.assertRaisesRegex(BuildError, r'\(v786432\) is not in the folder'): self.port(*self.NAMES, version='v786432')

    def test_a_registered_version_the_engine_does_not_run(self):   # refused before anything is extracted
        with tempfile.TemporaryDirectory() as d:
            dst = Path(d) / BOTW; shutil.copytree(game.load(BOTW).dir, dst, ignore=shutil.ignore_patterns('*.tsv.conflicts.tsv'))
            with open(dst / 'game.tsv', 'a') as f: f.write('build\tv786432\t1.6.0\tmain=00000000000000000000000000000000\n')
            old = {k: os.environ.get(k) for k in ('DM_GAMES_DIR', 'DM_GAMES_FIRST')}
            os.environ.update(DM_GAMES_DIR=d, DM_GAMES_FIRST='1')
            try:
                with self.assertRaisesRegex(BuildError, 'v786432 is version 1.6.0: the Steam Deck engine runs version 1.8.2, 1.9.0, 1.4.3'):
                    self.port(self.NAMES[0], 'Zelda BotW [01007EF00011E800][v786432].nsp')
            finally:
                for k, v in old.items():
                    if v is None: os.environ.pop(k, None)
                    else: os.environ[k] = v


class GameCode(unittest.TestCase):
    """The game code build of recomp/'s made-up modules (no game code: tools/test_layout.py makes them): the generator, zig cc for x86_64-linux-gnu, the link"""

    def test_build(self):
        zig = toolchain.find_zig()
        if not zig or not toolchain.zig_version(zig): self.skipTest('no Zig')
        if not all(deck.module_versions().values()): self.skipTest('the generator\'s modules are missing (requirements-deck.txt)')
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)   # (in a Python of its own: recomp/tools/nso.py and nxnative/dm/tools/nso.py share a name)
            subprocess.run([sys.executable, '-c', 'import sys, test_layout; test_layout.make_exefs(sys.argv[1])', str(d / 'exefs')], cwd=REPO / 'recomp/tools', check=True)
            seen = []
            lib = deck.build_game_code(d / 'exefs', d / 'out', zig=zig, jobs=os.cpu_count() or 4, env=dict(os.environ), logs=d / 'logs', sites=False,
                                       progress=lambda f, step: seen.append(f))
            machine, etype, names = dyn_symbols(lib)
            self.assertEqual((machine, etype), (62, 3))   # EM_X86_64, ET_DYN
            self.assertIn('swgame_tables', names)
            self.assertEqual((d / 'out/swgame.so.id').read_text().strip(), deck.gamelib_id())
            self.assertFalse((d / 'out/gamebuild').exists())   # the generated C and the objects are gone
            self.assertEqual(seen[-1], 1); self.assertEqual(seen, sorted(seen))
            # the library's tables name the id the runtime checks (tools/gamelib_entry.c: GAMELIB_ID in its data)
            self.assertIn(deck.gamelib_id().encode(), lib.read_bytes())


if __name__ == '__main__':
    unittest.main()
