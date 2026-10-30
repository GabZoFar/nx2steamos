# The port: a game folder (NSP / XCI) + prod.keys + the game's profile → a SteamOS game folder (docs/ARCHITECTURE.md "The port"), adapted from nx2android's porter/pipeline.py.
# The steps every platform shares are upstream's (porter.upstream.nsp: scan, extraction, the shader split, stage reuse, progress) and nx2android's (the Vulkan shader
# bundle, the 60 fps configs); the SteamOS ones are here: the rewrite for DM_TARGET=linux, the host built with Zig, the Steam artwork, the game folder and the install.
# Work files go where upstream's NSP script puts them, <game folder>/<title ID>_build/ (extract/, aoc/, shader/, logs/ are shared with an iOS or Android build of the
# same folder), with the SteamOS ones in its steamos/ folder.
import os, re, shutil, sys
from dataclasses import dataclass, field
from pathlib import Path

from porter import artwork, config, fpslocker, gamedir, install, paths, profiles, toolchain
from porter.paths import DM, NXNATIVE
from porter.upstream import BuildError, PROG, game, nacp, nsp, profile_guess, say, steamos_profile

PY = sys.executable


@dataclass
class Options:
    folder: Path                       # the game's folder: the base game, its updates and DLC
    keys: Path = None                  # prod.keys
    out: Path = None                   # where the game folder goes (default: the game files' folder)
    version: str = ''                  # v<N> to pack (default: the newest in the folder)
    language: str = ''                 # a code or a name (default: English)
    app_name: str = ''                 # the name in Steam's library (default: the game's name in its control data)
    install: bool = False              # install on the device after packing
    device: str = ''                   # user@host over SSH; empty = this machine (a SteamOS device)
    device_dir: str = ''               # where games go on the device (default ~/Games/nx2steamos)
    steam: bool = True                 # put the game in Steam's library when installing
    restart_steam: bool = False        # stop and start Steam to write its shortcuts directly
    debug: bool = False                # a development build: debug information, DM_CTL
    experimental: bool = False         # a game not ported to SteamOS or Android yet: a provisional profile from upstream's survey (or a copy of its iOS profile)
    fps60: bool = False                # 60 fps: the profile's own frame-rate cap, else the build's FPSLocker-Warehouse config (porter/fpslocker.py)
    jobs: int = field(default_factory=lambda: os.cpu_count() or 8)


def stem(g, name):   # the game folder's name: the profile's iOS IPA name without .ipa (NeoTheWorldEndsWithYou)
    ipa = g.get('ipa') or nsp.ipa_name(name, g.title_id)
    return ipa[:-4] if ipa.endswith('.ipa') else ipa


def inspect(folder):
    """What a game folder holds, without extracting anything (`porter inspect`)"""
    try: app, fam = nsp.scan_nsps(Path(folder))
    except BuildError:
        if any(Path(folder).glob('*.ns[zZ]')) or any(Path(folder).glob('*.xc[zZ]')):   # how dumps are often shared: zstd-compressed NCAs the extractor does not read
            raise BuildError('this folder has only NSZ / XCZ files: decompress them to NSP / XCI first (nsz -D with your prod.keys), then choose the folder with the NSP files') from None
        raise
    g, sp = profiles.by_title(app)
    info = dict(title_id=f'{app:016X}', base=fam['base'].name, updates=[{'version': f'v{v}' if v is not None else None, 'file': p.name} for v, p in fam['updates']],
                dlc=[{'title_id': f'{t:016X}', 'file': p.name} for t, _, p in fam['dlc']], profile=None, steamos=None, versions=[], pack=None, supported=False, reason='')
    from porter import deck
    dp = deck.profile(g)
    info['deck'] = dp.as_dict() if dp else None   # the Steam Deck engine (porter/deck.py)
    info['experimental'] = not (g and sp)
    if not g:
        info['reason'] = f'no game profile for {app:016X}: this game is not supported yet (an experimental port surveys it and guesses a profile; it may not start)'
        return info
    builds = g.builds()
    have = nsp.nsp_versions(fam, builds)
    pack = max(have, key=nsp.vnum)
    info.update(profile=g.id, name=g.get('name', g.id), versions=sorted(have, key=nsp.vnum), pack=pack,
                registered={v: b.get('display', '') for v, b in builds.items()}, derived=pack not in builds)
    if not sp:
        info['reason'] = f'{g.get("name", g.id)} has a game profile (iOS) but is not ported to Android or SteamOS yet (an experimental port tries it with that profile)'
        return info
    info.update(steamos=profiles.entry(g, sp).as_dict(), supported=True)
    if sp.status == 'untested': info['reason'] = f'{g.get("name", g.id)} plays on Android with the same Vulkan backend; nobody has checked it on SteamOS yet'
    elif sp.status == 'wip': info['reason'] = f'{g.get("name", g.id)} is a work in progress: it plays, but parts of it are missing (docs/PORT_STATUS.md says which)'
    if pack not in builds:
        info['reason'] = (info['reason'] + '; ' if info['reason'] else '') + f'{pack} is not a version the profile was brought up on ({", ".join(builds)}): it is packed from a derived profile, without that version\'s fixes'
    return info


