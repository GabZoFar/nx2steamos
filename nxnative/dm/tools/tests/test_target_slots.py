"""The rewriter's guest thread slot access for each host (patch_nso.py emit_tp, DM_TARGET), on real AArch64 encodings, without game files.
apple reads Darwin's TSD through tpidrro_el0; android loads bionic's TLS_SLOT_APP, tpidr_el0[2]; linux (glibc: SteamOS) loads tpidr_el0[1], glibc's tcbhead_t.private,
because tpidr_el0 + 16 is where glibc's static TLS starts. loader.c reads the same word (TLS_SLOT_APP)."""
import ast
from pathlib import Path
import re
import struct
import sys
import unittest

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import nso_asm  # noqa: E402

SRC = (TOOLS / 'patch_nso.py').read_text()


def emit_tp_words(target, reg=9):
    """The words emit_tp writes for target: patch_nso.py's own emit_tp and TP_SLOT, compiled without its driver"""
    tree = ast.parse(SRC)
    keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == 'emit_tp') or
            (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'TP_SLOT' for t in n.targets))]
    words = []
    scope = {**{k: getattr(nso_asm, k) for k in dir(nso_asm) if k.isupper()}, 'TARGET': target, 'emit': lambda w, kind=None: words.append(w & 0xFFFFFFFF), 're': re}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(TOOLS / 'patch_nso.py'), 'exec'), scope)
    scope['emit_tp'](reg)
    return words


def disasm(words):
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    return [f'{i.mnemonic} {i.op_str}' for i in md.disasm(struct.pack('<' + 'I' * len(words), *words), 0)]


class TargetSlots(unittest.TestCase):
    def test_targets_known(self):
        m = re.search(r"assert TARGET in \(([^)]*)\)", SRC)
        self.assertEqual(set(ast.literal_eval('(' + m.group(1) + ')')), {'apple', 'android', 'linux'})

    def test_apple_reads_tpidrro(self):
        self.assertEqual(disasm(emit_tp_words('apple')), ['mrs x9, tpidrro_el0'])

    def test_android_reads_bionic_tls_slot_app(self):
        self.assertEqual(disasm(emit_tp_words('android')), ['mrs x9, tpidr_el0', 'ldr x9, [x9, #0x10]'])

    def test_linux_reads_glibc_tcb_private(self):
        self.assertEqual(disasm(emit_tp_words('linux')), ['mrs x9, tpidr_el0', 'ldr x9, [x9, #8]'])

    def test_loader_reads_the_same_words(self):
        loader = (TOOLS.parent / 'host/loader.c').read_text()
        android = re.search(r'#if defined\(__ANDROID__\)\s*#define TLS_SLOT_APP (\d+)', loader)
        linux = re.search(r'#else\s*#define TLS_SLOT_APP (\d+)', loader)
        self.assertEqual((int(android.group(1)), int(linux.group(1))), (2, 1))
        self.assertIn('static const char *const want = "linux";', loader)


if __name__ == '__main__':
    unittest.main()
