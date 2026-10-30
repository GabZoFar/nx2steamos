# The engine (`nxnative/`)

`nxnative/` is **nx2android's** copy of [seedds/nxnative](https://github.com/seedds/nxnative), not upstream's: nx2android changed about 40 of the shared engine files (host, NVN layer, rewriter, Ryujinx's translator) and added the Android runtime (`dm/android/`) whose Vulkan backend nx2steamos runs. Taking nx2android's tree brings both, and upstream's fixes reach nx2steamos through nx2android.

| | Commit |
|---|---|
| nx2android (`GabZoFar/nx2android`, `shirai91/nx2android`) | `584708c` (2026-10-28), its `nxnative/` folder as committed |
| seedds/nxnative inside it | `6034fca` (2026-10-25, nx2android's subtree add) |

It was copied with `git -C ../nx2android archive 584708c nxnative | tar -x` (committed files only).

## What nx2steamos changed in it

Kept small and behind platform conditions, so nx2android can take them back (they are general Linux support):

| File | Change |
|---|---|
| `dm/tools/patch_nso.py` | `DM_TARGET=linux`: the slot block at `tpidr_el0[1]` (glibc's `tcbhead_t.private`), `TP_SLOT` per target |
| `dm/host/loader.c`, `dm/host/dm.h` | `TLS_SLOT_APP` 2 on Android, 1 on glibc; the modules' target `linux` expected off Android |
| `dm/host/hle_sdk16.c` | the shared font from the system's fonts on Linux (Noto Sans, DejaVu, Liberation) |
| `dm/host/hle_hid.c`, `dm/host/hle_audio.c` | touch in both modes and real audio on every Linux host (`__linux__`), not Android alone |
| `dm/android/gfx_vk.h`, `gfx_vk_core.c`, `gfx_vk_present.c` | Android calls behind `__ANDROID__`; the Linux side calls `vkp_*` (`dm/steamos/vk_platform.c`): loader name, surface, window size, pools, chunk size; a UNORM swapchain format preferred everywhere; opaque alpha, extent clamped and SUBOPTIMAL handled on Linux |
| `dm/android/perf_android.c` | Android calls behind `__ANDROID__`; no 40 fps step on a display 40 does not divide |
| `dm/android/compat/darwin_compat.c`, `include/darwin_compat.h` | `pthread_gettid_np` for glibc, `getdtablesize` on Android only, `<stdbool.h>` |
| `dm/tools/tests/test_target_slots.py` | new: the slot encoding per target |

Everything else nx2steamos adds is new files: in `dm/steamos/`, and the game profiles' `deck.tsv` (`dm/games/<title ID>/deck.tsv`: a game's status on the Steam Deck engine, read by `porter/deck.py`; data, as `steamos.tsv`).

## Updating

To take nx2android's newer engine (which may carry a newer upstream):

```
git -C ../nx2android pull
git -C ../nx2android diff 584708c HEAD -- nxnative > /tmp/engine.diff   # what changed there since this copy
git apply /tmp/engine.diff                                                 # from this repository's root (the paths start with nxnative/)
```

then rebuild and run `sh devtools/check.sh`, and record the new commits above. When nx2steamos is a git repository with history, the same can be done with `git subtree` over a `git subtree split --prefix nxnative` of nx2android.
