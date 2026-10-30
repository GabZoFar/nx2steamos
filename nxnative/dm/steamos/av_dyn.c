// SteamOS port: libavcodec opened at run time (av_dyn.h): the newest major first, the copy beside the host (<game folder>/lib) before the system's.
#include "dm.h"
#include "av_dyn.h"
#include <dlfcn.h>
#include <limits.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

struct dm_av dm_av;
static int g_rc = -1;
static void load_once(void) {
    static const int majors[] = {62, 61, 60, 59, 58};
    char self[PATH_MAX], lib[PATH_MAX]; ssize_t n = readlink("/proc/self/exe", self, sizeof self - 1); self[n > 0 ? n : 0] = 0;
    char *s = strrchr(self, '/'); if (s) *s = 0; s = strrchr(self, '/'); if (s) *s = 0;   // <game folder>
    void *h = NULL; int got = 0;
    for (int pass = 0; pass < 2 && !h; pass++)
        for (unsigned i = 0; i < sizeof majors / sizeof *majors && !h; i++) {
            if (pass == 0) snprintf(lib, sizeof lib, "%s/lib/libavcodec.so.%d", self, majors[i]); else snprintf(lib, sizeof lib, "libavcodec.so.%d", majors[i]);
            if ((h = dlopen(lib, RTLD_NOW | RTLD_LOCAL))) got = majors[i];
        }
    if (!h) { LOG("[RED] [movie] no libavcodec (FFmpeg 4 to 8): movies play without pictures and AAC sound"); return; }
    const char *miss = NULL;
#define DM_AV_LOAD(ret, name, args) if (!(dm_av.name = (ret (*) args)dlsym(h, #name)) && !miss) miss = #name;   // libavutil's functions come through libavcodec's dependencies
    DM_AV_FNS(DM_AV_LOAD)
#undef DM_AV_LOAD
    if (miss) { LOG("[RED] [movie] %s has no %s: no movie decoding", lib, miss); return; }
    unsigned v = dm_av.avcodec_version();
    LOG("[movie] %s (libavcodec %u.%u.%u)", lib, v >> 16, v >> 8 & 0xff, v & 0xff);
    if ((int)(v >> 16) != got) { LOG("[RED] [movie] %s reports major %u: not used", lib, v >> 16); return; }
    g_rc = 0;
}
int dm_av_load(void) { static pthread_once_t once = PTHREAD_ONCE_INIT; pthread_once(&once, load_once); return g_rc; }
