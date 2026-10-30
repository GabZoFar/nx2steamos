# Architecture

nx2steamos turns a player's own Switch game files into a game that runs natively on SteamOS: the player picks the game's folder, runs a port, and gets a game folder that installs on the device and appears in Steam's library. On ARM64 SteamOS it is nx2android's design for another Linux: every game the tool supports is a **game profile**, everything all games share is the **core**. The Steam Deck (x86-64) cannot run Arm code, so it has an engine of its own, which recompiles the game's code ahead of time ([RECOMP.md](RECOMP.md)).

```
porter/  (headless port pipeline and CLI: python -m porter)
  │  ARM64:       inputs → extract → rewrite (DM_TARGET=linux) → shaders → host (Zig) → art → game folder → [install → Steam]
  │  Steam Deck:  inputs → extract → version → game code (Savage Wind's generator, Zig for x86-64) → art → game folder → [install, data image → Steam]  (porter/deck.py)
  │  reuses nxnative's NSP script steps (imported, not copied) and nx2android's shader bundle
  ▼
nxnative/  (the ARM64 engine: nx2android's copy of seedds/nxnative, with nx2android's Android runtime and nx2steamos's SteamOS runtime)
  ├─ dm/host, dm/gfx, dm/tools        core: Horizon host, NVN layer, rewriter, shader split          (shared with iOS and Android)
  ├─ dm/android                       core: the Vulkan backend gfx_vk_*.c, perf, the Darwin compat layer  (shared with Android)
  ├─ dm/steamos                       core: the SteamOS runtime (SDL3 front end, Vulkan platform, audio, movies, build.py)
  └─ dm/games/<title ID>/             profiles: game.tsv + shader tables (+ patches.py) + android.tsv / steamos.tsv

recomp/  (the Steam Deck engine: Savage Wind Recomp's tree; the porter drives it with --engine recomp)
  ├─ tools/                           the recompiler: gen.py (each module → C), layout.py, game_sites.py, patches.py
  └─ runtime/                         the Horizon kernel and services, NVN on Vulkan, the Maxwell shader translator, SDL3, the start screen
```

## Layers and their rules

| Layer | Where | Holds | Must not hold |
|---|---|---|---|
| **Core, shared** | `nxnative/dm/{host,gfx,tools,third_party}` | What every game and platform needs; SteamOS differences behind `__linux__` (Android and SteamOS), `__ANDROID__` (Android only) or `DM_STEAMOS` | Game-specific branches. Keep the diff to upstream small ([ENGINE.md](ENGINE.md)) |
| **Core, Android runtime (shared)** | `nxnative/dm/android/` | The Vulkan replay backend (`gfx_vk_*`), `perf_android.c`, the Darwin compat layer (`compat/`), the shader bundle tool (`tools/shader_vk.py`). Android-only parts behind `__ANDROID__`; the Linux side calls `vkp_*` | SteamOS code beyond the `vkp_*` calls and `#ifdef` splits |
| **Core, SteamOS runtime** | `nxnative/dm/steamos/` | The SDL3 front end, the Vulkan platform (`vk_platform.c`), audio, movies, rumble, the run-time loaders of SDL3 and FFmpeg, `build.py`, the profile header tool, the vendored SDL3 headers, the overlay's SPIR-V | Game-specific code (no title IDs: `tests/test_architecture.py`) |
| **Game profile** | `nxnative/dm/games/<title ID>/` | Data only: `game.tsv`, shader tables, `patches.py`, `android.tsv`, `steamos.tsv` | Code paths |
| **Steam Deck engine** | `recomp/` | Savage Wind Recomp's recompiler and runtime, as its upstream has them; nx2steamos's changes small and listed in [RECOMP.md](RECOMP.md) | Generated code, extracted game files (`recomp/.gitignore`: `generated*/`, `extracted*/`, `build/`) |
| **Port pipeline** | `porter/` | The stages that turn game files into a game folder (`pipeline.py` for the ARM64 engine, `deck.py` for the Steam Deck's); the toolchain check and downloads; the profile registry; installing on a device; Steam's library (`steam.py`, standard library only: it runs on the device) | UI |
| **Dev tools** | `devtools/`, `probes/` | The arm64 dev container, the SDL3 build, the checks, device probes | Anything a port needs |

## Game profiles on SteamOS

A profile is offered for SteamOS when it has a `steamos.tsv`, or an `android.tsv`: the Android port's games replay NVN with the same Vulkan backend, so they are offered with status **untested** (and the Android profile's `env` rows and `docked` / `fps` / `rumble` settings) until someone checks them on a device and writes a `steamos.tsv`. `steamos.tsv` holds, one `key<TAB>value` row each (`nxnative/dm/steamos/tools/steamos_profile.py`):

