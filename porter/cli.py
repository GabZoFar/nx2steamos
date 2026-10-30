# The command line (python -m porter …). Every command a GUI would use is here, so a port runs the same headless. Exit codes: 0 done, 1 error, 130 interrupted.
# Adapted from nx2android's porter/cli.py.
import argparse, json, os, sys
from pathlib import Path

PROGRESS = ('auto', 'bar', 'lines', 'json', 'off')


def _parser():
    ap = argparse.ArgumentParser(prog='porter', description='Port your own Switch game files to native SteamOS: ARM64 devices, and the Steam Deck with --engine recomp (docs/ARCHITECTURE.md).')
    sub = ap.add_subparsers(dest='cmd', required=True)
    d = sub.add_parser('doctor', help='check the tools a port needs'); d.add_argument('--json', action='store_true'); d.add_argument('-k', '--keys')
    p = sub.add_parser('profiles', help='list the games the tool can port'); p.add_argument('--all', action='store_true', help='also the iOS profiles not ported to Android or SteamOS'); p.add_argument('--json', action='store_true')
    p.add_argument('--deck', action='store_true', help='the games the Steam Deck engine (--engine recomp) runs')
    i = sub.add_parser('inspect', help='what a game folder holds and whether it can be ported'); i.add_argument('folder'); i.add_argument('--json', action='store_true')
    po = sub.add_parser('port', help='make the game folder (and install it)')
    po.add_argument('folder', help='the game\'s folder: its base game NSP / XCI, updates and DLC')
    po.add_argument('-k', '--keys', help='prod.keys (default: the configured one)')
    po.add_argument('-o', '--out', help='folder the game folder is made in (default: the game files\' folder)')
    po.add_argument('-p', '--version', default='', help='version to pack, v<N> (default: the newest in the folder)')
    po.add_argument('-l', '--language', default='', help='game language, a code or a name (default: English)')
    po.add_argument('--app-name', default='', help='the name in Steam\'s library (default: the game\'s name)')
    po.add_argument('--install', action='store_true', help='install on the SteamOS device afterwards and add it to Steam')
    po.add_argument('--device', default='', help='the SteamOS device over SSH, user@host (default: the configured one; this machine when it runs SteamOS)')
    po.add_argument('--device-dir', default='', help='where games go on the device (default ~/Games/nx2steamos)')
    po.add_argument('--no-steam', action='store_true', help='install without adding the game to Steam\'s library')
    po.add_argument('--restart-steam', action='store_true', help='stop Steam, write its shortcuts and start it again (else Steam is asked to add the game itself)')
    po.add_argument('--debug', action='store_true', help='a development build: debug information, DM_CTL key presses')
    po.add_argument('--experimental', action='store_true', help='try a game not ported to SteamOS or Android yet (a profile guessed from a survey of its files; it may not start)')
    po.add_argument('--fps60', action='store_true', help='60 fps: the profile\'s own frame-rate cap, else the community\'s FPSLocker-Warehouse config of the game\'s build, fetched from GitHub')
    po.add_argument('--engine', choices=('nxnative', 'recomp'), default='', help='nxnative: the game\'s Arm code on an ARM64 SteamOS device; recomp: the code recompiled for '
                    'x86-64, for the Steam Deck (docs/RECOMP.md) (default: the configured one, else nxnative)')
    po.add_argument('--jobs', type=int, default=os.cpu_count() or 8)
    po.add_argument('--progress', default='auto', choices=PROGRESS, help='json: one JSON event per line, for a GUI')
    ins = sub.add_parser('install', help='install a game folder made earlier'); ins.add_argument('folder', help='the game folder (with nx2s-game.json)')
    ins.add_argument('--device', default=''); ins.add_argument('--device-dir', default=''); ins.add_argument('--no-steam', action='store_true'); ins.add_argument('--restart-steam', action='store_true')
    ins.add_argument('--progress', default='auto', choices=PROGRESS)
    dv = sub.add_parser('device', help='what the SteamOS device is (arch, glibc, GPU, SDL3, Vulkan, FFmpeg, Steam)'); dv.add_argument('--device', default=''); dv.add_argument('--json', action='store_true')
    st = sub.add_parser('steam', help='the games this tool put in Steam\'s library on the device'); st.add_argument('--device', default=''); st.add_argument('--json', action='store_true')
    t = sub.add_parser('tools', help='list, or download, the missing tools (Zig, .NET SDK) from ziglang.org and Microsoft')
    t.add_argument('--download', action='store_true', help='download and unpack them into the nx2steamos folder (you accept their licenses)')
    t.add_argument('--only', default='', help='these instead of the missing ones: zig, dotnet, savagewind (the Steam Deck engine\'s runtime) (comma-separated)'); t.add_argument('--json', action='store_true')
    t.add_argument('--progress', default='auto', choices=PROGRESS)
    c = sub.add_parser('config', help='show or change the saved settings'); c.add_argument('key', nargs='?'); c.add_argument('value', nargs='?')
    return ap


