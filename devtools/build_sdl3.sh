#!/bin/sh
# Builds dist/sdl3/libSDL3.so.0 (SDL 3 for SteamOS on arm64) in the devtools/sdl3 container, on any arm64 machine with Docker (an Apple-silicon Mac, an ARM Linux box).
set -eu
here=$(cd "$(dirname "$0")" && pwd); repo=$(dirname "$here")
docker build -q -t nx2steamos-sdl3 "$here/sdl3" >/dev/null
mkdir -p "$repo/dist/sdl3"
docker run --rm -e SDL="${SDL:-3.4.18}" -e SDL_SHA256="${SDL_SHA256:-}" -v "$repo/dist/sdl3":/out nx2steamos-sdl3
