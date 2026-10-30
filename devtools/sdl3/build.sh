#!/bin/sh
# Inside the devtools/sdl3 container: fetch SDL's release archive into an empty folder, check it, build the shared library, copy it to /out.
set -eu
SDL=${SDL:-3.4.18}
mkdir -p /work/dl && cd /work/dl
curl -fsSL -o SDL3.tar.gz "https://github.com/libsdl-org/SDL/releases/download/release-${SDL}/SDL3-${SDL}.tar.gz"
got=$(sha256sum SDL3.tar.gz | cut -d' ' -f1)
if [ -n "${SDL_SHA256:-}" ] && [ "$got" != "$SDL_SHA256" ]; then echo "SDL3-${SDL}.tar.gz: sha256 $got, expected $SDL_SHA256" >&2; exit 1; fi
tar -xzf SDL3.tar.gz -C /work
cmake -S "/work/SDL3-${SDL}" -B /work/build -G Ninja -DCMAKE_BUILD_TYPE=Release -DSDL_SHARED=ON -DSDL_STATIC=OFF -DSDL_TESTS=OFF -DSDL_EXAMPLES=OFF \
      -DSDL_INSTALL_DOCS=OFF -DSDL_WAYLAND=ON -DSDL_X11=ON -DSDL_PIPEWIRE=ON -DSDL_PULSEAUDIO=ON -DSDL_ALSA=ON -DSDL_HIDAPI=ON -DSDL_VULKAN=ON >/work/cmake.log
cmake --build /work/build >/work/build.log
lib=$(ls /work/build/libSDL3.so.0.* | head -1)
mkdir -p /out && cp "$lib" /out/libSDL3.so.0 && strip --strip-unneeded /out/libSDL3.so.0
cp "/work/SDL3-${SDL}/LICENSE.txt" /out/LICENSE.txt
glibc=$(objdump -T /out/libSDL3.so.0 | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -1)
printf 'SDL %s\nsource sha256 %s\nbuilt on Ubuntu 22.04 (%s), needs %s\nlibrary sha256 %s\n' "$SDL" "$got" "$(ldd --version | head -1)" "$glibc" "$(sha256sum /out/libSDL3.so.0 | cut -d' ' -f1)" > /out/BUILDINFO
cat /out/BUILDINFO