def _print_json(obj): print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_doctor(a):
    from porter import toolchain
    rows = toolchain.checks(keys=a.keys)
    if a.json: _print_json([r.as_dict() for r in rows])
    else:
        for r in rows: print(f'{"ok " if r.ok else ("!! " if r.required else "-- ")} {r.name:19} {r.detail}' + (f'\n{"":23}→ {r.fix}' if r.fix and not r.ok else ''))
    return 0 if all(r.ok or not r.required for r in rows) else 1


def cmd_profiles(a):
    from porter import profiles
    if a.deck:
        from porter import deck
        rows = deck.all_profiles()
        if a.json: _print_json([r.as_dict() for r in rows]); return 0
        for r in rows: print(f'{r.id}  {r.status:12} {r.name}' + (f'  ({r.tested})' if r.tested else '') + '  [deck.tsv]')
        print(f'versions: {", ".join(v.name for v in deck.game_versions() if v.supported)} (recomp/runtime/game_versions.h)')
        return 0
    rows = profiles.all_entries(steamos_only=not a.all)
    if a.json: _print_json([r.as_dict() for r in rows]); return 0
    for r in rows: print(f'{r.id}  {r.status:12} {r.name}' + (f'  ({r.tested})' if r.tested else '') + (f'  [{r.source}]' if r.source else ''))
    return 0


def cmd_inspect(a):
    from porter import config, pipeline
    from porter.upstream import BuildError
    try: info = pipeline.inspect(a.folder)
    except BuildError as e:
        if a.json: _print_json({'error': str(e)})
        else: print(e, file=sys.stderr)
        return 2
    ok = bool(info.get('deck')) if config.load().get('engine') == 'recomp' else info['supported']   # (the exit code: the configured engine can port it)
    if a.json: _print_json(info); return 0 if ok else 2
    print(f'{info["title_id"]}  {info.get("name", "")}\n  base: {info["base"]}')
    for u in info['updates']: print(f'  update {u["version"]}: {u["file"]}')
    for d in info['dlc']: print(f'  DLC {d["title_id"]}: {d["file"]}')
    if info.get('pack'): print(f'  packs {info["pack"]} (versions in the folder: {", ".join(info["versions"])})')
    if info['steamos']: print(f'  SteamOS: {info["steamos"]["status"]}' + (f', tested on {info["steamos"]["tested"]}' if info['steamos']['tested'] else ''))
    if info['reason']: print(f'  {info["reason"]}')
    if info.get('experimental'): print('  porter port --experimental tries it')
    print(f'  Steam Deck engine: {info["deck"]["status"]}' + (f', tested on {info["deck"]["tested"]}' if info['deck']['tested'] else '') + ' (porter port --engine recomp)'
          if info.get('deck') else '  Steam Deck engine: not this game yet')
    return 0 if ok else 2


def _with_progress(mode, fn):
    from porter.upstream import BuildError, PROG, nsp
    PROG.mode = nsp.progress_mode(mode)
    environ = dict(os.environ)
    try:
        fn(); return 0
    except BuildError as e:
        PROG.close(ok=False); PROG.say(f'[RED] {e}'); return 1
    except KeyboardInterrupt:
        PROG.close(ok=False); PROG.say('[RED] interrupted'); return 130
    except Exception as e:
        PROG.close(ok=False)
        if PROG.mode == 'json': PROG.say(f'[RED] {type(e).__name__}: {e}')
        raise
    finally:
        PROG.close(ok=False)
        for k in os.environ.keys() - environ.keys(): del os.environ[k]
        os.environ.update(environ)


def cmd_port(a):
    from porter import config, pipeline
    opts = pipeline.Options(folder=Path(a.folder), keys=Path(a.keys) if a.keys else None, out=Path(a.out) if a.out else None, version=a.version, language=a.language,
                            app_name=a.app_name, install=a.install, device=a.device, device_dir=a.device_dir, steam=not a.no_steam, restart_steam=a.restart_steam,
                            debug=a.debug, experimental=a.experimental, fps60=a.fps60, jobs=a.jobs)
    if (a.engine or config.load().get('engine') or 'nxnative') == 'recomp':
        from porter import deck
        return _with_progress(a.progress, lambda: pipeline.emit_result(deck.DeckPort(opts).run()))
    return _with_progress(a.progress, lambda: pipeline.emit_result(pipeline.Port(opts).run()))