class Port:
    def __init__(self, opts):
        self.o = opts
        self.cfg = config.load()
        self.result = {}

    # ---- the whole port ----
    def run(self):
        o = self.o
        o.folder = Path(o.folder).expanduser().resolve()
        if not o.folder.is_dir(): raise BuildError(f'not a folder: {o.folder}')
        keys = o.keys or self.cfg.get('keys')
        if not keys: raise BuildError('no prod.keys: give -k / --keys, or set it once with `porter config keys <path>`')
        o.keys = Path(keys).expanduser().resolve()
        o.device = o.device or self.cfg.get('device') or ''
        o.device_dir = o.device_dir or self.cfg.get('device_dir') or ''
        if o.install and not o.device and not toolchain.local_steamos():
            raise BuildError('--install needs the SteamOS device: run the port on it, or give --device user@host (porter config device user@host)')
        try: self.tools = toolchain.require(self.cfg, str(o.keys), remote=o.install and bool(o.device))
        except toolchain.ToolError as e: raise BuildError(str(e)) from e
        o.out = Path(o.out or self.cfg.get('out') or o.folder).expanduser().resolve()
        # the chosen dotnet first on PATH, in this process too: Windows finds a program by the parent's PATH, not the one passed to the child (upstream runs `dotnet` by name)
        os.environ['PATH'] = os.pathsep.join([str(self.tools['dotnet'].parent), os.environ.get('PATH', '')])
        os.environ['DOTNET_ROOT'] = str(self.tools['dotnet'].parent)   # where nx_extract's apphost looks for the runtime
        # PYTHONUTF8: the engine's tools write their tables with open() and no encoding, which is the ANSI code page on Windows
        self.env = dict(os.environ, J=str(o.jobs), PYTHONIOENCODING='utf-8', PYTHONUTF8='1', DOTNET_CLI_TELEMETRY_OPTOUT='1')

        self.inputs()
        PROG.plan(1 + len(self.versions) + bool(self.fam['dlc']) + bool(o.fps60) + 6 + bool(o.install))   # nx_extract, the versions, the DLC, 60 fps, rewrite / split / Vulkan shaders / host / art / folder, install
        self.extract()
        self.check_graphics()
        self.fps_unlock()
        self.rewrite()
        if o.install: self.pull_captures()
        self.shaders()
        self.host()
        self.art()
        self.folder()
        if o.install: self.result['install'] = install.install(self.dst, device=o.device, device_dir=o.device_dir, steam=o.steam, restart_steam=o.restart_steam,
                                                               account=self.cfg.get('steam_user') or '', ssh=self.tools['ssh'], rsync=self.tools['rsync'])
        nsp.rmtree(self.work / 'tmp')
        self.result.update(title_id=self.g.id, name=self.app_name, version=self.pkg_ver, display=self.display, language=self.lang,
                           status=self.sp.status, derived=bool(self.derived), installed=bool(o.install))
        say(f'done: {self.result["folder"]}')
        if self.derived: say(f'[RED] {self.derived}: the port is built from a derived profile and may not run as the profile\'s tested version does')
        if self.sp.status in ('untested', 'experimental'): say(f'[RED] {self.app_name} is {self.sp.status} on SteamOS: please report how it runs (docs/PORT_STATUS.md)')
        return self.result

    # ---- inputs: the folder's games and versions, the profile, the version to pack ----
    def inputs(self):
        o = self.o
        say(f'game folder: {o.folder}')
        self.app, self.fam = nsp.scan_nsps(o.folder)
        tid = f'{self.app:016X}'
        self.work = o.folder / f'{tid}_build'
        g, sp = profiles.by_title(self.app)
        self.provisional = False
        if not (g and sp):
            what = f'no game profile for {tid}' if not g else f'{g.get("name", g.id)} ({g.id}) has a game profile but is not ported to SteamOS or Android yet (no steamos.tsv or android.tsv in {g.dir})'
            if not o.experimental: raise BuildError(f'{what}: --experimental tries it anyway (profiles: `porter profiles`)')
            g, sp = self.experimental_profile(g, tid)
        self.g, self.sp = g, sp
        say(f'game profile: {g.id} {g.get("name", "")} (SteamOS: {sp.status}' + (f', tested on {sp.tested}' if sp.tested else '') + f', from {sp.source})')
        builds, need = g.builds(), g.words('shader_versions')
        have = nsp.nsp_versions(self.fam, builds)
        self.derived = ''
        if self.provisional:   # the version the survey saw
            self.pkg_ver = o.version or g.get('package')
            if self.pkg_ver not in builds: raise BuildError(f'the version to pack must be one the provisional profile registers: {", ".join(builds)}')
        else:
            self.pkg_ver = o.version or max(have, key=nsp.vnum)
            if self.pkg_ver not in have: raise BuildError(f'the version to pack ({self.pkg_ver}) is not in the folder, which holds {", ".join(sorted(have, key=nsp.vnum))}')
            if self.pkg_ver not in builds: self.derived = f'{self.pkg_ver} is not a version game profile {g.id} registers ({", ".join(f"{v} {b["display"]}" for v, b in builds.items())})'
            elif not all(v in have for v in need): self.derived = f'the folder has no {", ".join(v for v in need if v not in have)}, whose files the profile\'s shader tables were made from'
            if self.derived: say(f'{self.derived}: packing {self.pkg_ver} from a profile derived from it')
        self.versions = [self.pkg_ver] if self.derived else need + ([self.pkg_ver] if self.pkg_ver not in need else [])
        self.have = have
        self.check_space()
        self.sw = self.work / 'steamos'; self.logs = self.work / 'logs'
        for d in (self.sw, self.logs, self.work / 'tmp'): d.mkdir(parents=True, exist_ok=True)
        # DM_GAME picks the profile for every tool; WORK keeps their temporary folders inside the build folder (the shader translator writes thousands of files)
        self.env.update(DM_GAME=g.id, DM_ROOT=str(self.work), WORK=str(self.work / 'tmp'))

    def check_space(self):
        """Stop before extracting when the game folder's drive cannot hold the port: a build folder takes about 1 to 2 times the game files, less what an earlier
        port left there, plus a margin"""
        game_b = sum(f.stat().st_size for f in self.o.folder.iterdir() if f.is_file() and f.suffix.lower() in ('.nsp', '.xci'))
        built = sum(f.stat().st_size for f in self.work.rglob('*') if f.is_file()) if self.work.is_dir() else 0
        need, free = max(0, 2 * game_b - built) + 2**30, shutil.disk_usage(self.o.folder).free
        if free < need: raise BuildError(f'not enough disk space on {self.o.folder.anchor}: the port needs about {need / 2**30:.1f} GB more, {free / 2**30:.1f} GB is free')

    def use_profile_dir(self, pdir, first):   # every tool finds the game's profile in pdir (game.py DM_GAMES_DIR; first: before dm/games)
        dirs = [str(pdir)] + [d for d in os.environ.get('DM_GAMES_DIR', '').split(os.pathsep) if d and d != str(pdir)]
        self.env['DM_GAMES_DIR'] = os.environ['DM_GAMES_DIR'] = os.pathsep.join(dirs)
        if first: self.env['DM_GAMES_FIRST'] = os.environ['DM_GAMES_FIRST'] = '1'

    def experimental_profile(self, g, tid):
        """A profile for a game not ported to SteamOS, in <build folder>/profile/<id>/ (outside the repository): upstream's survey and provisional profile when the game
        has none (scripts/build_ipa_from_nsp.py survey / write_profile, dm/games/profile_guess.py), or a copy of its iOS profile; plus a steamos.tsv with status
        experimental. A game brought up this way gets its profile into dm/games by hand (docs/ARCHITECTURE.md "Adding a game")"""
        pdir = self.work / 'profile'; d = pdir / tid
        if g is None:
            if (d / 'game.tsv').is_file(): say(f'no game profile for {tid}: reusing the provisional profile from the last run ({d}; delete it to survey again)')
            else:
                facts = nsp.survey(self.o.folder, self.app, self.fam, self.o.keys, NXNATIVE, PY, self.env)
                facts['survey'] = f'<NSP folder>/{tid}_survey/survey.md'
                nsp.write_profile(d, self.app, facts)
            self.provisional = True
            say('[RED] provisional game profile: values guessed from the survey, the game may not start')
        elif not (d / 'game.tsv').is_file() or (g.dir / 'game.tsv').stat().st_mtime > (d / 'game.tsv').stat().st_mtime:   # the iOS profile as it is now
            nsp.rmtree(d); shutil.copytree(g.dir, d, ignore=shutil.ignore_patterns('patches.py', '__pycache__'))   # patches.py stays in dm/games, where patch_nso.py finds it
        if not (d / 'steamos.tsv').is_file():
            (d / 'steamos.tsv').write_text('# Written by porter for an experimental port: the game has not been brought up on SteamOS\nstatus\texperimental\n', encoding='utf-8')
        self.use_profile_dir(pdir, first=True)
        g = game.load(tid)
        return g, steamos_profile.SteamosProfile(g)

    # ---- extract: nx_extract, each version the shaders come from, the DLC ----
    def extract(self):
        o, g = self.o, self.g
        picks = nsp.pick_nsps(self.fam, self.have, self.versions)
        ex = self.work / 'extract'; ex.mkdir(parents=True, exist_ok=True)
        need = sum({p.stat().st_size for b, u in picks.values() for p in (b, u) if p}) / 2**30 + 6
        if nsp.free_gb(self.work) < need and not all((ex / v).exists() for v in self.versions): raise BuildError(f'less than {need:.0f} GB free on the volume of {self.work}')
        PROG.stage('build nx_extract')
        nx = nsp.build_nx_extract(NXNATIVE, self.work, self.logs, self.env)
        PROG.end()
        self.dirs = {ver: nsp.extract(nx, o.keys, ex, ver, *picks[ver], None, self.logs, g) for ver in self.versions}
        bad = [] if self.derived else [f'{v}: {x}' for v in self.versions for x in nsp.build_mismatch(self.dirs[v], v, g)]
        if bad and self.provisional: raise BuildError(f'not the builds the provisional profile registers: {"; ".join(bad)}')
        if bad:
            self.derived = f'not the builds game profile {g.id} registers: {"; ".join(bad)}'
            say(f'{self.derived}: packing {self.pkg_ver} from a profile derived from it')
            self.versions, self.dirs = [self.pkg_ver], {self.pkg_ver: self.dirs[self.pkg_ver]}
        self.game = self.dirs[self.pkg_ver]
        if self.derived:   # the derived profile (its steamos.tsv / android.tsv included) goes into the build folder, and every tool takes it first
            pdir = self.work / 'profile-derived'
            disp = nsp.derive_profile(g, pdir / g.id, self.pkg_ver, self.game, g.words('modules'))
            self.use_profile_dir(pdir, first=True)
            self.g = game.load(g.id); self.sp = steamos_profile.SteamosProfile(self.g)
            say(f'derived game profile for {self.pkg_ver} ({disp}): {self.g.dir}')
        self.aoc = nsp.extract_aoc(nx, o.keys, NXNATIVE, self.work, self.app, self.fam, self.logs)
        self.base = next((self.dirs[v] for v in self.versions if picks[v][1] is None), self.dirs[self.versions[0]])
        self.app_name = o.app_name or nacp.name(self.base / 'control.nacp') or self.g.get('name', self.g.id)
        self.display = nacp.version(self.game / 'control.nacp') or nacp.version(self.base / 'control.nacp') or '1.0'
        lang_in = o.language or self.cfg.get('language')
        want = nsp.language_code(lang_in) if lang_in else 'en-US'
        if not want: raise BuildError(f'unknown game language {lang_in!r}')
        have = nacp.languages(self.game / 'control.nacp') or nacp.languages(self.base / 'control.nacp')
        self.lang = nsp.game_language(want, bool(lang_in), have)
        say(f'game: {self.app_name} {self.display}, game language {self.lang}')

    def check_graphics(self):
        """What the SteamOS host can run, as the Android host (its Vulkan backend): NVN games whose shaders are all in their files. The other kinds need a step upstream
        runs on a Mac (booting the game in the Mac host) or a graphics layer the host does not have yet"""
        src = profile_guess.build_sources(self.game / 'ExeFS', self.game / 'RomFS', self.g.words('modules'))
        gfx = src['graphics'][0]
        if gfx != 'nvn': raise BuildError(f'{self.app_name} renders with {gfx} ({src["graphics"][1]}): the SteamOS host replays NVN only so far')
        for k in ('glslc_capture', 'ue_decode', 'oodle_packs', 'ue_legacy'):
            if src[k][0]: raise BuildError(f'{self.app_name} needs a step that boots the game in the Mac host ({src[k][1]}), which this port cannot run yet')

    # ---- 60 fps (--fps60): the profile's own cap (fps_target), else the community's FPSLocker config of the build ----
    def fps_unlock(self):
        self.fps_env, self.fps_files = [], {}
        self.env.pop('DM_PATCHES_EXTRA', None)
        if not self.o.fps60: return
        PROG.stage('60 fps', step='the game profile')
        if self.g.get('fps_target', '0') != '0':
            self.fps_env = ['DM_FPS_TARGET=60']; PROG.end('the game profile\'s own frame-rate cap (fps_target), at 60'); return
        img, bid = fpslocker.nso_image(self.game / 'ExeFS/main')
        src = fpslocker.url(self.g.id, bid); d = self.sw / 'fpslock'; d.mkdir(exist_ok=True); cache = d / f'{bid[:16].upper()}.yaml'
        PROG.set(step='fetching the FPSLocker-Warehouse config')
        try: text = fpslocker.fetch(self.g.id, bid)
        except OSError as e:
            if not cache.is_file(): say(f'[RED] 60 fps: {src} could not be fetched ({e}): the game keeps its own frame rate'); PROG.end('not fetched: the game keeps its own frame rate'); return
            text = cache.read_text(encoding='utf-8'); say(f'[RED] 60 fps: {src} could not be fetched ({e}): using the copy fetched before')
        if text is None:
            nsp.rmtree(cache); say(f'60 fps: FPSLocker-Warehouse has no config for main build {bid[:16].upper()}: the game keeps its own frame rate'); PROG.end('no config for this build'); return
        cache.write_text(text, encoding='utf-8')
        try: tsv, cuts = fpslocker.convert(text, img, bid, src)
        except fpslocker.Refused as e: say(f'[RED] 60 fps: {src} cannot be applied ({e}): the game keeps its own frame rate'); PROG.end('config not applicable: the game keeps its own frame rate'); return
        (d / 'fpslock.tsv').write_text(tsv, encoding='utf-8', newline='\n')
        if cuts:   # code bytes: the rewriter's cuts, applied before it rewrites main (patch_nso.py DM_PATCHES_EXTRA; part of the rewrite's reuse key)
            (d / 'patches.py').write_text(fpslocker.patches_py(bid, cuts, src), encoding='utf-8', newline='\n'); self.env['DM_PATCHES_EXTRA'] = str(d / 'patches.py')
        # FPSLocker paces the presents for its target itself: presents follow the display (present_interval display); the path is the game folder's on the device
        self.fps_env = ['DM_FPS_TARGET=60', 'DM_FPS_LOCKS=${DM_GAME_DIR}/fpslock.tsv', 'DM_PRESENT_INTERVAL=display']
        self.fps_files = {'fpslock.tsv': d / 'fpslock.tsv'}
        n = tsv.count('\n') - 3
        say(f'60 fps: FPSLocker-Warehouse config {src}, by masagrator and contributors: {n} memory write(s)' + (f', {len(cuts)} code cut(s)' if cuts else ''))
        PROG.end(f'{n} memory write(s)' + (f', {len(cuts)} code cut(s)' if cuts else ''))

    # ---- rewrite: patch_nso.py for glibc's thread slots (DM_TARGET=linux) ----
    def rewrite(self):
        self.patched = self.sw / f'patched_{self.pkg_ver}'   # beside, not over, an iOS or Android build's: the rewrite's reuse key does not include the target
        nsp.rewrite(PY, NXNATIVE, self.game / 'ExeFS', self.patched, dict(self.env, DM_TARGET='linux'), self.logs, self.g)
        if self.derived or self.provisional:   # the keys that need the rewritten modules (upstream's provisional steps)
            nsp.provisional_prep(PY, NXNATIVE, self.env, self.logs, self.g)
            self.g = nsp.refine_after_rewrite(self.g, self.game, self.patched)
            if self.provisional and profile_guess.heap_check_needed(self.game / 'ExeFS') and not nsp.heap_checked(self.g):
                say('[RED] no heap check: it boots the game in the Mac host; the provisional profile keeps its guessed heap keys')

    # ---- shaders: upstream's split, then SPIR-V for the Vulkan backend (nx2android's shader_vk.py) ----
    def shaders(self):
        sv = self.versions
        pkg = self.work / 'shader' / 'pkg'
        nsp.split_shaders(PY, NXNATIVE, [self.dirs[v] / 'RomFS' for v in sv], [self.dirs[v] / 'ExeFS/main' for v in sv], pkg, self.env, self.logs, self.g, bool(self.derived or self.provisional),
                         aoc=self.aoc)
        self.dmsv = self.sw / 'shaders.dmsv'; kf = self.sw / 'shaders.dmsv.key'; cap = self.sw / 'captured_shaders'
        PROG.stage('Vulkan shaders', step='building the translator')
        xlate, xkey = self.translator()
        PROG.set(step='checking the inputs')
        key = nsp.fingerprint(nsp.package_key(pkg), ('files', [DM / 'android/tools/shader_vk.py'] + nsp.tool_files(NXNATIVE)), ('files', nsp.profile_files(self.g)), xkey,
                              ('files', sorted(cap.glob('*.bin'))))
        if self.dmsv.is_file() and (note := nsp.reused(kf, key)) is not None:
            PROG.end(f'{note}, reused (inputs unchanged)'); return
        tmp = self.dmsv.with_name('shaders.dmsv.partial'); nsp.rmtree(tmp); nsp.rmtree(kf)
        def line(s):
            if m := re.match(r'translating (\d+) shaders', s): PROG.set(step=f'translating {int(m.group(1)):,} shaders')
        nsp.run([PY, DM / 'android/tools/shader_vk.py', pkg, tmp, '--jobs', str(self.o.jobs), '--dotnet', self.tools['dotnet'], '--xlate', xlate, '--capture', cap],
                self.logs / 'steamos_shaders.log', env=self.env, on_line=line)
        m = re.search(r'^(\d+) of (\d+) shaders', (self.logs / 'steamos_shaders.log').read_text(errors='replace'), re.M)
        if not m or m.group(1) == '0': raise BuildError(f'no shader translated (see {self.logs / "steamos_shaders.log"})')
        note = f'{int(m.group(1)):,} of {int(m.group(2)):,} shaders, {tmp.stat().st_size / 2**20:,.0f} MB'
        nsp.swap_in(tmp, self.dmsv); kf.write_text(f'{key}\n{note}\n')
        PROG.end(note)
        for d in (self.work / 'tmp').glob('shvk_*'): nsp.rmtree(d)   # the translator's work folders

    def pull_captures(self):
        """Shaders whose code no game file holds: the host saves each one the bundle lacks on the device (gfx_vk_shader.c vk_shader_missing, in the game's state folder),
        and they are pulled here into <build folder>/steamos/captured_shaders, kept from port to port, for shader_vk.py to translate into the bundle. Only for an
        install, which has the device; a device that cannot be reached leaves the captures already pulled"""
        try: got = install.pull_captures(self.o.device, self.g.id, self.sw / 'captured_shaders', ssh=self.tools['ssh'], rsync=self.tools['rsync'])
        except BuildError as e: say(f'shaders captured on the device not pulled ({e})'); return
        if got: say(f'{len(got)} shader(s) missing from the bundle, captured on the device: {" ".join(n[:16] for n in got)}')

    def translator(self):
        """The shader translator (dm/gfx/tools/dmsh_xlate over dm/third_party/ryujinx) built from the repository's source with the player's .NET SDK, once for every game,
        in <data dir>/tools/dmsh_xlate: (dmsh_xlate.dll, the source's key). As nx2android: not the iOS chain's prebuilt dmsh_xlate_bin, which only a Mac can rebuild"""
        src = DM / 'gfx/tools/dmsh_xlate'; ryu = DM / 'third_party/ryujinx'
        files = sorted([*src.glob('*.cs'), *src.glob('*.csproj'), *ryu.glob('*.props')] + [p for p in (ryu / 'src').rglob('*') if p.suffix in ('.cs', '.csproj', '.props')
                                                                                         and not {'bin', 'obj'} & set(p.relative_to(ryu).parts)])
        key = nsp.fingerprint(('files', files), ('cmd', [str(self.tools['dotnet']), '--version']), env=self.env)
        out = paths.data_dir() / 'tools/dmsh_xlate'; kf = out.with_name('dmsh_xlate.key'); dll = out / 'dmsh_xlate.dll'
        if dll.is_file() and nsp.reused(kf, key) is not None: return dll, key
        tmp = out.with_name('dmsh_xlate.partial'); nsp.rmtree(tmp); nsp.rmtree(kf)
        nsp.run([self.tools['dotnet'], 'build', src, '-c', 'Release', '-o', tmp, '-nologo', '-v', 'quiet'], self.logs / 'steamos_translator_build.log', env=self.env)
        if not (tmp / 'dmsh_xlate.dll').is_file(): raise BuildError(f'the translator built without dmsh_xlate.dll (see {self.logs / "steamos_translator_build.log"})')
        nsp.swap_in(tmp, out); kf.write_text(f'{key}\n')
        return dll, key

    # ---- host: the SteamOS host built for this profile with the player's Zig ----
    def host(self):
        PROG.stage('host', step=f'Zig {toolchain.zig_version(self.tools["zig"]) or "?"}')
        out = self.sw / ('host-debug' if self.o.debug else 'host')
        nsp.run([PY, DM / 'steamos/build.py', '--game', self.g.id, '--cc', 'zig', '--zig', self.tools['zig'], '--out', out, '--jobs', str(self.o.jobs)] + ([] if self.o.debug else ['--release']),
                self.logs / 'steamos_host.log', env=self.env)
        self.exe = out / 'nx2s-host'
        if not self.exe.is_file(): raise BuildError(f'build.py made no {self.exe}')
        PROG.end(f'{self.exe.stat().st_size / 2**20:.1f} MB')

    # ---- art: Steam's library images from the game's icon ----
    def art(self):
        PROG.stage('Steam artwork')
        src = next((d / 'icon.jpg' for d in (self.game, self.base) if (d / 'icon.jpg').exists()), None)
        self.art_files = artwork.make(src, self.sw / 'art') if src else {}
        if not src: say('[RED] no game icon in the extraction: the game gets no artwork in Steam')
        elif not self.art_files: say('[RED] no Pillow (pip install pillow): the game gets no artwork in Steam')
        PROG.end(', '.join(sorted(self.art_files)) or 'none')

    # ---- the game folder: everything the device needs, in one folder ----
    def folder(self):
        PROG.stage('game folder', step='linking')
        self.dst = self.o.out / stem(self.g, self.app_name)
        libs = [self.tools['sdl3']] if self.tools.get('sdl3') else []
        if not libs: say('[RED] no libSDL3.so.0 to bundle (devtools/build_sdl3.sh, or porter config sdl3 <path>): the game starts only where the device has SDL3')
        man = gamedir.build(self.dst, profile=self.sp, version=self.pkg_ver, display=self.display, name=self.app_name, host=self.exe, patched=self.patched,
                            romfs=self.game / 'RomFS', shaders=self.dmsv, aoc=self.aoc, language=self.lang, env=self.fps_env, files=self.fps_files, art=self.art_files,
                            libs=libs, progress=lambda what: PROG.set(step=f'linking {what}'))
        self.result['folder'] = str(self.dst)
        PROG.end(f'{man["files"]:,} files, {man["bytes"] / 2**30:.1f} GB')


def emit_result(r):   # the last JSON event of a port (a GUI's done screen); a summary line in the other modes
    if PROG.mode == 'json': PROG._json({'ev': 'result', **r})
