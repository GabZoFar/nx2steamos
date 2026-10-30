# Benchmarks

How fast a port runs on a device, measured the same way every time, so that builds can be compared and the numbers shared (the [README](../README.md)'s come from here). A row is what `devtools/deck_bench.py` prints; add it to the table with what changed.

## Tears of the Kingdom on a Steam Deck

**The scene.** The caves beneath Hyrule Castle at the start of the game: the benchmark loads its reference save with Continue and measures 30 seconds of play without input, standing where the save left Link, from 90 seconds after the start. The reference is a copy of a player's save and shader caches (`--set-reference`); each run gets its save back from it, in a data folder of its own (`~/.local/share/nx2steamos-bench`), so the player's own save is never written and later play does not move the scene.

**The device.** Steam Deck LCD (AMD Custom APU 0405: 4 Zen 2 cores, 8 threads; RADV VANGOGH), SteamOS 3.9.5, Desktop Mode, the game in a 1280x720 window, rendering as handheld (`SAVAGEWIND_HANDHELD=1`, its `deck.tsv`), VSync mailbox, the device's power settings as they were (leave the performance overlay's TDP and clock limits off for comparable numbers). With SteamOS's CPU governor, `powersave` (energy preference `balance_performance`), the CPU stays at its base 2.4 GHz in this scene, though the APU draws 3-10 W of its 15 W and runs at under 60 °C; the rows say when another governor was set (`steamosctl set-cpu-scaling-governor`).

**The modes.**

- *As played*: the frame rate the player gets. The game runs at 60 only once its frames have kept under 14 ms for 120 frames in a row, else at 30 (`recomp/runtime/hle/frame_timing.c`).
- *Forced 60* (`SAVAGEWIND_FORCE_60=1`): the game at 60 whatever its frames take, so the frame rate it reaches is what the game code and the runtime sustain (up to the display's 60; where they cannot keep up, the game's time runs slow). The number to compare builds by.

**The columns.**

| Column | What |
|---|---|
| FPS | Frames presented per second over the window (the runtime's `[vk]` lines) |
| Game frame | The time the game measures for its own frame's work, average / longest, ms (its frame loop's end-of-frame callback, this + 0x120; `SAVAGEWIND_LOG_FRAME_TIMING`). 60 fps needs it under 16.7; the runtime lets the game run at 60 as played under 14 |
| Main thread | CPU time of the game's main thread, ms a frame (`SAVAGEWIND_PROF_THREADS`) |
| Game threads | CPU time of all its threads together, ms a frame |
| IPC | Instructions a cycle of the main thread / of all game threads (`perf stat`) |
| Queue thread | CPU time of the thread that turns the game's GPU commands into Vulkan, ms a frame |

**Running it.** From the dev machine, with the Deck in Desktop Mode, its screen on and nothing else running (each run takes the screen for about two minutes):

```
python3 devtools/deck_bench.py --device deck@steamdeck.local --set-reference    # once: the player's save becomes the scene
python3 devtools/deck_bench.py --device deck@steamdeck.local                    # as played
python3 devtools/deck_bench.py --device deck@steamdeck.local --force60 --runs 2  # forced 60
```

The game folder installed on the Deck is what runs: its runtime (`bin/`) and game code (`game/swgame.so`); the label defaults to this tree's commit.

### Results

| Date | Build | Game code | Mode | FPS | Game frame (ms) | Main thread (ms) | Game threads (ms) | IPC (main / all) | Queue thread (ms) |
|---|---|---|---|---|---|---|---|---|---|
| 2026-10-30 | 6bc8451 | `017929e9016f99f4` | as played | 30.0 | 18.9 / 21.9 | 5.4 | 50.2 | 0.85 / 0.96 | 9.7 |
| 2026-10-30 | 6bc8451 | `017929e9016f99f4` | forced 60 | 57.0 | 16.5 / 20.0 | 4.8 | 41.5 | 0.74 / 0.82 | 8.2 |
| 2026-10-30 | 6bc8451, governor `performance` | `017929e9016f99f4` | forced 60 | 57.8 | 15.1 / 19.3 | 4.6 | 38.5 | 0.67 / 0.76 | 7.6 |
| 2026-10-30 | registers in C locals (tried, not kept) | `79e1d77f7473ff9f` | as played | 30.0 | 18.9 / 21.1 | 5.3 | 50.4 | 0.84 / 0.92 | 9.8 |
| 2026-10-30 | registers in C locals (tried, not kept) | `79e1d77f7473ff9f` | forced 60 | 56.9 | 16.5 / 21.1 | 4.8 | 41.9 | 0.73 / 0.79 | 8.3 |

The forced 60 rows are averages of two runs (56.9 and 57.0 fps for the first), but for the governor's (one run). The game does less per frame at 60 than at 30 (its threads 41.5 ms of CPU a frame against 50.2): at 57 fps it is a few frames short of a steady 60 in this scene, and its frames stay over the 14 ms the runtime waits for, so as played it stays at 30.

- *Governor `performance`*: the CPU at 3.3-3.5 GHz instead of 2.4 (a run, set back to `powersave` after it). 45% more clock took 8.5% off the game's frame (16.5 to 15.1 ms) and its instructions a cycle fell: its threads mostly wait for memory. The frame rate hardly moved: what holds it under 60 is its longest frames (19-23 ms), not their average.
- *Registers in C locals*: the generator kept the guest registers in C locals within each function, with a dataflow deciding which to store back at calls (correct against Unicorn on thousands of random programs: `recomp/tools/test_gen_functions.py`). No change at all: the round trips through the `Cpu` struct it removed are not where the game threads' time goes ([RECOMP.md](RECOMP.md) "Where a frame's time goes"). Not kept.

### For the README

> On a Steam Deck (LCD), in the caves at the start of the game: a steady 30 fps as played; 57 fps with the frame rate forced to 60 (`SAVAGEWIND_FORCE_60=1`).
