# python -m unittest discover tests: the porter's parts that need no game files, no toolchain and no device.
import json, os, shutil, stat, struct, sys, tempfile, unittest, zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault('NX2S_HOME', tempfile.mkdtemp(prefix='nx2s-test-home-'))

from porter import config, gamedir, install, pipeline, profiles, remote, steam, toolchain   # noqa: E402
from porter.upstream import BuildError, game, steamos_profile                                # noqa: E402

NEO = '010043B013C5C000'   # an Android profile: offered on SteamOS as untested


class Vdf(unittest.TestCase):
    def test_roundtrip_every_type(self):
        root = {'shortcuts': {'0': {'appid': steam.Int32(-1828814555), 'AppName': 'Ünïcode', 'big': steam.UInt64(1 << 40), 'f': steam.Float32(0.5), 'tags': {'0': 'a', '1': 'b'}}}}
        b = steam.dumps(root)
        self.assertEqual(b[:11], b'\x00shortcuts\x00'); self.assertTrue(b.endswith(b'\x08\x08\x08'))
        self.assertEqual(steam.dumps(steam.loads(b)), b)
        self.assertEqual(steam.loads(b)['shortcuts']['0']['appid'].value, -1828814555)

    def test_empty_file_and_garbage(self):
        self.assertEqual(steam.loads(b''), {'shortcuts': {}})
        with self.assertRaises(steam.VdfError): steam.loads(b'\x09bad')
        with self.assertRaises(steam.VdfError): steam.loads(b'\x00shortcuts\x00\x01name\x00unterminated')

    def test_appid_is_steams_formula(self):   # crc32 of the quoted Exe and the name, top bit set; the launch id adds 0x02000000 below it
        sc = steam.Shortcut('Game', '/home/deck/Games/nx2steamos/Game/nx2steamos-run', '/home/deck/Games/nx2steamos/Game')
        want = (zlib.crc32(b'"/home/deck/Games/nx2steamos/Game/nx2steamos-run"Game') & 0xFFFFFFFF) | 0x80000000
        self.assertEqual(sc.appid, want); self.assertEqual(steam.rungameid(want), want << 32 | 0x02000000)
        self.assertEqual(sc.fields()['appid'].value & 0xFFFFFFFF, want)   # written signed, read back unsigned
        self.assertEqual(steam.grid_names(want)['portrait'], f'{want}p.png')

    def test_upsert_keeps_steams_own_fields_and_other_shortcuts(self):
        other = {'appid': steam.Int32(5), 'AppName': 'Other', 'Exe': '"/usr/bin/other"', 'StartDir': '"/"', 'tags': {}}
        root = {'shortcuts': {'0': other}}
        sc = steam.Shortcut('Game', '/g/nx2steamos-run', '/g', '/g/art/icon.png')
        root, added = steam.upsert(root, sc); self.assertTrue(added); self.assertEqual(sorted(root['shortcuts']), ['0', '1'])
        root['shortcuts']['1']['LastPlayTime'] = steam.Int32(1700000000); root['shortcuts']['1']['tags']['1'] = 'favourite'
        root, added = steam.upsert(root, steam.Shortcut('Game 1.0.1', '/g/nx2steamos-run', '/g'))
        self.assertFalse(added); e = root['shortcuts']['1']
        self.assertEqual((e['AppName'], e['LastPlayTime'].value), ('Game 1.0.1', 1700000000))
        self.assertEqual(list(e['tags'].values()), ['nx2steamos', 'favourite'])
        self.assertIs(root['shortcuts']['0'], other)

    def test_remove_renumbers(self):
        root = {'shortcuts': {}}
        for n in 'abc': root, _ = steam.upsert(root, steam.Shortcut(n, f'/{n}/nx2steamos-run', f'/{n}'))
        self.assertTrue(steam.remove(root, '/b/nx2steamos-run'))
        self.assertEqual([(k, e['AppName']) for k, e in root['shortcuts'].items()], [('0', 'a'), ('1', 'c')])
        self.assertFalse(steam.remove(root, '/b/nx2steamos-run'))

    def test_accounts_most_recent_first(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for a in ('11111', '22222', '0'): (root / 'userdata' / a / 'config').mkdir(parents=True)
            (root / 'config').mkdir()
            (root / 'config/loginusers.vdf').write_text('"users"\n{\n\t"76561197960287930"\n\t{\n\t\t"AccountName"\t\t"x"\n\t\t"MostRecent"\t\t"1"\n\t}\n}\n')
            # 76561197960287930 & 0xFFFFFFFF = 22202: not a folder here, so the newest folder comes first; with 22222's SteamID it wins
            self.assertNotIn('0', [p.name for p in steam.account_dirs(root)])
            (root / 'config/loginusers.vdf').write_text(f'"users"\n{{\n\t"{76561197960265728 + 22222}"\n\t{{\n\t\t"MostRecent"\t\t"1"\n\t}}\n}}\n')
            self.assertEqual(steam.account_dirs(root)[0].name, '22222')

    def test_desktop_entry(self):
        t = steam.desktop_entry(steam.Shortcut('Game: Edition', '/g h/nx2steamos-run', '/g h', '/g h/art/icon.png'))
        self.assertIn('Exec="/g h/nx2steamos-run"\n', t); self.assertIn('Name=Game: Edition\n', t); self.assertTrue(t.startswith('[Desktop Entry]\n'))

    def test_register_with_steam_closed(self):   # the file itself, a backup of the player's, the artwork under the computed id
        with tempfile.TemporaryDirectory() as d:
            home = Path(d); acct = home / '.local/share/Steam/userdata/12345/config'; acct.mkdir(parents=True)
            old = steam.dumps({'shortcuts': {'0': {'appid': steam.Int32(7), 'AppName': 'Mine', 'Exe': '"/x"', 'tags': {}}}}); (acct / 'shortcuts.vdf').write_bytes(old)
            art = home / 'p.png'; art.write_bytes(b'png')
            was = Path.home; Path.home = staticmethod(lambda: home); run = steam.steam_running; steam.steam_running = lambda: False
            try: res = steam.register(steam.Shortcut('Game', '/g/nx2steamos-run', '/g'), {'portrait': art}, log=lambda m: None)
            finally: Path.home = was; steam.steam_running = run
            self.assertEqual(res['how'], 'added to shortcuts.vdf')
            self.assertEqual((acct / 'shortcuts.vdf.nx2steamos-backup').read_bytes(), old)
            names = [e['AppName'] for e in steam.loads((acct / 'shortcuts.vdf').read_bytes())['shortcuts'].values()]
            self.assertEqual(names, ['Mine', 'Game']); self.assertTrue((acct / 'grid' / f'{res["appid"]}p.png').is_file())


class SteamosProfiles(unittest.TestCase):
    def test_android_profile_is_offered_untested(self):
        p = steamos_profile.load(NEO)
        self.assertEqual((p.status, p.source, p.requires), ('untested', 'android.tsv', ['bcn']))
        self.assertEqual(p.settings, {'docked': '0', 'rumble': '1'})   # Android's touch gamepad setting (overlay) is not carried over

    def test_listing(self):
        rows = profiles.all_entries()
        self.assertIn(NEO, [r.id for r in rows]); self.assertTrue(all(r.steamos for r in rows))
        self.assertGreater(len(profiles.all_entries(steamos_only=False)), len(rows))

    def test_own_file_wins_and_is_checked(self):
        with tempfile.TemporaryDirectory() as d:
            src = game.load(NEO).dir; dst = Path(d) / NEO; shutil.copytree(src, dst, ignore=shutil.ignore_patterns('*.tsv.conflicts.tsv'))
            (dst / 'steamos.tsv').write_text('status\tplayable\ntested\tv196608 AYN Odin 3\nsetting\tfps=1\nenv\tDM_X=1\n')
            g = game.load(str(dst)); p = steamos_profile.SteamosProfile(g)
            self.assertEqual((p.status, p.source, p.env, p.settings), ('playable', 'steamos.tsv', ['DM_X=1'], {'fps': '1'}))
            for bad in ('status\tplayable\n', 'nonsense\t1\n', 'setting\toverlay=1\n', 'env\tlower=1\n', 'status\tboots\nstatus\tboots\n'):
                (dst / 'steamos.tsv').write_text(bad)
                with self.assertRaises(steamos_profile.ProfileError, msg=bad): steamos_profile.SteamosProfile(g)

    def test_header(self):
        self.assertIn('#define DM_STEAMOS_STATUS "untested"', steamos_profile.header(steamos_profile.load(NEO)))


class GameFolder(unittest.TestCase):
    def test_build(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for f, b in (('mods/main.patched.bin', b'\0' * 64), ('RomFS/data/a.bin', b'romfs'), ('aoc/1/x', b'dlc'), ('host', b'\x7fELF'), ('shaders.dmsv', b'DMSV'),
                         ('art/portrait.png', b'p'), ('libSDL3.so.0', b'so')):
                (d / f).parent.mkdir(parents=True, exist_ok=True); (d / f).write_bytes(b)
            p = steamos_profile.load(NEO)
            man = gamedir.build(d / 'out' / 'Neo', profile=p, version='v196608', display='1.03', name='NEO: The World Ends with You', host=d / 'host', patched=d / 'mods',
                                romfs=d / 'RomFS', shaders=d / 'shaders.dmsv', aoc=d / 'aoc', language='ja', env=['DM_FPS_TARGET=60'], art={'portrait': d / 'art/portrait.png'},
                                libs=[d / 'libSDL3.so.0'])
            g = d / 'out/Neo'
            self.assertTrue(os.access(g / gamedir.HOST, os.X_OK) and os.access(g / gamedir.LAUNCHER, os.X_OK))
            env = (g / 'env.txt').read_text().splitlines()
            self.assertIn('DM_LANG=ja', env); self.assertIn('DM_AOC_DIR=${DM_GAME_DIR}/aoc', env); self.assertEqual(env[-1], 'DM_FPS_TARGET=60')
            self.assertEqual((g / 'settings.txt').read_text(), 'docked=0\nrumble=1\n')
            self.assertEqual(((g / 'lib/libSDL3.so.0').read_bytes(), (g / 'art/portrait.png').read_bytes(), (g / 'RomFS/data/a.bin').read_bytes()), (b'so', b'p', b'romfs'))
            self.assertEqual(gamedir.read_manifest(g), man)
            self.assertEqual((man['dlc'], man['requires'], man['status'], man['libs'], man['files']), ([1], ['bcn'], 'untested', ['libSDL3.so.0'], 6))
            self.assertFalse((d / 'out/Neo.partial').exists())
            launcher = (g / gamedir.LAUNCHER).read_text()
            self.assertTrue(launcher.startswith('#!/bin/sh\n')); self.assertIn('exec "$here/bin/nx2s-host" "$here/mods"', launcher)

    def test_stem(self):
        g = game.load(NEO)
        self.assertEqual(pipeline.stem(g, 'NEO: The World Ends with You'), 'NeoTheWorldEndsWithYou')


class Device(unittest.TestCase):
    MAN = {'name': 'Game', 'requires': ['bcn']}

    def test_refuses_what_cannot_run(self):
        with self.assertRaises(BuildError): install.check_device({'arch': 'x86_64', 'glibc': '2.39'}, self.MAN)   # a Steam Deck
        with self.assertRaises(BuildError): install.check_device({'arch': 'aarch64', 'glibc': '2.36'}, self.MAN)
        install.check_device({'arch': 'aarch64', 'glibc': '2.39', 'gpu': 'qcom,adreno-43050c01', 'lib.libvulkan.so.1': '/usr/lib/libvulkan.so.1', 'lib.libavcodec.so': 'x'}, self.MAN)

    def test_local_push_keeps_the_players_files(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); (d / 'src/sub').mkdir(parents=True); (d / 'src/sub/a').write_text('new'); (d / 'src/settings.txt').write_text('defaults')
            (d / 'dst').mkdir(); (d / 'dst/settings.txt').write_text('mine'); (d / 'dst/stale').write_text('old')
            remote.Local().push_tree(d / 'src', d / 'dst', keep=('settings.txt',))
            self.assertEqual(((d / 'dst/sub/a').read_text(), (d / 'dst/settings.txt').read_text(), (d / 'dst/stale').exists()), ('new', 'mine', False))

    def test_os_release(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'etc').mkdir(); (Path(d) / 'etc/os-release').write_text('NAME="SteamOS"\nID=steamos\nID_LIKE=arch\nVARIANT_ID=steamdeck\n')
            self.assertEqual(toolchain.os_release(d)['ID'], 'steamos')


class Artwork(unittest.TestCase):
    def test_sizes(self):
        try: from PIL import Image
        except ImportError: self.skipTest('no Pillow')
        from porter import artwork
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 'icon.jpg'; Image.new('RGB', (256, 256), (200, 40, 40)).save(src)
            out = artwork.make(src, Path(d) / 'art')
            sizes = {}
            for k, p in out.items():
                with Image.open(p) as im: sizes[k] = im.size
            self.assertEqual(sizes, {'portrait': (600, 900), 'wide': (920, 430), 'hero': (1920, 620), 'icon': (256, 256)})


class Misc(unittest.TestCase):
    def test_config_roundtrip(self):
        config.set_value('device', 'deck@steamos.local'); self.assertEqual(config.load()['device'], 'deck@steamos.local')
        with self.assertRaises(KeyError): config.set_value('nonsense', 1)
        config.set_value('engine', 'recomp'); self.assertEqual(config.load()['engine'], 'recomp'); config.set_value('engine', '')
        with self.assertRaises(KeyError): config.set_value('engine', 'deck')


class FpsLocker(unittest.TestCase):   # porter/fpslocker.py, from nx2android: FPSLocker-Warehouse configs → the host's rows (hle_fpslock.c DM_FPS_LOCKS) and the rewriter's cuts
    BID = 'd49165c05ead8ae140f189aa97934f39'
    UNITY = ('ALL_FPS:\n  # Vsync\n  -\n    type: evaluate_write\n    address: [MAIN, 0x7A642D8]\n    value_type: int32\n    value: VSYNC_TARGET\n'
             '  -\n    type: evaluate_write\n    address: [MAIN, 0x7A642E4]\n    value_type: float\n    value: "1 / FPS_LOCK_TARGET"\n')

    def rows(self, text, image=b''):
        from porter import fpslocker
        tsv, cuts = fpslocker.convert(text, image, self.BID, 'test')
        return [line.split('\t') for line in tsv.splitlines() if not line.startswith('#')], cuts

    def test_unity_vsync_and_frame_time(self):
        try: import yaml   # noqa: F401
        except ImportError: self.skipTest('no PyYAML')
        rows, cuts = self.rows(self.UNITY)
        self.assertEqual(rows[0], ['build', self.BID]); self.assertEqual(cuts, [])
        self.assertEqual(rows[1][1:], ['int32', '0x7a642d8', '2', '1', '-'])
        self.assertEqual(rows[2][1:3], ['float', '0x7a642e4']); self.assertAlmostEqual(float(rows[2][3]), 1 / 30); self.assertAlmostEqual(float(rows[2][4]), 1 / 62)

    def test_expressions(self):
        from porter.fpslocker import evaluate
        self.assertEqual(evaluate('TruncDec(FRAMETIME_TARGET, 2)', 30), 33.33); self.assertEqual(evaluate('2^3', 60), 8)
        with self.assertRaises(Exception): evaluate('__import__("os")', 60)

    def test_url(self):
        from porter import fpslocker
        self.assertTrue(fpslocker.url('0100fd501f95a000', self.BID).endswith('/patches/0100FD501F95A000/D49165C05EAD8AE1.yaml'))


if __name__ == '__main__':
    unittest.main()
