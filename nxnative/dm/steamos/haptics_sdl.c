// SteamOS port: rumble. The game's vibration for player 1 (hle_haptics.c's state) goes to player 1's controller through SDL_RumbleGamepad, on a thread of its own:
// the haptics sink runs on the game's thread under hle_haptics.c's lock (haptics_android.c does the same with Android's Vibrator). Each motor's low band drives the
// controller's low-frequency (heavy) motor and its high band the high-frequency one; a strength is sent for RUMBLE_MS and renewed while the game keeps it.
// The pause menu's Rumble setting turns it off.
#include "dm.h"
#include "hle_haptics.h"
#include "sdl_dyn.h"
#include <pthread.h>
#include <stdatomic.h>
#include <string.h>
#include <time.h>

#define RUMBLE_MS 600
#define RENEW_NS 400000000ll
#define GAP_NS 25000000ll   // at most one change per this
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER; static pthread_cond_t g_cv = PTHREAD_COND_INITIALIZER;
static dm_haptic_state g_state; static SDL_Gamepad *g_pad; static _Atomic int g_on = 1; static int g_dirty;
static int64_t mono_now(void) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return (int64_t)ts.tv_sec * 1000000000 + ts.tv_nsec; }
static void sink(dm_haptic_state s) {   // hle_haptics.c, under its lock: only hand the state over
    if (s.player) return;
    pthread_mutex_lock(&g_mu); g_state = s; g_dirty = 1; pthread_cond_signal(&g_cv); pthread_mutex_unlock(&g_mu);
}
void dm_haptics_sdl_pad(SDL_Gamepad *g) { pthread_mutex_lock(&g_mu); g_pad = g; g_dirty = 1; pthread_cond_signal(&g_cv); pthread_mutex_unlock(&g_mu); }   // player 1's controller (the main thread)
void dm_haptics_sdl_set(int on) { atomic_store(&g_on, on != 0); pthread_mutex_lock(&g_mu); g_dirty = 1; pthread_cond_signal(&g_cv); pthread_mutex_unlock(&g_mu); }
static uint16_t level(float a) { if (a <= 0.02f) return 0; if (a > 1) a = 1; return (uint16_t)(a * 65535.0f); }
static void *rumble_thread(void *arg) {
    (void)arg; pthread_setname_np(pthread_self(), "rumble");
    uint16_t lo_was = 0, hi_was = 0; int64_t sent = 0;
    for (;;) {
        pthread_mutex_lock(&g_mu);
        if (!g_dirty && !(lo_was || hi_was)) pthread_cond_wait(&g_cv, &g_mu);
        else if (!g_dirty) { struct timespec t; clock_gettime(CLOCK_REALTIME, &t); t.tv_nsec += 100000000; if (t.tv_nsec >= 1000000000) { t.tv_sec++; t.tv_nsec -= 1000000000; } pthread_cond_timedwait(&g_cv, &g_mu, &t); }
        dm_haptic_state s = g_state; SDL_Gamepad *pad = g_pad; g_dirty = 0;
        pthread_mutex_unlock(&g_mu);
        if (!pad) { lo_was = hi_was = 0; continue; }
        float lo = s.motor[0].amplitude_low > s.motor[1].amplitude_low ? s.motor[0].amplitude_low : s.motor[1].amplitude_low;
        float hi = s.motor[0].amplitude_high > s.motor[1].amplitude_high ? s.motor[0].amplitude_high : s.motor[1].amplitude_high;
        uint16_t l = atomic_load(&g_on) ? level(lo) : 0, h = atomic_load(&g_on) ? level(hi) : 0;
        int64_t now = mono_now();
        if (l == lo_was && h == hi_was && (!(l || h) || now - sent < RENEW_NS)) continue;
        if (now - sent < GAP_NS) { struct timespec t = {0, GAP_NS - (now - sent)}; nanosleep(&t, NULL); }
        if (!SDL_RumbleGamepad(pad, l, h, (l || h) ? RUMBLE_MS : 0)) { static int said; if (!said++) LOG("[haptics] this controller does not rumble: %s", SDL_GetError()); }
        lo_was = l; hi_was = h; sent = mono_now();
    }
    return NULL;
}
void dm_haptics_sdl_start(int on) {
    atomic_store(&g_on, on != 0);
    dm_haptics_init(sink, 1);
    dm_haptics_route(0, 1);
    pthread_t t; if (!pthread_create(&t, NULL, rumble_thread, NULL)) pthread_detach(t);
}