| Key | Meaning |
|---|---|
| `status` | `playable` (tested end to end on a device), `wip` (plays, parts missing: [PORT_STATUS.md](PORT_STATUS.md) says which), `boots`, `untested`, `experimental` |
| `tested` | `<version> <device>`: what the status was checked on (required for playable and wip) |
| `requires` | device features: `bcn` (BC texture compression; every Adreno has it) |
| `setting` | `<name>=<value>`: the pause menu's defaults on first launch (`docked`, `fps`, `rumble`), the game folder's `settings.txt` |
| `env` | `<KEY>=<VALUE>`: a host switch the game needs on SteamOS, the game folder's `env.txt` |

## The game folder

What a ported game is on the device; Steam's shortcut starts its launcher (`porter/gamedir.py`):

```
<Stem>/                       e.g. ~/Games/nx2steamos/NeoTheWorldEndsWithYou/
  nx2steamos-run              the launcher: exec bin/nx2s-host <folder>/mods [-- Steam's launch options]
  bin/nx2s-host               the host, built for this game's profile (aarch64, glibc ≥ 2.38, nothing else linked)
  lib/libSDL3.so.0            SDL 3 for the device (its base image has SDL2 only): devtools/build_sdl3.sh
  mods/                       the rewritten code modules (DM_TARGET=linux)
  RomFS/  aoc/<n>/            the game's files, its DLC
  shaders.dmsv                the Vulkan shaders
  env.txt  settings.txt       host switches (${DM_GAME_DIR} = this folder), the pause menu's defaults
  fpslock.tsv                 with --fps60: FPSLocker's memory writes
  art/                        portrait, wide, hero and icon PNGs made from the game's icon
  nx2s-game.json              the manifest, written last: a folder with it is complete
```