def cmd_install(a):
    import shutil
    from porter import config, install
    from porter.upstream import PROG
    def go():
        cfg = config.load(); PROG.plan(1)
        install.install(Path(a.folder).resolve(), device=a.device or cfg.get('device') or '', device_dir=a.device_dir or cfg.get('device_dir') or '', steam=not a.no_steam,
                        restart_steam=a.restart_steam, account=cfg.get('steam_user') or '', ssh=shutil.which('ssh'), rsync=shutil.which('rsync'))
    return _with_progress(a.progress, go)


def _target(device):
    import shutil
    from porter import config, remote
    return remote.target(device or config.load().get('device') or '', shutil.which('ssh'), shutil.which('rsync'))


def cmd_device(a):
    from porter import remote
    from porter.upstream import BuildError
    try: info = remote.probe(_target(a.device))
    except BuildError as e: print(e, file=sys.stderr); return 1
    if a.json: _print_json(info); return 0
    for k, v in info.items(): print(f'{k:22} {v}')
    return 0


def cmd_steam(a):
    from porter import steam
    from porter.upstream import BuildError
    t = _target(a.device)
    try: r = t.sh('python3 - list', input=Path(steam.__file__).read_bytes(), check=False)
    except BuildError as e: print(e, file=sys.stderr); return 1
    try: rows = json.loads(r.stdout.decode(errors='replace').strip().splitlines()[-1])
    except (ValueError, IndexError): print((r.stderr or r.stdout).decode(errors='replace').strip(), file=sys.stderr); return 1
    if isinstance(rows, dict): print(rows.get('error'), file=sys.stderr); return 1
    if a.json: _print_json(rows); return 0
    for row in rows: print(f'{row["appid"]:>10}  {row["name"]}  ({row["exe"]}, account {row["account"]})')
    if not rows: print('no game from nx2steamos in Steam\'s library')
    return 0


def cmd_tools(a):
    from porter import fetch
    from porter.upstream import BuildError
    from porter.fetch import SAVAGEWIND
    only = {'zig': 'Zig', 'dotnet': '.NET SDK', 'savagewind': SAVAGEWIND}
    if bad := [n.strip() for n in a.only.split(',') if n.strip() and n.strip() not in only]: print(f'--only: {", ".join(bad)}? (one of {", ".join(only)})', file=sys.stderr); return 1
    names = [only[n.strip()] for n in a.only.split(',') if n.strip()] or None
    if a.download:
        def go():
            items = fetch.plan(names)
            if not items: fetch.PROG.say('nothing to download: every tool is there'); return
            for it in items: fetch.PROG.say(f'{it.name} {it.version} ({it.size / 2**20:,.0f} MB) from {it.url}, license: {it.license}')
            fetch.download(items)
        return _with_progress(a.progress, go)
    try: items = fetch.plan(names)
    except (BuildError, OSError) as e: print(f'cannot read the download lists: {e}', file=sys.stderr); return 1
    if a.json: _print_json([it.as_dict() for it in items]); return 0
    for it in items: print(f'{it.name:12} {it.version:16} {it.size / 2**20:8,.0f} MB  {it.url}\n{"":12} license: {it.license}')
    print('porter tools --download fetches them' if items else 'nothing to download: every tool is there')
    return 0


def cmd_config(a):
    from porter import config
    if a.key is None:
        for k, v in config.load().items(): print(f'{k:11} {v or "-":40}  {config.KEYS[k]}')
        print(f'({config.path()})'); return 0
    if a.value is None: print(config.load().get(a.key, '')); return 0
    try: config.set_value(a.key, a.value)
    except KeyError as e: print(e.args[0], file=sys.stderr); return 1
    return 0


def main(argv):
    if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8', errors='replace')   # game names and progress bars on a Windows console
    a = _parser().parse_args(argv)
    return {'doctor': cmd_doctor, 'profiles': cmd_profiles, 'inspect': cmd_inspect, 'port': cmd_port, 'install': cmd_install, 'device': cmd_device, 'steam': cmd_steam,
            'tools': cmd_tools, 'config': cmd_config}[a.cmd](a)
