# python -m unittest discover tests: the rules of docs/ARCHITECTURE.md that a test can check (nx2android's tests/arch_rules.py is the model).
import ast, os, re, sys, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STEAMOS = REPO / 'nxnative/dm/steamos'
SKIP = {'.git', '.venv', 'dist', 'out', '__pycache__', 'third_party', 'gen'}


LOCAL = re.compile(r'^(extracted|generated)(-.*)?$|^build$')   # recomp/'s local folders for a game's files and its generated code (ignored by recomp/.gitignore)


def files(root, *suffixes):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP and not LOCAL.match(d)]
        for f in fns:
            if f.endswith(suffixes): yield Path(dp) / f


def code_only(text):   # C without its comments and string literals' contents
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
    return re.sub(r'//[^\n]*', ' ', text)


def imports(path):
    tree = ast.parse(path.read_text())
    for n in ast.walk(tree):
        if isinstance(n, ast.Import): yield from (a.name.split('.')[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level: yield n.module.split('.')[0]


class Rules(unittest.TestCase):
    def test_runtime_names_no_game(self):   # a game that needs something different gets a profile key (dm/steamos/tools/steamos_profile.py), not a branch in code
        for p in files(STEAMOS, '.c', '.h'):
            self.assertIsNone(re.search(r'\b0100[0-9A-Fa-f]{12}\b', code_only(p.read_text())), f'{p}: a title ID in code')

    def test_steam_module_is_standard_library_only(self):   # install.py runs it on the device with the system's python3, nothing installed
        allowed = set(sys.stdlib_module_names) | {'__future__'}
        self.assertEqual({m for m in imports(REPO / 'porter/steam.py')} - allowed, set())

    def test_porter_has_no_ui_and_no_devtools(self):
        for p in files(REPO / 'porter', '.py'):
            bad = {m for m in imports(p)} & {'gui', 'devtools', 'PySide6', 'tkinter'}
            self.assertEqual(bad, set(), f'{p} imports {bad}')

    def test_no_keys_or_game_files(self):
        for p in files(REPO, '.keys', '.nsp', '.nsz', '.xci', '.xcz', '.nca', '.nso', '.nro', '.dmsv'):
            self.fail(f'{p}: keys and game files never go in the repository (they stay in the game\'s folder)')

    def test_recomp_keeps_game_code_out(self):   # the Steam Deck engine's generated C is the game's code translated: it stays on the player's machine (docs/RECOMP.md)
        ignored = (REPO / 'recomp/.gitignore').read_text().split()
        for pattern in ('extracted/', 'extracted-*/', 'generated/', 'generated-*/', 'build/', '*.keys', '*.nsp', '*.xci', '*.nca'):
            self.assertIn(pattern, ignored, f'recomp/.gitignore must ignore {pattern}')
        self.assertRegex((REPO / 'docs/RECOMP.md').read_text(), r'\| `[0-9a-f]{7}` \(', 'docs/RECOMP.md names the vendored commit')

    def test_runtime_targets_linux_glibc(self):   # the rewriter's target and the loader's agree (nxnative/dm/tools/tests/test_target_slots.py checks the encodings)
        self.assertIn("DM_TARGET='linux'", (REPO / 'porter/pipeline.py').read_text())
        self.assertIn("'--cc', 'zig'", (REPO / 'porter/pipeline.py').read_text())

    def test_doctor_names_every_tool(self):
        sys.path.insert(0, str(REPO))
        from porter import toolchain
        names = {r.name for r in toolchain.checks()}
        for n in ('Python', 'Zig', '.NET SDK', 'SDL3 for the device', 'ssh', 'prod.keys'): self.assertIn(n, names)

    def test_shader_headers_current(self):   # the overlay's SPIR-V headers match their sources (dm/steamos/tools/gen_shaders.py)
        sys.path.insert(0, str(STEAMOS / 'tools'))
        import gen_shaders
        self.assertEqual(gen_shaders.stale(), [])


if __name__ == '__main__':
    unittest.main()
