# nx2steamos

Port your own Nintendo Switch games to **SteamOS**, natively, so they appear in Steam's library and play in Game Mode with your controller. Bring your own game files and `prod.keys`, dumped from your own console; nothing from any game is in this repository. It is not an emulator, and there are two engines, one for each kind of SteamOS machine:

- **ARM64 SteamOS** (Valve's SteamOS for the Steam Frame, [SteamOS-ARM-Port](https://github.com/hashtagbasit/SteamOS-ARM-Port) on Snapdragon handhelds and tablets: AYN Odin 2 / 3 and Thor, KONKR Pocket FIT, AYANEO Pocket, Retroid Pocket 6, Lenovo Legion Y700 Gen 3 / 4…): the game's own Arm code runs directly on the device's CPU, Horizon's system services are answered by a host, and NVN graphics are replayed on Vulkan (Mesa's Turnip on Adreno). This is [nxnative](https://github.com/seedds/nxnative)'s approach (iPhone, iPad, Mac) by way of [nx2android](https://github.com/shirai91/nx2android) (the Linux host and the Vulkan backend), in `nxnative/`.
- **The Steam Deck** (x86-64): its CPU cannot run Arm code, so the game's code is **recompiled** to x86-64 once, on your machine, from your files, and runs on a Horizon runtime with NVN on Vulkan (RADV). This is [Savage Wind Recomp](https://github.com/Revivedx/SavageWindRecomp)'s engine, built on [LAS Recomp](https://github.com/peiikai/LASRecomp), in `recomp/` ([docs/RECOMP.md](docs/RECOMP.md)).

> **Status: early development. No game has run through nx2steamos yet.** The ARM64 runtime builds and starts; the port pipeline and the Steam integration are written and tested without a device. The Steam Deck engine builds for x86-64 Linux and passes its checks. TotK 1.4.3's code has been recompiled, but it has not launched on the Deck. [RESEARCH.md](RESEARCH.md) says what is known and measured, [docs/ROADMAP.md](docs/ROADMAP.md) what comes next, [docs/PORT_STATUS.md](docs/PORT_STATUS.md) which games and devices.

## Port a game

The steps below are the ARM64 path; the Steam Deck's follow them.

On the SteamOS device itself (Desktop Mode, or SSH into it), or on any PC or Mac that installs over SSH:

1. **Python 3.11+** and the modules: `python -m venv .venv && .venv/bin/pip install -r requirements.txt` (on SteamOS the system Python is read-only: use the venv).
2. **The tools:** `python -m porter tools --download` fetches Zig 0.17 (the C toolchain the game's host is built with, for the device, from any machine) and the .NET SDK 10 (extraction and the shader translator) from ziglang.org and Microsoft, checked against their published checksums.
3. **SDL 3 for the device** (SteamOS's base image has SDL 2 only): `sh devtools/build_sdl3.sh` on any arm64 machine with Docker makes `dist/sdl3/libSDL3.so.0`, which every game folder then carries.
4. **Your keys:** `python -m porter config keys ~/keys/prod.keys`, and, from another machine, your device: `python -m porter config device deck@steamos.local` (SSH on, with a key: `ssh-copy-id`).
5. `python -m porter doctor` checks everything.
6. **Port** a folder with the game (base NSP / XCI, its update and DLC):

```
python -m porter port ~/Games/Switch/NEO -k ~/keys/prod.keys --install
```

The game folder appears next to the game files (`-o` picks another place), is copied to the device (`~/Games/nx2steamos/`), and is added to Steam's library with artwork made from the game's icon. In Game Mode Steam is running, so Steam is asked to add it itself; `--restart-steam` restarts Steam instead. `python -m porter -h` lists every command: `profiles`, `inspect`, `install`, `device`, `steam`, `tools`, `config`.

### On the Steam Deck

The Deck runs the game's code recompiled for x86-64 by the engine in `recomp/`, which runs the games it was brought up on: so far Savage Wind's *Breath of the Wild* (versions 1.8.2 and 1.9.0, without DLC; `python -m porter profiles --deck`). On a PC or Mac (faster), or on the Deck in Desktop Mode:

1. `python -m venv .venv && .venv/bin/pip install -r requirements-deck.txt` (the code generator's modules, at Savage Wind's versions).
2. `python -m porter config engine recomp`, then `python -m porter tools --download`: Zig and the .NET SDK as above. Build the runtime with `cd recomp && make appimage ARCH=x86_64`, then select the AppImage with `python -m porter config savagewind <AppImage>`. The generator's continuation fix requires this runtime for game code built from this checkout.
3. Your keys and, from another machine, the Deck: `python -m porter config device deck@steamdeck.local` (in Desktop Mode: `passwd`, then `sudo systemctl enable --now sshd`, and `ssh-copy-id deck@steamdeck.local` from your machine).
4. **Port** the folder with the base game and its update:

```
python -m porter port ~/Games/Switch/BotW -k ~/keys/prod.keys --install
```

The game's code is recompiled on your machine (minutes; the generated C is deleted as it is compiled), the folder is copied to the Deck, the game's data image is built there (a few minutes), and the game is added to Steam's library. Launch option `--config` opens Savage Wind's own settings (language, 30 / 60 fps, graphics, shader precompilation); saves and its log (`savagewind.log`) stay in `~/.local/share/nx2steamos/<title ID>/` on the Deck. *Tears of the Kingdom* has an experimental site-free build path but has not launched on the Deck ([docs/RECOMP.md](docs/RECOMP.md), "Games").

## Games

The games nx2android brought up on Android share the Vulkan backend, so they are offered first, as **untested** on SteamOS: 13 Sentinels: Aegis Rim, Graveyard Keeper 2, Hades II, Mario vs. Donkey Kong, NEO: The World Ends with You, Tales of Eternia Remastered, Unicorn Overlord, Unpacking, Viractal, Xenoblade Chronicles 3. nxnative's other profiles can be tried with `--experimental`. See [docs/PORT_STATUS.md](docs/PORT_STATUS.md).

## Playing

- **Controllers** by position, as on a Switch: the bottom face button is B, the right one A. Every controller is a player.
- **Pause menu** (rumble, frame-rate counter, docked or handheld mode): press **Minus and Plus together**, or Esc / F1 on a keyboard. Steam's own menu (the Steam button) quits the game.
- **Touch screen** works as the Switch's. **Text entry** (names): the on-screen keyboard, or Steam's (it opens itself in Game Mode).
- **Saves and settings** stay in `~/.local/share/nx2steamos/<title ID>/`, the log in `~/.local/state/nx2steamos/<title ID>/log.txt` (on the Steam Deck engine: `savagewind.log` in the first folder). Porting a game again keeps them.
- **On the Steam Deck** the controls, menus and settings are Savage Wind's (`recomp/docs/linux.md`).

## For developers

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): the layers, the game folder, the runtime, the port. [docs/ENGINE.md](docs/ENGINE.md): the vendored ARM64 engine and what nx2steamos changed in it; [docs/RECOMP.md](docs/RECOMP.md): the vendored Steam Deck engine. [AGENTS.md](AGENTS.md): rules and where to start, including reverse engineering with REA.
- `sh devtools/check.sh`: every check that needs no device, in Linux containers: on arm64, the host built with clang and Zig, the host's unit tests on glibc, the rewriter's and the porter's tests, the probes on lavapipe; on x86-64, the Steam Deck engine's build and its game-free tests (`sh devtools/check.sh deck` runs only those).
- `probes/`: what a device answers (glibc's TLS, the Vulkan driver's features, zero-copy memory pools).

## Layout

- `porter/`: the port pipeline and CLI (`python -m porter`), adapted from nx2android's; `porter/deck.py` drives the Steam Deck engine.
- `nxnative/`: the ARM64 engine (nx2android's copy of nxnative), with the SteamOS runtime in `nxnative/dm/steamos/` and the game profiles in `nxnative/dm/games/`.
- `recomp/`: the Steam Deck engine (Savage Wind Recomp's tree): the recompiler in `recomp/tools/`, the runtime in `recomp/runtime/`.
- `devtools/`: the dev containers (arm64 and amd64), the SDL3 build, the checks. `probes/`: device probes. `tests/`: unit and architecture tests (`python -m unittest discover tests`).

## Acknowledgements

nx2steamos stands on **[nxnative](https://github.com/seedds/nxnative)** (MIT: the rewriter, the Horizon host, the NVN layer, the game profiles, and everything nxnative credits: d2r-mobile, Ryujinx, Atmosphère, yuzu, LibHac, hactool, SPIRV-Cross, glslang, deko3d…), **[nx2android](https://github.com/shirai91/nx2android)** (MIT: the Linux host, the Vulkan backend, the porter), and, for the Steam Deck, **[Savage Wind Recomp](https://github.com/Revivedx/SavageWindRecomp)** (MIT, Revivedx and its contributors) and **[LAS Recomp](https://github.com/peiikai/LASRecomp)** (MIT, Peiikai), the recompiler and runtime it is built on. The facts about SteamOS on Snapdragon come from **[SteamOS-ARM-Port](https://github.com/hashtagbasit/SteamOS-ARM-Port)**; **[SDL](https://libsdl.org)** (zlib), **[Zig](https://ziglang.org)** (MIT) and **FFmpeg** (LGPL) run or build it.

## License

MIT ([LICENSE](LICENSE)). Each engine keeps its own (MIT: `nxnative/LICENSE`, `recomp/LICENSE`); third-party code keeps its license (`nxnative/dm/third_party`, `nxnative/dm/steamos/third_party`, `recomp/third_party`).
