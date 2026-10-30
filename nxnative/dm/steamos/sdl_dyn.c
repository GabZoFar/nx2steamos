// SteamOS port: SDL3 opened at run time (sdl_dyn.h). The copy beside the host (<game folder>/lib, a player's fallback when the system has none) is tried first,
// then the system's; every function the host calls must be there, or SDL counts as missing and the host runs without a window.
#define DM_SDL_NO_REDIRECT
#include "dm.h"
#include "sdl_dyn.h"
#include <dlfcn.h>
#include <limits.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

struct dm_sdl dm_sdl;
static int g_rc = -1;
static void load_once(void) {
    char self[PATH_MAX], beside[PATH_MAX + 32]; ssize_t n = readlink("/proc/self/exe", self, sizeof self - 1);
    void *h = NULL;
    if (n > 0) {   // <game folder>/bin/nx2s-host → <game folder>/lib/libSDL3.so.0
        self[n] = 0; char *s = strrchr(self, '/'); if (s) *s = 0; s = strrchr(self, '/'); if (s) *s = 0;
        snprintf(beside, sizeof beside, "%s/lib/libSDL3.so.0", self); h = dlopen(beside, RTLD_NOW | RTLD_LOCAL);
        if (h) LOG("[sdl] %s", beside);
    }
    if (!h) h = dlopen("libSDL3.so.0", RTLD_NOW | RTLD_LOCAL);
    if (!h) { LOG("[RED] [sdl] no libSDL3.so.0 (%s): no window, no controllers, no sound (SteamOS ships SDL3; elsewhere install it)", dlerror()); return; }
    const char *miss = NULL;
#define DM_SDL_LOAD(f) if (!(dm_sdl.f = (__typeof__(dm_sdl.f))dlsym(h, #f)) && !miss) miss = #f;
    DM_SDL_FNS(DM_SDL_LOAD)
#undef DM_SDL_LOAD
    if (miss) { LOG("[RED] [sdl] libSDL3.so.0 has no %s (SDL 3.2 or later is needed)", miss); return; }
    int v = dm_sdl.SDL_GetVersion(); LOG("[sdl] SDL %d.%d.%d", v / 1000000, v / 1000 % 1000, v % 1000);
    g_rc = 0;
}
int dm_sdl_load(void) { static pthread_once_t once = PTHREAD_ONCE_INIT; pthread_once(&once, load_once); return g_rc; }
