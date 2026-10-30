# Can nxnative's approach bring Switch games to SteamOS, natively?

*Research notes, 2026-10-28. Based on a read of [nxnative](https://github.com/seedds/nxnative) (via the user's fork, upstream `6034fca` as vendored by nx2android), [nx2android](https://github.com/shirai91/nx2android) @ `584708c`, [hashtagbasit/SteamOS-ARM-Port](https://github.com/hashtagbasit/SteamOS-ARM-Port), Valve's SteamOS package repositories and Mesa/glibc sources, plus measurements in an arm64 Linux container. **Nothing has been run on a SteamOS device or with a real game yet.** Confidence: **H** read in a primary source or measured, **M** strong indirect evidence, **L** inference.*

## TL;DR

**Yes for ARM64 SteamOS, and most of the work already exists.** nxnative runs a Switch game's own ARM64 code natively by rewriting it statically and answering Horizon's calls in a host; nx2android ported that host to Linux (bionic) and replaced Metal with a Vulkan backend. SteamOS on ARM is Linux too, with Mesa's Turnip driver for the same Adreno GPUs nx2android was brought up on. What differs is the libc (glibc), the window/input/audio stack (SDL3 under gamescope and Steam Input instead of an Android activity), how memory pools become GPU memory (dma-bufs or placed maps instead of AHardwareBuffers), and packaging (a game folder in Steam's library instead of an APK).

- **Targets:** Valve's SteamOS for ARM (the Steam Frame image), and the community [SteamOS-ARM-Port](https://github.com/hashtagbasit/SteamOS-ARM-Port), which runs that userspace on Snapdragon 8 Gen 2 / 8 Gen 3 / 8 Elite handhelds (AYN Odin 2 / 3, Thor, KONKR Pocket FIT, AYANEO Pocket S2, Retroid Pocket 6…) and the Lenovo Legion Y700 Gen 3 / 4, the very tablet nx2android's games were tested on.
- **The Steam Deck needs another engine.** Its CPU is x86-64, so the game's Arm code cannot run there as it is. Static recompilation translates it to x86-64 ahead of time, and since mid-2026 that works for Switch games: Savage Wind Recomp (MIT) plays *Breath of the Wild* on a Deck. nx2steamos vendors it as its Deck engine (section 8, [docs/RECOMP.md](docs/RECOMP.md)).
- **State of this repository:** the SteamOS runtime is written and builds (140 sources, clang and Zig), boots to the point where it needs a game, headless and with a window; the port pipeline, Steam integration and device probes are written and unit-tested. The next step is a device.

## 1. What the pieces are

