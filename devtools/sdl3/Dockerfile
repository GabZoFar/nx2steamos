# libSDL3.so.0 for SteamOS on arm64, built against an older glibc (Ubuntu 22.04: 2.35) so it runs on every SteamOS ARM image (glibc 2.39) and newer.
# SteamOS's base image has SDL2 only; the host opens <game folder>/lib/libSDL3.so.0 first (nxnative/dm/steamos/sdl_dyn.c), and porter/gamedir.py puts this one there.
#   devtools/build_sdl3.sh  →  dist/sdl3/libSDL3.so.0 (+ LICENSE.txt, BUILDINFO)
# SDL loads its window system, audio and input libraries at run time (Wayland, X11, PipeWire, PulseAudio, ALSA, udev, D-Bus), so it needs only their headers here.
FROM ubuntu:22.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential cmake ninja-build pkg-config ca-certificates curl python3 \
    libwayland-dev wayland-protocols libxkbcommon-dev libegl1-mesa-dev libx11-dev libxext-dev libxrandr-dev libxcursor-dev libxi-dev libxfixes-dev \
    libxss-dev libxtst-dev libxinerama-dev libpipewire-0.3-dev libpulse-dev libasound2-dev libdecor-0-dev libdrm-dev libgbm-dev libudev-dev libdbus-1-dev \
 && rm -rf /var/lib/apt/lists/*
ARG SDL=3.4.18
ARG SDL_SHA256=
COPY build.sh /build.sh
CMD ["sh", "/build.sh"]
