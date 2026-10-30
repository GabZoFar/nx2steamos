# The dev container, for both kinds of SteamOS machine: a glibc Linux like SteamOS's, on any machine with Docker.
#   arm64 (ARM devices, nxnative's engine):       docker build -t nx2steamos-dev devtools/container
#   amd64 (the Steam Deck, the recompiler engine): docker build --platform linux/amd64 -t nx2steamos-dev:amd64 devtools/container
# On an Apple-silicon Mac the amd64 image runs under Docker Desktop's x86-64 emulation (Rosetta): slower, but a real x86-64 Linux with AVX2.
# It builds the hosts with the system clang or with Zig (the player's toolchain), runs the unit tests, and runs Vulkan code on lavapipe (Mesa's CPU driver).
#   docker run --rm -v "$PWD":/src -w /src nx2steamos-dev python3 nxnative/dm/steamos/build.py --game <title ID> --cc zig
# The device is not emulated: no Adreno / Turnip, no Van Gogh / RADV, no gamescope. probes/ say what the device itself does.
FROM debian:trixie
ARG TARGETARCH
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    clang lld llvm make python3 python3-venv python3-lz4 python3-capstone python3-pil python3-yaml git ca-certificates curl xz-utils file gdb strace binutils \
    libsdl3-dev libvulkan-dev mesa-vulkan-drivers vulkan-tools glslang-tools spirv-tools libavcodec-dev libavutil-dev libavformat-dev libswscale-dev \
    libshaderc-dev glslc zlib1g-dev pkg-config xvfb xauth \
 && rm -rf /var/lib/apt/lists/*
# The recompiler's own checks (recomp/tools/test_*.py) run instructions through Unicorn: a venv over the system's Python modules, first on the PATH
RUN python3 -m venv --system-site-packages /opt/venv && /opt/venv/bin/pip install -q unicorn==2.1.4
ENV PATH=/opt/venv/bin:$PATH
# Zig, the toolchain porter/fetch.py gives players: one archive, checked against the checksum in ziglang.org's index
ARG ZIG=0.17.0
RUN case "$TARGETARCH" in \
      amd64) Z=x86_64; S=1cbe9df9f27e6b78d14ccbca43b6703a404ef79ef1c463de901d7f088d4e2026 ;; \
      arm64) Z=aarch64; S=9e8d11661d4ae3bd57702a3832781e23ad151dde5798e16a5ccd503f65234ff8 ;; \
      *) echo "no Zig for $TARGETARCH"; exit 1 ;; esac \
 && curl -fsSL -o /tmp/zig.tar.xz https://ziglang.org/download/${ZIG}/zig-$Z-linux-${ZIG}.tar.xz \
 && echo "$S  /tmp/zig.tar.xz" | sha256sum -c - \
 && mkdir -p /opt/zig && tar -xJf /tmp/zig.tar.xz -C /opt/zig --strip-components=1 && rm /tmp/zig.tar.xz \
 && ln -s /opt/zig/zig /usr/local/bin/zig
# lavapipe as the only Vulkan driver, without the loader's search noise
ENV VK_DRIVER_FILES=/usr/share/vulkan/icd.d/lvp_icd.json
