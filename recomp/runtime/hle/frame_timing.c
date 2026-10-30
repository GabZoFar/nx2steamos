// The frame rate of games without Breath of the Wild's sites (framepace.c needs those), by the build of their main
// module: where the game picks its present interval, and how to let it pick 1 (60 fps).
//
// Tears of the Kingdom 1.4.3 (main 277178b7dba1b6d4): a virtual method of its frame loop (main+0x2daa20, this) calls
// main+0x2db174 with its timing at this + 0x200, which picks the present interval from the measured frame time
// within a minimum (+0x00, 2: 30 fps) and a maximum (+0x04), with hysteresis (+0x40 the interval now, +0x44..+0x4c
// its counters), and sets the frames per second its game time steps by, 60 / interval (+0x50). With the minimum at 1
// the game runs at 60 fps where frames are fast enough and steps its time to match, and drops to 30 or 20 by itself
// under load (as FPSLocker's config for this build does; masagrator found the sites, nxnative's
// dm/games/0100F2C0115B6000/patches.py has them). Here the method is hooked (it is reached through its vtable):
// before it, the minimum follows the frame rate setting (Video > Frame rate: 60 or more, or no limit -> 1; 30 -> 2;
// 30 too while a movie plays, as FPSLocker caps them); after it, the interval it chose paces the presents
// (frame_timing_interval, runtime/nvn/objects.c present_pace), in place of the window's (which stays 2).
#include <string.h>

#include "../framepace.h"
#include "../hle.h"
#include "../settings.h"

const char *gamelib_main_build_id(uint64_t *base);  // (runtime/gamelib.c)
extern volatile int g_movie_playing;                 // (runtime/hle/movie.c)

static const struct {
    const char *build;    // main module NSO build id (its first 16 hex digits)
    uint64_t method;      // the frame loop's method that updates the timing (main-relative)
    uint64_t timing;      // its timing, from this
} k_games[] = {
    {"277178b7dba1b6d4", 0x2daa20, 0x200},  // Tears of the Kingdom 1.4.3
};

static GuestFn g_method;
static uint64_t g_timing_off;
static volatile int g_interval;  // the interval the game chose last (0: no such game, or not yet)

int frame_timing_interval(void) { return g_interval; }

static void timing_method(Cpu *c) {
    uint64_t timing = c->x[0] + g_timing_off, lr = c->x[30];
    int fps = framepace_target();
    uint32_t min = fps == 0 || fps >= 60 ? 1 : fps >= 30 ? 2 : 3;
    if (__atomic_load_n(&g_movie_playing, __ATOMIC_RELAXED) > 0 && min < 2) min = 2;
    uint32_t *t = (uint32_t *)(uintptr_t)timing;
    // the minimum, and the lower interval its hysteresis steps down to (+0x44, set from the minimum when the game
    // starts: left at 2, the interval never went below it); the game's maximum stays above them
    if (t[1] >= min) {
        if (t[0] != min) t[0] = min;
        if (t[0x44 / 4] < min || (t[0x44 / 4] > min && t[0x40 / 4] <= t[0x44 / 4])) t[0x44 / 4] = min;
    }
    g_method(c);
    c->x[30] = lr;
    uint32_t iv = t[0x40 / 4];
    if (iv >= 1 && iv <= 4) g_interval = (int)iv;
}

void frame_timing_boot(void) {
    g_interval = 0;
    uint64_t base;
    const char *id = gamelib_main_build_id(&base);
    for (size_t i = 0; i < sizeof k_games / sizeof *k_games; i++) {
        if (strncmp(id, k_games[i].build, strlen(k_games[i].build))) continue;
        uint64_t a = base + k_games[i].method;
        if (!(g_method = dispatch_lookup(a))) {
            LOG("[RED] [frame] the frame loop's timing method at %llx is not in the game code\n", (unsigned long long)a);
            return;
        }
        g_timing_off = k_games[i].timing;
        hle_override(a, timing_method, "frame timing");
        LOG("[frame] the game picks its present interval at main+%llx: paced to the frame rate setting\n",
            (unsigned long long)k_games[i].method);
        return;
    }
}
