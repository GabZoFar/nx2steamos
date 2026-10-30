#!/bin/bash
# make appimage ARCH=<x86_64|aarch64>: build/dist/Savage_Wind-<version>-<arch>.AppImage, built in Ubuntu 22.04
# containers (podman or Docker; tools/linux/Containerfile) so it runs on glibc 2.35+ distributions. Everything is built inside
# the amd64 container - aarch64 cross-compiled against the arm64 image's root filesystem (build/linux/aarch64/sysroot)
# - except the generator helpers, which PyInstaller freezes in a container of their own arch (arm64: QEMU, binfmt).
# No game code inside: the start screen builds it on the player's machine. See docs/linux.md.
set -euo pipefail
cd "$(dirname "$0")/../.."
ARCH=${1:?usage: tools/linux/appimage.sh x86_64|aarch64}
case $ARCH in x86_64) PODARCH=amd64 ;; aarch64) PODARCH=arm64 ;;
    *) echo "unknown arch $ARCH"; exit 1 ;; esac
if command -v podman >/dev/null 2>&1; then CONTAINER=podman
elif command -v docker >/dev/null 2>&1; then CONTAINER=docker
else echo "podman or docker is required"; exit 1
fi

image() {  # arch -> tag (built once, then cached by the container engine)
    if [ "$CONTAINER" = podman ]; then
        podman build -q --arch "$1" -t "localhost/savagewind-build:$1" -f tools/linux/Containerfile tools/linux >/dev/null
    else
        docker build -q --platform "linux/$1" -t "localhost/savagewind-build:$1" -f tools/linux/Containerfile tools/linux >/dev/null
    fi
    echo "localhost/savagewind-build:$1"
}
run() {  # image, command...: the checkout mounted at /src
    local img=$1; shift
    if [ "$CONTAINER" = podman ]; then
        podman run --rm --arch "${img##*:}" -v "$PWD:/src" -w /src -e SYSROOT=/src/build/linux/aarch64/sysroot "$img" "$@"
    else
        docker run --rm --platform "linux/${img##*:}" -v "$PWD:/src" -w /src -e SYSROOT=/src/build/linux/aarch64/sysroot "$img" "$@"
    fi
}

AMD=$(image amd64)
if [ "$ARCH" = aarch64 ]; then
    ARM=$(image arm64)
    S=build/linux/aarch64/sysroot
    if [ ! -f "$S/.ok" ]; then  # the arm64 image's files: headers and libraries to cross-compile against
        rm -rf "$S" && mkdir -p "$S"
        if [ "$CONTAINER" = podman ]; then c=$(podman create --arch arm64 "$ARM")
        else c=$(docker create --platform linux/arm64 "$ARM"); fi
        # (anchored: only the top-level pseudo filesystems, not e.g. usr/include/<triple>/sys)
        "$CONTAINER" export "$c" | tar -x -C "$S" --anchored --exclude='dev/*' --exclude='proc/*' --exclude='sys/*' 2>/dev/null || true
        "$CONTAINER" rm -f "$c" >/dev/null
        # absolute symlinks (libm.so -> /lib/aarch64-linux-gnu/libm.so.6) would point into this machine's root:
        # relative, so they stay inside the sysroot
        find "$S" -type l -lname '/*' | while read -r l; do
            ln -sfn "$(realpath -m --relative-to="$(dirname "$l")" "$S$(readlink "$l")")" "$l"
        done
        touch "$S/.ok"
    fi
    run "$ARM" tools/linux/gen_helper.sh aarch64
else
    run "$AMD" tools/linux/gen_helper.sh x86_64
fi
run "$AMD" tools/linux/package.sh "$ARCH"
