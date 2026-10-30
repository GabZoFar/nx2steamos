# Roadmap

Cheapest validation first, as nx2android's research suggested for Android. Each milestone names what proves it done.

## M0: the runtime builds and starts (done, 2026-10-28)

The engine vendored from nx2android; `DM_TARGET=linux` in the rewriter and loader; the SteamOS runtime (SDL3 front end, Vulkan platform, audio, movies, rumble); the port pipeline, game folder and Steam integration; the dev container and device probes.
*Proof:* the host builds with clang and Zig (glibc 2.38, libc and libm only), starts headless and with a window up to loading a game's modules; host, rewriter and porter tests pass (`sh devtools/check.sh`).

## M1: the device answers

On a SteamOS ARM device (first: the Legion Y700 Gen 4 or a Snapdragon handheld on SteamOS-ARM-Port): `python -m porter device`; `probes/tlsprobe` (glibc keeps tp[1]), `probes/vkprobe` (Turnip's features), `probes/vkzc` (which pool method is zero-copy and CPU-cached).
*Proof:* the three probes' output in `docs/` and the pool method chosen.

## M2: a game boots headless

A port of an Android-playable game run over SSH with `DM_HEADLESS=1`: the modules load, the guest reaches `nnMain`, NVN's null device or the Vulkan backend initialises.
*Proof:* its log, past `>>> nnMain`.

## M3: first pixels, then a game played

The same game in Game Mode from Steam's library: the window, the swapchain on Turnip, controllers, sound. Then the Android list one by one (Mario vs. Donkey Kong, Unpacking, Hades II, NEO…), each measured with nx2android's Adreno workarounds on and off.
*Proof:* `steamos.tsv` with `status playable` for the first game, its row in [PORT_STATUS.md](PORT_STATUS.md).

## M4: a player's tool

- Release bundles: `libSDL3.so.0` built in CI (an arm64 runner, Ubuntu 22.04), the porter as a single download, checksums.
- A GUI for Desktop Mode (PySide6, as nx2android's `gui/`), or better a **Decky plugin** in Game Mode: pick a game folder, port, and add it to the library through Steam's own API (no restart).
- Steam artwork beyond the icon: optional SteamGridDB with the player's own API key.

## M5: playing well

- Performance: game threads on the big cores, `uclamp` hints (SteamOS-ARM-Port's konkrd gives Steam games a boost), present timing from `VK_KHR_present_wait` / `VK_EXT_present_timing` under gamescope, frame pacing at 90 / 120 / 144 Hz panels.
- Motion controls: the controller's gyro to the game's six-axis sensor (a new host API; Steam Input's virtual pads have no sensors, so it needs Steam Input off for the game, or a DSU source).
- Bundled FFmpeg (LGPL) where the device has none.
- More profiles: nxnative's iOS games through `--experimental`, and the engine's Vulkan- and GL-rendered games (pass-through to the device's Vulkan).

## The Steam Deck track (`recomp/`)

The Deck's CPU is x86-64, so its engine recompiles the game's code ahead of time instead ([RECOMP.md](RECOMP.md)). Its milestones run beside M1–M5:

- **D0: the engine is here and builds (done, 2026-10-28).** Savage Wind Recomp `860f372` vendored in `recomp/`. *Proof:* the runtime builds for x86-64 Linux, and its game-free tests pass in the amd64 container: layout, sites, 159,968 FP / SIMD cases against Unicorn, the software FMA, 13,236 translated shaders compiled and validated (`sh devtools/check.sh deck`).
- **D1: the porter drives it (written and checked without a game, 2026-10-29).** `python -m porter port --engine recomp` (`porter/deck.py`): the extraction, the engine's version check, the game code library built with Savage Wind's generator and Zig for `x86_64-linux-gnu` on the player's PC or Mac (or on the Deck itself), the Deck's game folder carrying a matching runtime built from `recomp/`, installed over SSH with the data image built on the Deck, added to Steam like the ARM64 games. Checked: the id against make's, the build of made-up modules loaded by the local runtime, the launcher, the install's checks ([RECOMP.md](RECOMP.md)). *Proof still to come:* a port of a game the engine already runs (*Breath of the Wild* 1.9.0), from a dump of the player's own console, launched from Steam in Game Mode.
- **D1b: more than one game (in progress).** The version table recognizes TotK 1.4.3, the porter builds its game code without sites, and the runtime accepts that library while skipping Breath of the Wild's site-dependent hooks. A custom runtime built from `recomp/` is required. Runtime service and graphics bring-up remains.
- **D2: the Deck answers.** `probes/vkprobe` and `probes/vkzc` on RADV (VANGOGH): which pool path is zero-copy (`VK_EXT_external_memory_host`, `VK_EXT_map_memory_placed`, dma-buf), present timing under gamescope. *Proof:* the output in `docs/`.
- **D3: a second game (in progress).** *The Legend of Zelda: Tears of the Kingdom* 1.4.3 runs on a Steam Deck: menus, saves, the opening's gameplay and cutscenes lit, at 30 fps with sound, 60 where its frames allow. Its memory in the alias region (out of shaders' reach) and some surfaces remain ([RECOMP.md](RECOMP.md), "Games").

## Not planned

- Anything that ships or downloads game files or keys, or works from a copy that was not dumped from the player's own console.
