# Dev tools

| Tool | What |
|---|---|
| `container/Dockerfile` | The dev container (Debian 13: glibc 2.41, clang 19, Mesa's lavapipe, SDL3, FFmpeg, shaderc, glslang, Xvfb, Zig 0.17, Unicorn), for both engines: `nx2steamos-dev` (arm64, a glibc aarch64 Linux like SteamOS on ARM) and `nx2steamos-dev:amd64` (`--platform linux/amd64`, x86-64 like the Steam Deck; under emulation on an Apple-silicon Mac). It is not the device: no Adreno / Turnip, no Van Gogh / RADV, no gamescope |
| `check.sh [arm \| deck] [<title ID>]` | Every check that needs no device or game: on arm64, the host built with clang and with Zig, the host's unit tests on glibc, the rewriter's and the porter's tests, movies through FFmpeg, the probes on lavapipe; on amd64, the Steam Deck engine (`recomp/`) built and its game-free tests ([docs/RECOMP.md](../docs/RECOMP.md)), then the porter's game code for it loaded in that runtime (`deck_porter_check.sh`) |
| `deck_bench.py` | The benchmark of a Steam Deck port on the device, over SSH: a fixed scene from a reference save, frame rate, the game's frame time, its threads' CPU time and instructions a cycle, as a row of [docs/BENCHMARKS.md](../docs/BENCHMARKS.md) |
| `build_sdl3.sh`, `sdl3/` | `dist/sdl3/libSDL3.so.0`: SDL 3.4 built on Ubuntu 22.04 (glibc 2.35), so it runs on every SteamOS ARM image (glibc 2.39); the porter bundles it in each game folder |

A windowed run without a device, in the container (the host stops where it needs a game's modules unless the folder has them):

```
docker run --rm -v "$PWD":/src nx2steamos-dev sh -c 'Xvfb :99 & sleep 1; mkdir -p /tmp/g/bin /tmp/g/lib;
  cp /src/nxnative/dm/steamos/out/<title ID>/nx2s-host /tmp/g/bin/; cp /src/dist/sdl3/libSDL3.so.0 /tmp/g/lib/;
  DISPLAY=:99 SDL_AUDIO_DRIVER=dummy /tmp/g/bin/nx2s-host; cat ~/.local/state/nx2steamos/<title ID>/log.txt'
```
