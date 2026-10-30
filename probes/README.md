# Device probes

Small programs that answer, on a real SteamOS device, the questions the runtime's design depends on. They need no game files. nx2android's `probes/` did the same for Android; `vkprobe.c` is its probe with Linux's loader name and the present-timing extensions added.

| Probe | Answers |
|---|---|
| `tlsprobe.c` | glibc leaves `tpidr_el0[1]` alone (where `DM_TARGET=linux` keeps each guest thread's slot block), and the executable's TLS starts at tp+16 |
| `vkprobe.c` | Turnip's identity, features, limits, extensions (BC textures, descriptor indexing, transform feedback, placed maps, dma-buf, present timing), formats and memory types |
| `vkzc.c` | which way makes a guest memory pool GPU memory with zero copy: `placed` (VK_EXT_map_memory_placed), `dmabuf` (an exported dma-buf mapped MAP_FIXED), `opaque`, `host` (host-pointer import), `remap`; and whether the mapping is CPU-cached. Each method runs in a process of its own |

## On the device

Build them anywhere with Zig, copy them over, run them (the device needs nothing installed):

```
mkdir -p out; Z=$(python -m porter config zig); I="-I../nxnative/dm/steamos/compat/include -I../nxnative/dm/third_party/vulkan-headers/include"
for p in tlsprobe vkprobe vkzc; do $Z cc -target aarch64-linux-gnu.2.34 -O1 $I -o out/$p $p.c -lpthread; done
scp out/* deck@steamos.local:/tmp/ && ssh deck@steamos.local 'cd /tmp && ./tlsprobe && ./vkprobe && ./vkzc'
```

(or on the device itself with any C compiler: `cc -O1 vkzc.c -o vkzc -ldl`). Keep the output: it goes into the research notes and decides `DM_VK_POOLS`.

## In the dev container

`sh devtools/check.sh` builds and runs `tlsprobe` and `vkzc` on lavapipe (Mesa's CPU driver): that checks the probes themselves, not a device.