The Steam Deck engine's folder is laid out for Savage Wind's runtime instead: the launcher, `bin/<AppImage>` (built from this checkout's `recomp/`), `game/exefs/`, `game/romfs/`, `game/swgame.so` (the game code recompiled for x86-64) and `game/romfs.bin` (the data image, built on the device), `art/`, the manifest (`"engine": "recomp"`): [RECOMP.md](RECOMP.md), "The porter drives it".

**Nothing the player makes lives there.** The host (`dm/steamos/front_sdl.c`) finds its folder from its own path and keeps the game's state in `$XDG_DATA_HOME/nx2steamos/<title ID>` (`DM_ROOT`: `save/`, the player's `settings.txt` and own `env.txt`, the Vulkan pipeline caches, captured shaders), its log in `$XDG_STATE_HOME/nx2steamos/<title ID>/log.txt` (with the previous run's), and `DM_TEMP` under `$XDG_CACHE_HOME`. Porting again replaces the folder and keeps all of that.

## The runtime (`nxnative/dm/steamos`)

- **Process and threads.** `main` (front_sdl.c) blocks SIGTERM (so the host's graceful exit takes it), sets the folders up, loads SDL3 (`sdl_dyn.c`: `<folder>/lib` first, then the system's) and opens the window on the main thread, then starts the host's own `main` (compiled as `dm_host_main`) on a thread with a 16 MB stack, as the Android app does; the main thread runs SDL's events. Without a display (`DM_HEADLESS=1`, or no `WAYLAND_DISPLAY` / `DISPLAY`) there is no window, input or sound: how a run is checked over SSH.
- **Input.** SDL's gamepads, one player each, buttons by position (bottom B, right A, left Y, top X); triggers past half are ZL / ZR. The touch screen goes to the game, relative to the picture. In Desktop Mode the keyboard maps as on the Mac host. The pause menu (rumble, frame-rate counter, docked / handheld, resume): Minus + Plus together, the Guide button where Steam leaves it, Esc or F1. The game's software keyboard is the overlay's keyboard, typed with the controller, a keyboard, or Steam's on-screen keyboard (SDL opens it in Game Mode).
- **Graphics.** The Android port's backend unchanged but for its platform calls (`vkp_*`, `vk_platform.c`): the surface is SDL's (made on the main thread), the swapchain UNORM and opaque and remade at a new size; memory pools are made GPU memory as an exported dma-buf mapped over the guest range, or a placed map, else copied (`DM_VK_POOLS=auto|dmabuf|placed|copy`).
- **Sound and movies.** The host's AudioUnit calls on an SDL3 stream (PipeWire); H.264 and AAC through the system's FFmpeg, opened at run time (`av_dyn.c`); VP9 and Vorbis are in the engine.
- **Build.** `build.py --game <id> --cc zig` compiles the host, the graphics layer, the Android backend and this runtime for `aarch64-linux-gnu.2.38`; the overlay's shaders are committed SPIR-V (`tools/gen_shaders.py`). The result links `libc` and `libm` only.

## The port

`python -m porter port <game folder> -k <prod.keys> [--install]` runs these stages; the slow ones (extraction, rewrite, shader split, the translator build, the Vulkan shaders) keep their output with a key of their inputs and are skipped when it is unchanged (nxnative's NSP script's mechanism).

| Stage | What | Output (in `<game folder>/<title ID>_build/`) |
|---|---|---|
| inputs | the base game, updates and DLC; the profile; the version; disk space | |
| extract | `nx_extract` (built with the .NET SDK) | `extract/<version>/{ExeFS,RomFS,control.nacp,icon.jpg}`, `aoc/` |
| 60 fps | `--fps60`: the profile's own cap, or the build's FPSLocker-Warehouse config converted for the host | `steamos/fpslock/` |
| rewrite | `patch_nso.py` with `DM_TARGET=linux` | `steamos/patched_<version>/` |
| shaders | `shader_split.py`, then `shader_vk.py` with the translator built from source (+ shaders the device captured) | `shader/pkg`, `steamos/shaders.dmsv` |
| host | `dm/steamos/build.py --release` with the player's Zig | `steamos/host/nx2s-host` |
| art | Steam's images from the game's icon | `steamos/art/` |
| game folder | the folder above, big trees hard-linked | `<out>/<Stem>/` |
| install | over SSH (rsync, else tar) or locally; the device checked (arm64, glibc, Vulkan, FFmpeg); then the Steam shortcut, by running `porter/steam.py` on the device | `~/Games/nx2steamos/<Stem>` |

**Steam.** With Steam closed, `shortcuts.vdf` is written directly (the player's original kept as `shortcuts.vdf.nx2steamos-backup`) under the appid Steam's formula gives, and the artwork is put in `config/grid/`. With Steam running (always, in Game Mode), Steam is asked to add the game (`steam://addnonsteamgame/` with the folder's `.desktop` file, as Valve's `steamos-add-to-steam`) and the artwork follows under the id Steam gives it; `--restart-steam` stops Steam, writes, and starts it again instead.

**Where the port runs.** On the SteamOS device itself (Desktop Mode or SSH: Python, Zig and .NET all run on aarch64 Linux), or on any PC or Mac, installing over SSH (`porter config device user@host`; SteamOS-ARM-Port has SSH on).

**The Steam Deck.** `--engine recomp` (or `porter config engine recomp`) runs `porter/deck.py` instead: the same extraction, then the engine's version check, the game code built with Savage Wind's generator and Zig for x86_64-linux-gnu (on the PC or Mac: minutes; on the Deck itself: about a quarter of an hour), the Deck's game folder, and at install the data image built on the Deck by the folder's own launcher before Steam's shortcut. The game code and the runtime must have the same id ([RECOMP.md](RECOMP.md)).

## What the player installs

Python 3.11+ with `requirements.txt`, then `python -m porter tools --download` for Zig 0.17 and the .NET SDK 10 (from ziglang.org and Microsoft, checked against the checksums their lists give, into the player's nx2steamos folder: `~/.local/share/nx2steamos/toolchains` on Linux), their own `prod.keys`, and an arm64 `libSDL3.so.0` to bundle (`devtools/build_sdl3.sh` until releases carry one). For the Steam Deck: `requirements-deck.txt` (the generator's capstone, unicorn and lz4 at Savage Wind's versions) and Savage Wind's runtime (`porter tools --download` fetches it too once `porter config engine recomp` is set), no SDL3. `python -m porter doctor` checks it all.

## Adding a game

1. `python -m porter port <folder> --experimental --install`: the survey, a provisional profile, and a first try on the device. Bring it up with the engine's tools and REA ([AGENTS.md](../AGENTS.md)): the profile moves to `nxnative/dm/games/<title ID>/`, fixes go into the core as general rules.
2. Add `steamos.tsv` with `status` and `tested`.
3. Port, install, play; raise `status` to `playable` when it is, and add its row to [PORT_STATUS.md](PORT_STATUS.md).