| Piece | What it gives nx2steamos | License |
|---|---|---|
| [nxnative](https://github.com/seedds/nxnative) | The engine: `patch_nso.py` (static ARM64→ARM64 rewrite), the Horizon host `dm/host` (SVCs, services, fs, hid, audio, movies), the NVN record layer `dm/gfx/nvn`, the shader chain (Ryujinx's translator), 54 game profiles, `nx_extract` (LibHac) | MIT |
| [nx2android](https://github.com/shirai91/nx2android) | nxnative's host on Linux (`DM_TARGET=android`, `darwin_compat.c` for the Mach calls), the **NVN→Vulkan replay backend** `dm/android/gfx_vk_*.c` (~410 KB), the SPIR-V shader bundle (`shader_vk.py`), 11 games brought up on Adreno, and the `porter` pipeline | MIT |
| [SteamOS-ARM-Port](https://github.com/hashtagbasit/SteamOS-ARM-Port) | The device side: what SteamOS on Snapdragon is (kernels, Mesa, gamescope, InputPlumber's virtual Deck controller), and a working example of building for that rootfs | GPL-2.0 (not copied: read for facts) |
| [REA](https://github.com/morluto/rea) | Reverse engineering for bring-up: an MCP server over Ghidra / Hopper / IDA (open a game module converted with `nso2elf.py`, list and analyse functions, match functions across builds for the rewriter's cuts) | MIT |

## 2. SteamOS on ARM: the platform

| | Steam Frame (image 20261002.6232440, vr/aarch64) | SteamOS-ARM-Port (same rootfs) | Conf. |
|---|---|---|---|
| glibc | **2.39** | 2.39 | M-H |
| Mesa | Valve's Turnip + zink, 26.3.0-devel | 8 Gen 2 / 8 Elite: Mesa 26.2.3 + patches (the a830 chip ID); 8 Gen 3: Valve's | H |
| Kernel, page size | linux-618-deckard 6.18, **4 KB pages**, 48-bit VA | ROCKNIX-based 7.1–7.2, **4 KB pages** | H |
| SDL | **SDL2 2.30 only, no SDL3** | the same | M |
| FFmpeg | 7.0 (libavcodec.so.61) in Valve's repo | its own FFmpeg patch | M |
| Vulkan loader, PipeWire | 1.4.309, 1.4.9 | the same | H |
| Session | Steam with `-deckard -vrgamepadui` (VR) | gamescope Game Mode + KDE Desktop Mode | H |

Sources: the port's [build-gamescope-in-rootfs.sh](https://github.com/hashtagbasit/SteamOS-ARM-Port/blob/main/scripts/build-gamescope-in-rootfs.sh) and [apply-overlays.sh](https://github.com/hashtagbasit/SteamOS-ARM-Port/blob/main/scripts/apply-overlays.sh) ("SteamOS is 2.39"); Valve's [holo-packages archlinux-deckard](https://holo-packages.steamos.cloud/archlinux-deckard-hotfixes/goldmaster/) repositories (glibc 2.39, ffmpeg 7.0, pipewire 1.4.9, vulkan-icd-loader 1.4.309; kernel configs with `CONFIG_ARM64_4K_PAGES=y`); [gpuinfo report 52003](https://vulkan.gpuinfo.org/displayreport.php?id=52003) (SteamOS arm64, Adreno 750, Mesa 26.3.0-devel). Newer Valve snapshots carry glibc 2.42–2.43: a future image may jump.

**Native arm64 Steam games** run in "Steam Linux Runtime 4.0 - Arm64" (pressure-vessel, Debian 13, glibc 2.41, SDL3 3.4.18). **Non-Steam shortcuts run on the host**, without a container, unless a compatibility tool is picked for them (M). So a ported game uses the host's glibc 2.39, Mesa and PipeWire, and must bring what the host lacks (SDL3).

### Turnip on Adreno 740 / 750 / 830

From [Mesa's tu_device.cc](https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.2.3/src/freedreno/vulkan/tu_device.cc) (H unless marked):

| What the backend needs | Turnip |
|---|---|
| BC1–BC7 (Switch textures) | yes (ASTC LDR too) |
| geometry, tessellation, transform feedback (with streams) | yes |
| descriptor indexing, buffer device address, timeline semaphores, dynamic rendering | yes |
| `shaderFloat64` | **no** (as on Android's Adreno driver: the backend already enables everything but it) |
| `VK_EXT_external_memory_host` (Metal's zero-copy model) | **no** (the msm kernel interface has no userptr) |
| `VK_EXT_map_memory_placed` | yes: whole allocations only, page-aligned, `memoryUnmapReserve` yes |
| dma-buf export (`VK_EXT_external_memory_dma_buf`) | yes, with `VkExportMemoryAllocateInfo`; the fd can be mmapped (a VM_PFNMAP mapping: no O_DIRECT, shared futexes or process_vm_* through it, as Android's) |
| a HOST_CACHED + HOST_COHERENT memory type | yes (type 1, with msm ≥ 1.8 and the cached-coherent quirk; present on all three GPUs) |
| present_id / present_wait (and the 2 versions) | yes (25.2+ / 25.3+) |
| `VK_GOOGLE_display_timing` | through gamescope's WSI layer (M); `VK_EXT_present_timing` on a750 / a830 (26.1+) |
| custom border colours, extended dynamic state 3 (mostly) | yes |

**The one deep design point** (nx2android's RESEARCH.md §2, solved there with AHardwareBuffers) is that NVN gives the GPU the game's own memory (GPU address = CPU address). On Turnip it is solvable two ways, both now in `dm/steamos/vk_platform.c`: export the pool's Vulkan memory as a dma-buf and `mmap(MAP_FIXED)` it over the guest range (Android's method, with an fd for the read-only remaps nvn_dirty's defrag needs), or let the driver place the mapping itself (`VK_EXT_map_memory_placed`). `probes/vkzc.c` decides on a device, and whether the mapping is CPU-cached.

### glibc and the rewriter's thread slots

The rewritten code finds each guest thread's slot block through `tpidr_el0`: on Android at `tpidr_el0[2]`, bionic's `TLS_SLOT_APP`. **On glibc that word is the executable's own static TLS** (TLS variant I: a 16-byte TCB, then the executable's block at tp+16), so the Android target would corrupt the host. The TCB's second word, `tcbhead_t.private`, is never read or written by glibc on aarch64 (2.35, 2.39 and 2.42 sources grepped; the stack and pointer guards are globals on aarch64) (H). nx2steamos adds **`DM_TARGET=linux`**: the block hangs off `tpidr_el0[1]`, one load per slot as on Android. Measured in the container (glibc 2.41): tp[1] starts NULL in every thread and survives malloc, stdio, locales, `dlopen(libvulkan)`, signals and thread creation (`probes/tlsprobe.c`). The field is reserved to the implementation, so a future glibc could take it: the loader checks it is free at start-up and the probe runs on each device.

Other glibc facts: `pthread_gettid_np` exists only from 2.42 (a shim covers it; every caller asks about itself); the host uses `strlcpy` (2.38) and `arc4random` (2.36), so it is built against **glibc 2.38**, under the devices' 2.39.

## 3. Steam's side

- **Library:** a ported game is a non-Steam shortcut in `userdata/<account>/config/shortcuts.vdf` (binary KeyValues) with grid artwork in `config/grid/<appid>p.png` (600×900), `<appid>.png` (920×430), `<appid>_hero.png`. The appid is `crc32(quoted Exe + name) | 0x80000000`; Steam keeps a valid one it finds in the file, and gives new shortcuts a random one otherwise ([steam-for-linux #9237](https://github.com/ValveSoftware/steam-for-linux/issues/9237), [Steam ROM Manager](https://github.com/SteamGridDB/steam-rom-manager)). Steam rewrites the file from memory when shortcuts change, so it is written only with Steam closed. **Steam is always running in Game Mode**: there, the game is added the way Valve's own `steamos-add-to-steam` does (`steam steam://addnonsteamgame/<.desktop file>`), and the artwork follows under the id Steam gives it.
- **Controllers:** with Steam Input on, SDL sees "Steam Virtual Gamepad" devices with positional buttons (Xbox labels); the Switch's A is EAST, B SOUTH, X NORTH, Y WEST (H). On the port, InputPlumber presents the built-in controls as a Steam Deck controller with gyro and rumble; the virtual gamepad has no motion sensors, so gyro aiming needs Steam Input's gyro-to-stick, or Steam Input off for the game (M).
- **On-screen keyboard:** `steam://open/keyboard`; SDL 3.4.12+ opens it itself on `SDL_StartTextInput` with `SDL_ENABLE_STEAM_SCREEN_KEYBOARD=1` (H). Games run on Xwayland under gamescope.
- **SDL3 is not on the device**, so nx2steamos bundles `libSDL3.so.0` in each game folder, built against Ubuntu 22.04's glibc 2.35 (`devtools/build_sdl3.sh`: SDL 3.4.18, needs `GLIBC_2.34`, measured).

## 4. What the port changes, component by component

| Component | Android (nx2android) | SteamOS (nx2steamos) | Work |
|---|---|---|---|
| Rewriter `patch_nso.py` | `DM_TARGET=android` (tp[2]) | `DM_TARGET=linux` (tp[1]) | small, done |
| Host `dm/host` + `darwin_compat.c` | bionic | glibc: `getdtablesize`, `stdbool`, `pthread_gettid_np`, fonts, audio and touch defaults | small, done |
| Vulkan backend `gfx_vk_*.c` | ANativeWindow, AHardwareBuffer pools | SDL surface (made on the main thread), dma-buf / placed pools, UNORM swapchain, opaque alpha, resize | medium, done, untested on Turnip |
| Front end | NativeActivity, JNI, on-screen gamepad | SDL3: controllers by position, touch, Desktop Mode keyboard, pause menu, text entry, rumble | medium, done |
| Audio | AudioUnit on AAudio | AudioUnit on SDL3 streams (PipeWire) | small, done |
| Movies (H.264, AAC) | MediaCodec | FFmpeg's libavcodec, opened at run time (ABI-stable fields only) | small, done, tested against FFmpeg 7.1 |
| Performance | ADPF, `setFrameRate` | none yet; the adaptive rate skips 40 fps on a panel 40 does not divide | later: uclamp / big cores |
| Build | NDK clang | **Zig** (`zig cc -target aarch64-linux-gnu.2.38`) from any machine, the device included | done |
| Package | APK + data pack, adb | a game folder (launcher, host, `lib/libSDL3.so.0`, modules, RomFS, shaders, art) over SSH / rsync, a Steam shortcut | done |

## 5. Measured so far (no device)

In an arm64 Debian 13 container on an Apple-silicon Mac (`devtools/container`, glibc 2.41, Mesa 25.0 lavapipe):
- The host builds: 140 sources, 0 errors with clang 19 and with Zig 0.17; a release build is 1.5 MB, needs `GLIBC_2.38` at most and links `libc.so.6` and `libm.so.6` only (SDL3, Vulkan and FFmpeg are opened at run time). Zig also builds it on macOS in about 5 s.
- It runs: headless, and on Xvfb with the bundled SDL 3.4.18 (an X11 window, 1280×720), up to loading the game's modules, where it stops for want of a game; the crash handler prints registers and every thread's stack on glibc.
- The host's own unit tests on glibc: 15 pass, 4 skipped (Mac-only, or stale in the vendored engine), 3 to look at (`t_fs`; `t_fs_case`'s fault injection; `t_pctl` does not link).
- The rewriter's slot encodings per target: tested (`nxnative/dm/tools/tests/test_target_slots.py`).
- Movies through the system's FFmpeg (7.1, libavcodec 61; `nxnative/dm/steamos/tools/media_test.sh`): a 12-frame H.264 clip with B-frames comes out in presentation order with its times, each picture's NV12 byte-identical to ffmpeg's own decode; 1 s of AAC decodes to the 440 Hz stereo sine encoded.
- `probes/vkzc.c` on lavapipe: host-pointer import works there; placed maps and dma-bufs are not offered by lavapipe; moving the driver's mapping crashes it (as expected): the probe's methods are what the device must answer.

## 6. Risks and unknowns

1. **No device yet.** Turnip is not Qualcomm's Android driver: nx2android carries Adreno-proprietary workarounds (submission splits, LRZ touches, descriptor-pool accounting) that may cost on Turnip. Measure each game with them on and off.
2. **Zero-copy pools on Turnip** (dma-buf first, placed maps second): to be measured by `probes/vkzc.c`; the copied fallback works but is slow on big pools.
3. **FFmpeg on the device** (M): without libavcodec, movies have no pictures or AAC sound; bundling an LGPL build is the fallback.
4. **The Steam Frame** runs Steam in VR: a flat game likely appears on a virtual screen, and its controllers are unknown. The handhelds and tablets of the port are the first targets.
5. **Steam's shortcut handling** is undocumented (an open beta bug drops shortcuts: [#13754](https://github.com/ValveSoftware/steam-for-linux/issues/13754)).
6. **Legal:** as nx2android notes, the pipeline decrypts the player's own dumps with their `prod.keys`, which is what Nintendo's February 2026 DMCA notices against 13 emulator projects cited. Nothing from any game, and no keys, are in this repository or ever downloaded by it.

## 7. Related projects

| Project | License | Use |
|---|---|---|
| [Zig](https://ziglang.org) 0.17 | MIT | the C toolchain: aarch64 glibc targets up to 2.44 from any host |
| [SDL](https://github.com/libsdl-org/SDL) 3.4 | zlib | window, input, audio; headers vendored (3.2), library bundled |
| FFmpeg (libavcodec) | LGPL-2.1+ | H.264 / AAC, the system's |
| [Steam ROM Manager](https://github.com/SteamGridDB/steam-rom-manager), [BoilR](https://github.com/PhilipK/BoilR) | GPL-3.0, Apache-2.0 | references for shortcuts.vdf and restarting Steam |
| [gamescope](https://github.com/ValveSoftware/gamescope) | BSD-2 | the compositor games run under |
| LibHac, hactool, nstool | MIT / ISC / MIT | NSP extraction (LibHac through `nx_extract`) |
| Ryujinx / Ryubing | MIT | the shader translator, services reference |
| Eden (yuzu) | GPL-3.0 | NCE on Linux arm64: the emulator alternative (needs 39-bit titles) |
| [LASRecomp](https://github.com/peiikai/LASRecomp) | MIT | the closest analogue: player-supplied dump, `zig cc` on the player's machine, SDL3 + FFmpeg; the base of Savage Wind |
| [Savage Wind Recomp](https://github.com/Revivedx/SavageWindRecomp) | MIT | the Steam Deck engine, vendored in `recomp/` (section 8) |
| [ScarletNX](https://github.com/scarletnx/ScarletNX) | GPL-3.0 | a Switch static recompiler through LLVM (its decoder from QEMU's `a64.decode`) with its own Horizon HLE and Maxwell GPU emulation; Linux, Windows, Android. A reference only: its license and its own runtime |
| [nxrecomp](https://github.com/drakolordx7/nxrecomp) | GPL-3.0 | AArch64 → C++ checked against Unicorn, NVN on Vulkan, Windows x64 (*Metroid Prime Remastered*). A reference only |
| [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) | GPL-2.0 | arm64 since 3.3: a future in-Steam front end (add games without restarting Steam) |

## 8. The Steam Deck (x86-64)

- **Why not the ARM64 design** (H): the Deck's APU (Van Gogh, OLED: Sephiroth) has Zen 2 x86-64 cores; nxnative's rewrite keeps the game's Arm code and runs it on an Arm CPU. On x86-64 the code must be translated: by a JIT at run time (what Ryujinx- and yuzu-family emulators do) or ahead of time, once, into native code (static recompilation).
- **Static recompilation of Switch games exists now** (H, read in each project's repository, 2026-10-28):
  - [LAS Recomp](https://github.com/peiikai/LASRecomp) (MIT): *Link's Awakening*. Releases for Windows, macOS and Linux, Steam Deck included.
  - [Savage Wind Recomp](https://github.com/Revivedx/SavageWindRecomp) (MIT, built on LAS Recomp): *Breath of the Wild* 1.9.0, Windows and Linux.
  - [ScarletNX](https://github.com/scarletnx/ScarletNX) (GPL-3.0): a general one through LLVM, experimental.
  - [nxrecomp](https://github.com/drakolordx7/nxrecomp) (GPL-3.0): *Metroid Prime Remastered*, Windows.
  - Press reports (M, not checked in a repository): suyu's last release (0.0.4) added a recompiler mode before its repository was archived on 2026-09-13 ([xenospectrum](https://xenospectrum.com/en/suyu-static-recompiler-performance-evidence/), [wccftech](https://wccftech.com/nintendo-switch-emulation-leap-forward-suyu-native-pc-performance/)), and per-game recomps are listed on [Recompendium](https://recompendium.com/en/tool/scarletnx/).
  - The MIT ones fit this repository's license; the GPL ones would make it GPL-3.0.
- **How Savage Wind does it** (H, its source): the generator, in Python with capstone, turns each module into C. Functions come from the `.eh_frame` FDEs and jump tables are resolved; its notes report 122,961 functions in Breath of the Wild's `main` and 40,461 in `sdk`. The C is compiled `-O1 -march=x86-64-v3` (AVX2 + FMA, which Zen 2 has), with Zig on the player's machine. The runtime keeps guest addresses equal to host addresses, as nxnative does, runs the SDK's own code, and answers its kernel calls and IPC.
- **On a Deck** (M: Savage Wind's measurement, `recomp/docs/linux.md`, 2026-10-23, not repeated here): SteamOS 3.8 with Mesa 26.2 RADV. The game code was generated and compiled on the Deck in about 16 minutes. *Breath of the Wild* ran at about 40 fps on the Great Plateau with the 60 fps cap, and a steady 30 with the 30 fps cap. RADV needed `VK_KHR_maintenance5` (point lists without a point size hung the GPU). An imported host-memory heap cost about 4 ms per submission, because amdgpu revisits its pages, so the heap became exported GPU memory mapped at the heap's addresses.
- **Tears of the Kingdom** (H, nxnative's profiles): 1.4.3's `sdk` (`574284b40c06cf18…`) and `subsdk0` (`f39f4c431234c72e…`) are the builds *Breath of the Wild* 1.9.0 has, the version Savage Wind supports; `main` (`277178b7dba1b6d4…`) is its own. What nxnative's bring-up found it needs beyond *Breath of the Wild* is listed in [docs/RECOMP.md](docs/RECOMP.md).
- **Measured here** (H, x86-64 under emulation on the Mac, 2026-10-28): the vendored engine builds, and its game-free tests pass: 159,968 FP / SIMD cases against Unicorn, 2 million software-FMA cases, and 13,236 translated shaders compiled and validated (`sh devtools/check.sh deck`). No game has been recompiled or run here.
