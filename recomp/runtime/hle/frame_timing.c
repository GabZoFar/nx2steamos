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
//
// What it measures (this + 0x120, 19.2 MHz ticks): the time its frame loop (main+0x2db8ac) spends in a callback of
// its own (this + 0x158) at the frame's end, game work and a mutex; this + 0x128 is the GPU's time from its
// timestamps, meaningless here (the renderer's counters carry no GPU time) and too large to count. In the opening's
// caves that is ~18-20 ms on a Steam Deck, at the edge of a 60 fps frame: left to itself the game switched to 60 for
// a few frames now and then and back (a judder). So 60 is allowed only after FAST_FRAMES frames in a row under
// FAST_TICKS, and the minimum goes back to 2 at the first frame over SLOW_TICKS: a steady 30 there, 60 where the game
// keeps up with it.
//
// (benchmarks) SAVAGEWIND_FORCE_60=1: the minimum and the maximum at 1 whatever the frames take, so the frame rate
// reached is what the game code and the runtime sustain (up to the display's 60); where they cannot keep 60 the
// game's time runs slow.
#include <stdlib.h>
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
static int g_fast;               // frames in a row whose time stayed under FAST_TICKS
#define FAST_TICKS (14ull * 19200)
#define SLOW_TICKS (16ull * 19200)
#define FAST_FRAMES 120

int frame_timing_interval(void) { return g_interval; }

static void timing_method(Cpu *c) {
    uint64_t timing = c->x[0] + g_timing_off, lr = c->x[30];
    int fps = framepace_target();
    uint32_t min = fps == 0 || fps >= 60 ? 1 : fps >= 30 ? 2 : 3;
    if (__atomic_load_n(&g_movie_playing, __ATOMIC_RELAXED) > 0 && min < 2) min = 2;
    if (min < 2 && g_fast < FAST_FRAMES) min = 2;  // (60 only once the game has kept well under a 60th for a while)
    static int force = -1;
    if (force < 0) force = getenv("SAVAGEWIND_FORCE_60") != NULL;
    uint32_t *t = (uint32_t *)(uintptr_t)timing;
    if (force) min = 1, t[1] = 1;
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
    uint64_t took = *(const uint64_t *)(uintptr_t)(timing - g_timing_off + 0x120);
    if (took < FAST_TICKS) { if (g_fast < FAST_FRAMES) g_fast++; }
    else if (took > SLOW_TICKS) g_fast = 0;
    // SAVAGEWIND_LOG_FRAME_TIMING=1: every 120 frames, the interval and the two times the game measured (this + 0x120,
    // + 0x128, 19.2 MHz ticks; each + its margin in 60ths, timing + 8, + 0xc, must stay under one 60th for 60 fps)
    static int log = -1;
    static uint64_t n, sa, sb, wa, wb;
    if (log < 0) log = getenv("SAVAGEWIND_LOG_FRAME_TIMING") != NULL;
    if (log) {
        const uint64_t *m = (const uint64_t *)(uintptr_t)(timing - g_timing_off + 0x120);
        sa += m[0]; sb += m[1];
        if (m[0] > wa) wa = m[0];
        if (m[1] > wb) wb = m[1];
        if (++n % 120 == 0) {
            const float *mg = (const float *)(uintptr_t)(timing + 8);
            LOG("[frame] interval %u (min %u, %d fast frames): times %.1f / %.1f ms average, %.1f / %.1f longest, margins %.2f / %.2f\n", iv,
                t[0], g_fast, sa / 120 / 19200.0, sb / 120 / 19200.0, wa / 19200.0, wb / 19200.0, mg[0], mg[1]);
            sa = sb = wa = wb = 0;
        }
    }
}

void frame_timing_boot(void) {
    g_interval = 0;
    g_fast = 0;
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
