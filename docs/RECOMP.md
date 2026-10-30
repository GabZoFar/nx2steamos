# The Steam Deck engine (`recomp/`)

The Steam Deck's CPU is x86-64 (AMD Zen 2), so the games' Arm code cannot run on it as it is; nxnative's approach (`nxnative/`, the Arm code rewritten and run directly) serves the ARM64 SteamOS devices only. For the Deck the code is **recompiled**: at port time each module of the game (`main`, `subsdk*`, `sdk`) is translated to C and compiled for x86-64, once, on the player's machine, from the player's own files. Nothing is translated or interpreted while the game runs.

`recomp/` is **[Savage Wind Recomp](https://github.com/Revivedx/SavageWindRecomp)** (MIT, Revivedx and the Savage Wind Recomp contributors), the static recompilation of *Breath of the Wild* 1.9.0, itself built on **[LAS Recomp](https://github.com/peiikai/LASRecomp)** (MIT, Peiikai) beta 3. Both say they are written almost entirely with Claude Code; nx2steamos takes Savage Wind's tree as it is, the way it takes nx2android's `nxnative/`.

| | Commit |
|---|---|
| Savage Wind Recomp (`Revivedx/SavageWindRecomp`, the user's checkout `../SavageWindRecomp`) | `860f372` (2026-10-27, "Merge pull request #230") |

It was copied with `git -C ../SavageWindRecomp archive 860f372 | tar -x -C recomp` (committed files only), without `.github/`. Its own notes (in Spanish) are `recomp/docs/SAVAGE-WIND.md`; its Linux and Steam Deck notes `recomp/docs/linux.md`.

## How it works

| Part | Where | What |
|---|---|---|
| Generator | `recomp/tools/gen.py`, `lifter.py`, `simd_c.py`, `analyze.py` | capstone decodes each module; functions come from the `.eh_frame` FDEs, calls, relocations (`DT_RELR` too) and resolved jump tables; each function becomes a C function on a `Cpu` (`runtime/cpu.h`). FP / SIMD is lowered to portable C (`--simd c`, `runtime/simd*.h`) on x86-64 and to inline asm on aarch64 |
| Layout | `recomp/tools/layout.py` | the modules back to back from `0x70_0000_0000`, as Horizon's loader would place them; the generated code has these addresses in it |
| Game code library | `make gen`, then `make` (or the start screen's own build with a bundled Zig) | `swgame.so`: the generated C, compiled `-O1 -march=x86-64-v3` (AVX2 + FMA, both on the Deck's Zen 2; `-v2` for older CPUs) |
| Runtime | `recomp/runtime/` | the Horizon kernel (threads, synchronisation, memory regions in a 12.5 GB guest window), IPC and the services the SDK talks to (`services/`), the SDK's own code running recompiled (its audio renderer too), NVN taken over at the API level and drawn with Vulkan (`nvn/vulkan/`), a Maxwell shader translator to GLSL / SPIR-V (`nvn/maxwell/translate.c`), SDL3 for the window, input and sound, a start screen (imgui) |
| Game knowledge | `recomp/tools/game_sites.py`, `patches.py`, `runtime/game_versions.h` | Breath of the Wild's: per-version sites in `main` found by masked patterns, patches for its Options screen and frame pacing |

Guest addresses are host addresses (one mapping of the guest window), as in nxnative; the two engines share nothing else. nxnative takes over the SDK's `nn::` functions by name and replays NVN; Savage Wind runs the SDK itself and answers its system calls and IPC.

## On the Steam Deck

Savage Wind ran on a Steam Deck on 2026-10-23 (`recomp/docs/linux.md`, its measurements, not nx2steamos's): SteamOS 3.8, Mesa 26.2 RADV (VANGOGH); the game code generated and compiled on the Deck in about 16 minutes on its 8 threads, the shaders precompiled in about 7; about 40 fps standing on the Great Plateau with the 60 fps cap, a steady 30 with the 30 fps cap. Two Deck fixes are in this copy: `VK_KHR_maintenance5` for point lists without a point size (a GPU hang), and the guest heap as GPU memory exported as a dma-buf and mapped at the heap's addresses (amdgpu revisited every page of an imported host-memory heap at each submission).

## The porter drives it

`python -m porter port <folder> --engine recomp [--install]` (`porter config engine recomp` makes it the default), in `porter/deck.py`:

| Stage | What | Output (in `<game folder>/<title ID>_build/`) |
|---|---|---|
| inputs | the game must have a `deck.tsv` beside its `game.tsv` (`nxnative/dm/games/<title ID>/`: `status`, `tested`); the newest update in the folder, refused at once when its profile registers it as a version the engine does not run; DLC left out (the runtime's `aoc:u` reports none) | |
| extract | nx_extract with the player's keys, the ARM64 port's extraction (shared, reused) | `extract/<version>/{ExeFS,RomFS,control.nacp,icon.jpg}` |
| version | the start screen's own checks (`runtime/ui/launcher.mm`): `main`'s build ID in `runtime/game_versions.h` and supported, the RomFS's version file or game marker, at least 95% of its files | |
| game code | `gen.py layout`, then `gen.py` on every module at once (`--simd c`, `PYTHONHASHSEED=0` as Savage Wind's frozen generator), each chunk compiled by `zig cc -target x86_64-linux-gnu.2.35` as soon as it is written, then linked `-shared` (the start screen's steps, `runtime/gamebuild.c`); the C is deleted as it is compiled; reused while the generator, the modules and Zig are unchanged | `deck/swgame.so`, `deck/swgame.so.id` |
| art, game folder | Steam's images from the icon; the folder below | `<out>/<Stem>/` |
| install | over SSH or locally: the device checked (x86-64, glibc 2.35, Vulkan; AVX2 / FMA / BMI2 and FUSE reported), the folder copied, then the data image built on the device by the folder's own launcher (`nx2steamos-run --nx2s-prepare`: the runtime's `--build-romfs`), then Steam's shortcut | `~/Games/nx2steamos/<Stem>` |

The game folder (`porter/gamedir.py` `build_deck`):

```
<Stem>/                         e.g. ~/Games/nx2steamos/ZeldaBreathOfTheWild/
  nx2steamos-run                the launcher: SAVAGEWIND_DATA_ROOT=~/.local/share/nx2steamos/<title ID>, then bin/<AppImage> <folder>/game [Steam's launch options]
  bin/Savage_Wind-…-x86_64.AppImage   the runtime built from this recomp/ checkout
  game/exefs/  game/romfs/      the game's files, as extracted (the runtime takes a game folder as its first argument: runtime/main.c)
  game/swgame.so (+ .id)        the game code, recompiled for x86-64-v3
  game/romfs.bin (+ .key)       the data image the game reads (runtime/services/fs.c), built on the device from game/romfs/ and kept until the RomFS changes
  art/  nx2s-game.json          as the ARM64 engine's ("engine": "recomp")
```

The game's saves, settings, shader caches and log (`savagewind.log`) are the runtime's data root, `~/.local/share/nx2steamos/<title ID>/`, outside the folder. Without FUSE the launcher has the AppImage unpack itself at each start (`APPIMAGE_EXTRACT_AND_RUN`). The launch option `--config` opens Savage Wind's configuration screen (its settings: language, 30 / 60 fps cap, graphics); its shader precompilation is started there too.

**Game code built from this checkout needs a runtime built from this `recomp/`.** The runtime loads a game code library only when the library's id is its own: `GAMELIB_ID`, a hash of the generator, the headers the game code is compiled against and the compile flags (`recomp/Makefile`). `porter/deck.py` `gamelib_id()` computes it as the Makefile does (the flags as make expands them, whitespace included) and builds the library with it. The older Savage Wind release remains recorded in `RUNTIMES` with its published SHA-256; its id no longer matches this checkout after the generator fix. It also requires Breath of the Wild's game sites:

| Savage Wind release | Built from | Its `GAMELIB_ID` (x86-64-v3, x86-64-v2), read from its `savagewind` | Since, in `860f372` |
|---|---|---|---|
| 0.3.0-alpha.3, `Savage_Wind-0.3.0-alpha.3-x86_64.AppImage` (85,215,736 bytes, SHA-256 `addf7010…f491ac`) | `5173d27` | `f0cd698371c0a703`, `ff06ab652b335fb4`: prior to the local continuation fix | `runtime/nvn/vulkan/device.c` (AMD's and Intel's Windows drivers get Alpha 2's Vulkan setup; RADV's path unchanged), the start screen's version line, docs |

Checked on 2026-10-29 without game files: the game code the porter builds from recomp/'s made-up modules (`tools/test_layout.py`) loads in that release's runtime and in one built from `recomp/` (both stop where they should, at the game sites made-up modules lack: "the game code has no address for opt_table"), and is refused with another id ("built for another version"). A change to `recomp/`'s generator or headers changes the id: the release no longer pairs (`tests/test_deck.py` says so), and the runtime must be built from `recomp/` (`make appimage`) and given with `porter config savagewind <AppImage>`.

**Not verified yet:** a real game through it on the Deck. TotK 1.4.3's extracted modules have been recompiled to a 421 MiB x86-64 game library without game sites. On 2026-10-30 the Deck launched it from an nx2steamos folder on the SD card: the runtime loaded the game code, indexed RomFS, initialized NVN, then crashed before the first frame in the SDK's virtual-address manager. The runtime had answered `GetInfo(TotalSystemResourceSize)` with zero despite the game's NPDM declaring 24 MB; the fix reads that field from the NPDM. A frame remains to be verified. Neither game has launched from an nx2steamos folder in Steam Game Mode ([ROADMAP.md](ROADMAP.md), D1).

## Games

| Game | Status in `recomp/` |
|---|---|
| The Legend of Zelda: Breath of the Wild 1.9.0 (`01007EF00011E000`) | Savage Wind's own game: the opening and the Great Plateau played, by its testers |
| The Legend of Zelda: Tears of the Kingdom 1.4.3 (`0100F2C0115B6000`) | Runs on a Steam Deck (2026-10-30): title screen, Continue / New Game, the opening's gameplay at a steady 30 fps with sound; the 3D picture is not right yet (objects unlit) |

Tears of the Kingdom 1.4.3 is the closest game to what Savage Wind already runs: its `sdk` (`574284b40c06cf18…`) and `subsdk0` (`f39f4c431234c72e…`, the movie module) are the same builds as Breath of the Wild 1.9.0's (nxnative's profiles, `nxnative/dm/games/0100F2C0115B6000/game.tsv` and `01007EF00011E000/game.tsv`); only `main` (`277178b7dba1b6d4…`) differs. What nxnative's bring-up of the same build found it needs beyond Breath of the Wild (`nxnative/docs/development.md`, `nxnative/docs/agents/03-bringup.md`), the starting list for this engine:

- **Memory:** a 24 MB system resource, so virtual address memory: the game's resource heaps are address regions in the alias region (64 GB on the Switch; 4 GB in Savage Wind's window), after the SDK's ~1 GB node area, and it calls `SetHeapSize(0x86000000)`. nxnative cut the node area to 62 MB to fit a phone; on Linux the guest window can grow instead.
- **SDK 20 calls that reach a service:** `oe::GetPreviousProgramIndex`, the hardware Opus decoder's C interface, cache storage, `fs::IsSaveDataExisting` for another title (Breath of the Wild's save) and `MountSaveDataReadOnly`, `time::ClockSnapshot::CreateWithStandardSystemClock`.
- **Graphics:** conditional rendering (`render_cond`: without it every frame was black), the transform feedback layout from the program's control section, block heights for textures without a packaged layout.
- **60 fps:** the frame step at FPSLocker's sites for this build (`nxnative/dm/games/0100F2C0115B6000/patches.py`, `FRAME_STEP`).
- **Modules:** `main` imports `nn::ro` (NRO modules at run time).

