#!/usr/bin/env python3
# The Vulkan backend's own small shaders (dm/android/shaders: the overlay's quad) as SPIR-V headers for the SteamOS build, in dm/steamos/gen/<name>_<stage>.spv.h.
# The Android build compiles them with the NDK's glslc at every build; the SteamOS build has no shader compiler (Zig is a C toolchain), so they are compiled here,
# once, and committed. Each header names its source's SHA-256: build.py refuses a header older than its source. Run after changing a shader (glslangValidator or glslc):
#   python3 dm/steamos/tools/gen_shaders.py [--check]
import hashlib, shutil, struct, subprocess, sys, tempfile
from pathlib import Path

DM = Path(__file__).resolve().parents[2]
SRC, GEN = DM / 'android/shaders', DM / 'steamos/gen'


def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def header_digest(h):
    try: line = h.read_text().splitlines()[0]
    except (OSError, IndexError): return None
    return line.rsplit(' ', 1)[-1] if 'sha256' in line else None


def stale():   # [(source, header)] whose header is missing or older than the source
    return [(s, GEN / f'{s.stem}_{s.suffix[1:]}.spv.h') for s in sorted(SRC.glob('*.*')) if header_digest(GEN / f'{s.stem}_{s.suffix[1:]}.spv.h') != digest(s)]


def compile_one(src, out):
    with tempfile.TemporaryDirectory() as t:
        spv = Path(t) / 'o.spv'
        if shutil.which('glslangValidator'): cmd = ['glslangValidator', '-V', '--target-env', 'vulkan1.1', str(src), '-o', str(spv)]
        elif shutil.which('glslc'): cmd = ['glslc', '--target-env=vulkan1.1', '-O', str(src), '-o', str(spv)]
        else: sys.exit('gen_shaders.py: no glslangValidator or glslc (the arm64 dev container has glslang: devtools/container)')
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        words = spv.read_bytes(); n = len(words) // 4
    body = ', '.join(f'{w:#010x}' for w in struct.unpack(f'<{n}I', words))
    out.write_text(f'// generated from dm/android/shaders/{src.name} by dm/steamos/tools/gen_shaders.py; source sha256 {digest(src)}\n'
                   f'static const uint32_t {src.stem}_{src.suffix[1:]}_spv[] = {{{body}}};\n')


if __name__ == '__main__':
    bad = stale()
    if '--check' in sys.argv: sys.exit(f'stale: {", ".join(s.name for s, _ in bad)}' if bad else 0)
    GEN.mkdir(parents=True, exist_ok=True)
    for s, h in bad: compile_one(s, h); print(f'{h.relative_to(DM)}')
    if not bad: print('up to date')
