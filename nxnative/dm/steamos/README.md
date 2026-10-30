# The SteamOS runtime (nx2steamos)

The host's platform for ARM64 SteamOS: what `dm/android/` is to Android, on glibc, SDL3 and Mesa. The engine's host (`dm/host`), NVN layer (`dm/gfx`) and the Android port's Vulkan backend (`dm/android/gfx_vk_*.c`, `perf_android.c`, `compat/`) are compiled with these files by `build.py` into one executable, `nx2s-host`, per game profile.

| File | What |
|---|---|
| `front_sdl.c` | The front end (`dm/host/front.h`): `main`, the game and state folders, the log, the host on a 16 MB-stack thread, SDL's window and events, controllers by position, touch, the Desktop Mode keyboard, the pause menu, the software keyboard's text entry, holding the game when hidden |
| `sdl_dyn.c`, `sdl_dyn.h` | SDL3 opened at run time: `<game folder>/lib/libSDL3.so.0`, then the system's; the SDL functions the host calls, by their own names |
| `vk_platform.c` | The Vulkan backend's Linux platform (`vkp_*` in `gfx_vk.h`): SDL's surface (made on the main thread), the window size, the device extensions the pools need, guest memory pools as exported dma-bufs or placed maps (`DM_VK_POOLS`) |
| `audio_sdl.c` | The AudioUnit subset the host uses (`dm/android/compat/include/AudioToolbox`) on an SDL3 audio stream |
| `av_dyn.c`, `av_dyn.h` | FFmpeg's libavcodec (58 to 62) opened at run time; only ABI-stable fields are touched |
| `movie_av.c`, `audioconv_av.c` | H.264 (`hle_movie_avc.h`) and AAC (AudioConverter) for the SDK's movie library |
| `haptics_sdl.c` | Rumble: the game's vibration for player 1 to `SDL_RumbleGamepad` |
| `build.py` | The build: `--cc zig` (any machine, `aarch64-linux-gnu.2.38`) or `--cc clang` (an aarch64 Linux machine); `--release` |
| `tools/steamos_profile.py` | A profile's `steamos.tsv` (or its `android.tsv`, as untested) and `steamos_profile.h` |
| `tools/gen_shaders.py`, `gen/` | The overlay's shaders (`dm/android/shaders`) compiled once to committed SPIR-V headers (Zig has no shader compiler) |
| `tools/host_tests.py`, `tools/host_tests_shim.c` | The host's unit tests (`dm/host/tests`) built and run on glibc |
| `tools/media_test.sh`, `tools/media_test.c` | `movie_av.c` and `audioconv_av.c` against a real FFmpeg: H.264 with B-frames compared to ffmpeg's decode, AAC |
| `compat/include/vulkan/vulkan.h` | `<vulkan/vulkan.h>` over the vendored `vulkan_core.h` |
| `third_party/SDL3` | SDL 3.2.30's headers (zlib) |

## Switches

Besides the engine's (`DM_*`, the engine's docs), set in the game folder's `env.txt`, the player's `~/.local/share/nx2steamos/<title ID>/env.txt`, or the environment (Steam's launch options: `DM_HEADLESS=1 %command%`):

| Switch | Effect |
|---|---|
| `DM_HEADLESS=1` | no window, input or sound (the default without `WAYLAND_DISPLAY` / `DISPLAY`) |
| `DM_FULLSCREEN=0/1` | a window or fullscreen (default: fullscreen under gamescope) |
| `DM_LOG_FILE=<path>` | the log there (default: `~/.local/state/nx2steamos/<title ID>/log.txt`, or the terminal) |
| `DM_VK_POOLS=auto/dmabuf/placed/copy` | how guest memory pools become GPU memory |
| `DM_VK_ZC_CHUNK_MB=<n>` | the largest zero-copy allocation (default 1024) |
| `DM_MOVIE_THREADS=<n>` | FFmpeg's H.264 threads (default 1: no frame delay) |