What its bring-up on a Steam Deck (SteamOS 3.9, RADV) needed in the runtime, in the order it came up:

- **Memory:** it maps its virtual address memory 128 KB at a time (`MapPhysicalMemory`): the region table merges adjacent physical memory, maps (and zeroes) only the gaps of a range, and unmaps by range (`runtime/kernel.c`).
- **Cache storage:** `fsp-srv` 60 / 61 / 62 / 68 return an `ISaveDataInfoReader` (`runtime/services/fs.c`); without one the game read a null interface.
- **Audio:** the hardware Opus decoder (`hwopus`, `runtime/services/opus.c`) decodes with the host's libopus, opened at run time (SteamOS has it); its light limiter, enabled after the first frame that carried it, is set up when first applied enabled (`runtime/services/audio.c`: the ADSP read its never-set-up state at address 0 harmlessly, the CPU crashed).
- **Compute:** its compute programs carry a 0x100-byte header (magic `0x12345679`); from the blob, every one of them hashed the same and they shared one translation, which wrote garbage over the heap (`runtime/nvn/maxwell/translate.c` `code_offset`).
- **Shader instructions:** RED (global atomic reductions: tiled lighting's depth ranges), VOTE, VMNMX and LEA.HI, after Ryujinx's decoders and emitters (MIT).
- **Conditional rendering:** `SetRenderEnableConditional` / `SetRenderEnable` (`runtime/nvn/cmdbuf.c`, `exec.c`), as nxnative found it needs.
- **ASTC:** its textures hold all-zero (illegal) blocks, whole mip chains of its texture arrays among them; they decode to transparent black, not the spec's magenta, which had tinted the light probes and the whole scene (`runtime/nvn/astc.c`).

Still wrong: lit surfaces of objects come out black (the cave walls are lit). Next: the lighting passes, starting with nxnative's TotK notes (`nxnative/docs/agents/04-graphics.md`: the stencil-mode texture view, scaled depth copies).

TotK's `deck.tsv` selects a site-free build. The runtime version table knows its main build ID and RomFS marker; its own game-code setup passes `--no-sites` for this version. The runtime accepts a library with no sites and disables Breath of the Wild's site-dependent frame pacing, version patch, and frame-loop hook when those sites are absent. The porter requires a custom AppImage built from this `recomp/` for TotK. The generator's local-continuation fix also requires rebuilding TotK's game library with the matching game-code ID. This only opens the bring-up path; the memory, service, graphics, and module work above remains.

## What nx2steamos changed in it

The changes from Savage Wind's vendored commit are limited to `runtime/game_versions.h`, `runtime/ui/launcher.mm`, `runtime/gamebuild.c`, `runtime/gamelib.c`, `runtime/hle/framepace.c`, `runtime/nvn/objects.c`, `runtime/services/fs.c`, `runtime/main.c`, `runtime/kernel.c`, `runtime/rt.h`, `runtime/platform/linux.c`, `tools/gen.py`, `tools/test_game_sites.py`, and `tools/linux/{Containerfile,appimage.sh,package.sh}`: TotK's version and marker, no-site generation/loading, guards around Breath of the Wild sites, NPDM system-resource reporting, a fatal-dialog suppression option for SSH tests, recognition of `ADR`/`BR` local continuations in generated code, test coverage, and Docker as an alternative AppImage builder on the Mac. TotK's bring-up on the Deck ("Games" above) added `runtime/services/opus.c` and changed `runtime/kernel.c` (physical memory), `runtime/services/fs.c` (save data info readers), `runtime/services/audio.c` (light limiter set-up), `runtime/nvn/maxwell/translate.c` (compute header, RED / VOTE / VMNMX / LEA.HI, the ballot extension in the GLSL prelude), `runtime/nvn/cmdbuf.c` and `runtime/nvn/vulkan/exec.c` (conditional rendering), `runtime/nvn/astc.{c,h}` and `tools/test_astc.c` (error blocks transparent black). None of these is in the game code id (`GAMELIB_SRCS`). The packaging script extracts `appimagetool` when Docker's Rosetta cannot execute its static PIE launcher. The generator change changes the game-code ID: both the TotK library and this custom AppImage must be rebuilt together. The amd64 dev container is nx2steamos's own (`devtools/container`, built with `--platform linux/amd64`); what drives the engine is the porter's (`porter/deck.py`, `porter/gamedir.py` `build_deck`).

## Checks

In the amd64 dev container (on an Apple-silicon Mac, Docker Desktop's x86-64 emulation), with no game files:

```
docker build --platform linux/amd64 -t nx2steamos-dev:amd64 devtools/container
docker run --rm --platform linux/amd64 -v "$PWD/recomp":/src -w /src nx2steamos-dev:amd64 make -j12
docker run --rm --platform linux/amd64 -v "$PWD/recomp":/src -w /src nx2steamos-dev:amd64 \
    sh -c 'python3 tools/test_layout.py && python3 tools/test_game_sites.py && python3 tools/test_simd.py --jobs 12'
```

`make` builds the runtime and the start screen (no game code: that needs `make gen` on a game's files). The tests: the module layout on made-up NSOs (`test_layout.py`), the site patterns on made-up code (`test_game_sites.py`), the portable FP / SIMD lowering against Unicorn's Cortex-A57 (`test_simd.py`), the software FMA bit for bit (`test_softfma.c`) and the shader translator's GLSL through shaderc and SPIRV-Tools (`test_translate.sh`). `test_lift.py` needs a module to lift.

Measured on 2026-10-28, x86-64 under emulation on the Mac (Debian 13, clang 19):

| Check | Result |
|---|---|
| `make -j12` | the runtime and start screen, `build/savagewind` (10 MB), in 18 s |
| `test_layout.py` | ok, 59 checks |
| `test_game_sites.py` | ok, 35 checks |
| `test_simd.py --jobs 12` | 20,000 encodings, 159,968 cases: 0 failing (122 s) |
| `test_softfma` | 2,000,000 cases (`fma` and `fmaf`, every rounding mode, FTZ / DAZ): 0 failing |
| `test_translate` (20,000 programs, 12 jobs) | 13,236 translated, compiled and validated, 0 compile failures, 0 validation failures; 6,768 random programs refused by the translator |

`test_translate.sh` links the MSYS2 library names (`-lshaderc_shared -lSPIRV-Tools-shared`); on Debian the same test links `-lshaderc -lSPIRV-Tools` (`devtools/check.sh` does so).

Then the porter's part (`devtools/deck_porter_check.sh`, the last step of `sh devtools/check.sh deck`): `porter/deck.py`'s game code id against make's, and the game code it builds from the made-up modules loaded by `build/savagewind` (measured 2026-10-29: id `f0cd698371c0a703` / `ff06ab652b335fb4` as make's, the library built with Zig 0.17.0 in 10 s, loaded). `tests/test_deck.py` runs the same build in the arm64 container (`zig cc` cross-compiling for x86_64-linux-gnu, as on a player's Mac).

## Updating

```
git -C ../SavageWindRecomp pull
git -C ../SavageWindRecomp diff 860f372 HEAD -- . ':!.github' > /tmp/recomp.diff
git apply --directory=recomp /tmp/recomp.diff
```

then rebuild, run the checks above, and record the new commit here.
