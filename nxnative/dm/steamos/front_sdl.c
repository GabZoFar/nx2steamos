// SteamOS port (nx2steamos): the host front end (dm/host/front.h) on SDL3, the counterpart of dm/android/front_android.c, whose pause menu, settings, text entry
// and holding of the game it keeps. The process is one ported game: <game folder>/bin/nx2s-host, started by the folder's launcher with <game folder>/mods.
//   Folders: the game folder (read-only to the host: mods/, RomFS/, aoc/, shaders.dmsv, env.txt, the settings' defaults) and the game's state folder
//   $XDG_DATA_HOME/nx2steamos/<title ID> (DM_ROOT: saves, the player's settings.txt and env.txt, the Vulkan pipeline cache, captured shaders), so porting the game
//   again keeps them; the log goes to $XDG_STATE_HOME/nx2steamos/<title ID>/log.txt unless stderr is a terminal; DM_TEMP is under $XDG_CACHE_HOME.
//   Threads: as on Android, the host's main (dm/host/main.c, built as dm_host_main) runs on a thread of its own with a 16 MB stack, and the guest on it
//   (front_run calls it directly); SDL's events are the main thread's. Without a display (DM_HEADLESS=1, or neither WAYLAND_DISPLAY nor DISPLAY) there is no window,
//   input or sound, which is how a run is checked over SSH.
//   Input: controllers by SDL's gamepad API (Steam Input's virtual pads in Game Mode; buttons by position, so the bottom face button is B and the right one A),
//   one player each; the touch screen; in Desktop Mode the keyboard as on the Mac host (J K U I = A B X Y, WASD the left stick, Q E Z C = L R ZL ZR, Return / Tab = + / −).
//   The pause menu (the game is held, gfx_vk_present.c draws it): Minus and Plus pressed together, the Guide button where Steam does not take it, Esc or F1. F11 = fullscreen.
#include "dm.h"
#include "front.h"
#include "dm_input.h"
#include "overlay_layout.h"
#include "hle_haptics.h"
#include "sdl_dyn.h"
#include "steamos_profile.h"
#include <errno.h>
#include <limits.h>
#include <math.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

int dm_host_main(int argc, char **argv);   // dm/host/main.c (build.py compiles it with -Dmain=dm_host_main)
void dm_focus_set(int st); void dm_audio_suspend(int on); void guest_clock_freeze_for(int reason, int on); void dm_gfx_suspend(int on);
int dm_opmode(void); void dm_set_opmode(int m);   // hle_oe.c: 0 handheld, 1 docked; a change reaches the game as the Switch's operation mode notification
void dm_host_shutdown(const char *why);           // hle_lifecycle.c
void dm_haptics_sdl_start(int on); void dm_haptics_sdl_set(int on); void dm_haptics_sdl_pad(SDL_Gamepad *g);   // haptics_sdl.c
__attribute__((weak)) void vk_window_lost(void) {}       // gfx_vk_present.c: the backend lets go of the window's swapchain
__attribute__((weak)) void dm_overlay_redraw(void) {}    // gfx_vk_present.c: the last frame presented again (the menu changed while the game is held)
__attribute__((weak)) void dm_picture_rect(float r[4]) { r[0] = r[1] = 0; r[2] = r[3] = 1; }   // gfx_vk_present.c: where the game's picture lies in the window

void front_run(void (*guest)(void)) { guest(); }   // the host thread is the guest's main thread (as on Android)
void *front_metal_layer(void) { return NULL; }
void front_screen_match(int fps) { (void)fps; }   // gamescope and Steam's frame limit choose the panel's rate

// ---------------- folders and switches ----------------
static char g_game[PATH_MAX], g_root[PATH_MAX];   // the game folder, the state folder (DM_ROOT)
static int g_headless;
static void mkdirs(const char *path) {   // mkdir -p
    char p[PATH_MAX]; snprintf(p, sizeof p, "%s", path);
    for (char *s = p + 1; *s; s++) if (*s == '/') { *s = 0; mkdir(p, 0755); *s = '/'; }
    mkdir(p, 0755);
}
static void xdg(char *out, size_t n, const char *var, const char *fallback, const char *sub) {   // $XDG_*_HOME/nx2steamos/<title ID>[/sub]
    const char *v = getenv(var), *home = getenv("HOME");
    if (v && *v == '/') snprintf(out, n, "%s/nx2steamos/%s%s%s", v, DM_GAME_ID, sub ? "/" : "", sub ? sub : "");
    else snprintf(out, n, "%s/%s/nx2steamos/%s%s%s", home ? home : "/tmp", fallback, DM_GAME_ID, sub ? "/" : "", sub ? sub : "");
}
// KEY=VALUE per line (# comments), as the Android app's env.txt; ${DM_GAME_DIR} and ${DM_ROOT} in a value are the two folders
static void env_file(const char *path) {
    FILE *f = fopen(path, "r"); char line[2048];
    if (!f) return;
    while (fgets(line, sizeof line, f)) {
        char *e = line + strlen(line); while (e > line && (e[-1] == '\n' || e[-1] == '\r' || e[-1] == ' ')) *--e = 0;
        if (!line[0] || line[0] == '#') continue;
        char *eq = strchr(line, '='); if (!eq) continue; *eq = 0;
        char val[4096]; size_t o = 0;
        for (const char *s = eq + 1; *s && o < sizeof val - 1;) {
            const char *rep = NULL; size_t skip = 0;
            if (!strncmp(s, "${DM_GAME_DIR}", 14)) { rep = g_game; skip = 14; } else if (!strncmp(s, "${DM_ROOT}", 10)) { rep = g_root; skip = 10; }
            if (rep) { o += (size_t)snprintf(val + o, sizeof val - o, "%s", rep); if (o >= sizeof val) o = sizeof val - 1; s += skip; } else val[o++] = *s++;
        }
        val[o] = 0; setenv(line, val, 1);
    }
    fclose(f);
}
static void copy_file(const char *from, const char *to) {
    FILE *a = fopen(from, "rb"); if (!a) return;
    FILE *b = fopen(to, "wb"); if (!b) { fclose(a); return; }
    char buf[4096]; size_t n; while ((n = fread(buf, 1, sizeof buf, a)) > 0) fwrite(buf, 1, n, b);
    fclose(a); fclose(b);
}
static void setup(char *argv0) {
    char self[PATH_MAX]; ssize_t n = readlink("/proc/self/exe", self, sizeof self - 1);
    if (n <= 0) snprintf(self, sizeof self, "%s", argv0); else self[n] = 0;
    char *s = strrchr(self, '/'); if (s) *s = 0;   // <game folder>/bin
    s = strrchr(self, '/'); if (s && !strcmp(s, "/bin")) *s = 0;
    snprintf(g_game, sizeof g_game, "%s", self);
    xdg(g_root, sizeof g_root, "XDG_DATA_HOME", ".local/share", NULL); mkdirs(g_root);
    char p[PATH_MAX + 64];
    if (!isatty(2) || getenv("DM_LOG_FILE")) {   // the log, kept with the previous run's (Steam shows a non-Steam game's output nowhere)
        char dir[PATH_MAX]; xdg(dir, sizeof dir, "XDG_STATE_HOME", ".local/state", NULL); mkdirs(dir);
        const char *lf = getenv("DM_LOG_FILE"); char prev[PATH_MAX + 64];
        if (lf && *lf) snprintf(p, sizeof p, "%s", lf); else snprintf(p, sizeof p, "%s/log.txt", dir);
        snprintf(prev, sizeof prev, "%s/log.prev.txt", dir); rename(p, prev);
        if (!freopen(p, "w", stderr)) fprintf(stdout, "cannot write the log %s\n", p);
    }
    setvbuf(stderr, NULL, _IOLBF, 0);
    setenv("DM_ROOT", g_root, 1); setenv("DM_GAME_DIR", g_game, 1);
    snprintf(p, sizeof p, "%s/RomFS", g_game); setenv("DM_ROMFS", p, 0);
    snprintf(p, sizeof p, "%s/shaders.dmsv", g_game); setenv("DM_VK_SHADERS", p, 0);
    snprintf(p, sizeof p, "%s/aoc", g_game); struct stat st; if (!stat(p, &st) && S_ISDIR(st.st_mode)) setenv("DM_AOC_DIR", p, 0);
    snprintf(p, sizeof p, "%s/font", g_game); if (!stat(p, &st) && S_ISDIR(st.st_mode)) setenv("DM_FONT_DIR", p, 0);   // the button-icon font, when the port made it
    snprintf(p, sizeof p, "%s/save", g_root); mkdirs(p); setenv("DM_SAVE", p, 0);
    char tmp[PATH_MAX]; xdg(tmp, sizeof tmp, "XDG_CACHE_HOME", ".cache", "temp"); mkdirs(tmp); setenv("DM_TEMP", tmp, 0);
    snprintf(p, sizeof p, "%s/settings.txt", g_root);
    if (stat(p, &st)) { char d[PATH_MAX + 32]; snprintf(d, sizeof d, "%s/settings.txt", g_game); copy_file(d, p); }   // first launch: the port's defaults
    snprintf(p, sizeof p, "%s/env.txt", g_game); env_file(p);   // the port's switches
    snprintf(p, sizeof p, "%s/env.txt", g_root); env_file(p);   // the player's own, over them
#ifdef DM_RELEASE
    unsetenv("DM_CTL");   // a release build takes no remote key presses (the control file is a development tool)
#endif
    const char *hl = getenv("DM_HEADLESS");
    g_headless = hl ? atoi(hl) != 0 : !getenv("WAYLAND_DISPLAY") && !getenv("DISPLAY");
    if (g_headless && !getenv("DM_AUDIO")) setenv("DM_AUDIO", "0", 1);   // no sound device to open without a session
}

// ---------------- the window ----------------
static SDL_Window *g_win;
static _Atomic int g_win_w, g_win_h;   // its size in pixels, kept by the main thread (vk_platform.c reads it on the present thread)
static _Atomic int g_hz;               // the display's refresh rate, Hz (0 = unknown)
static int g_sdl;                      // SDL loaded and initialised
int dm_sdl_ready(void) { return g_sdl; }
struct dm_window *dm_front_window(void) { return g_headless ? NULL : (struct dm_window *)g_win; }
void dm_front_pixel_size(uint32_t *w, uint32_t *h) { *w = (uint32_t)atomic_load(&g_win_w); *h = (uint32_t)atomic_load(&g_win_h); }
int front_screen_max_fps(void) { return atomic_load(&g_hz); }
static void window_measure(void) {   // the main thread
    int w = 0, h = 0; if (g_win && SDL_GetWindowSizeInPixels(g_win, &w, &h)) { atomic_store(&g_win_w, w); atomic_store(&g_win_h, h); }
    SDL_DisplayID d = g_win ? SDL_GetDisplayForWindow(g_win) : 0; if (!d) d = SDL_GetPrimaryDisplay();
    const SDL_DisplayMode *m = d ? SDL_GetCurrentDisplayMode(d) : NULL;
    if (m && m->refresh_rate > 1) atomic_store(&g_hz, (int)lroundf(m->refresh_rate));
}

// ---------------- settings (the pause menu's), kept in the state folder as KEY=VALUE lines ----------------
static _Atomic int g_set_overlay, g_set_rumble = 1, g_set_fps;   // overlay: Android's touch gamepad, kept in the file but never shown here
static void settings_load(void) {
    char p[PATH_MAX + 32]; snprintf(p, sizeof p, "%s/settings.txt", g_root); FILE *f = fopen(p, "r"); char line[128];
    if (!f) return;
    while (fgets(line, sizeof line, f)) { int v; if (sscanf(line, "overlay=%d", &v) == 1) atomic_store(&g_set_overlay, v != 0); else if (sscanf(line, "rumble=%d", &v) == 1) atomic_store(&g_set_rumble, v != 0);
        else if (sscanf(line, "fps=%d", &v) == 1) atomic_store(&g_set_fps, v != 0);
        else if (sscanf(line, "docked=%d", &v) == 1) setenv("DM_OPMODE", v ? "1" : "0", 1); }   // over env.txt's: the host reads it once, at its first query
    fclose(f);
}
static void settings_save(void) {
    char p[PATH_MAX + 32], tmp[PATH_MAX + 48]; snprintf(p, sizeof p, "%s/settings.txt", g_root); snprintf(tmp, sizeof tmp, "%s.new", p);
    FILE *f = fopen(tmp, "w"); if (!f) { LOG("[RED] cannot write %s", tmp); return; }
    fprintf(f, "overlay=%d\nrumble=%d\ndocked=%d\nfps=%d\n", atomic_load(&g_set_overlay), atomic_load(&g_set_rumble), dm_opmode() != 0, atomic_load(&g_set_fps));
    fclose(f); rename(tmp, p);
}

// ---------------- holding the game: hidden (minimised), and while the pause menu or the text entry is open; its clock waits and its audio goes silent ----------------
static _Atomic int g_running, g_bg, g_menu, g_tx_on;
static pthread_mutex_t g_hold_mu = PTHREAD_MUTEX_INITIALIZER; static int g_held;
static void hold_sync(void) {
    pthread_mutex_lock(&g_hold_mu);
    int want = atomic_load(&g_bg) || atomic_load(&g_menu) || atomic_load(&g_tx_on);
    if (want != g_held) { g_held = want; if (want) { dm_audio_suspend(1); guest_clock_freeze_for(1, 1); } else { guest_clock_freeze_for(1, 0); dm_audio_suspend(0); } }
    pthread_mutex_unlock(&g_hold_mu);
}
// hidden: a FIFO swapchain on a hidden Wayland window may never give an image back, so the graphics are suspended too (Android's background)
static void go_background(void) { if (!atomic_load(&g_running) || atomic_exchange(&g_bg, 1)) return; dm_focus_set(3); hold_sync(); dm_gfx_suspend(1); }
static void go_foreground(void) { if (!atomic_load(&g_running) || !atomic_exchange(&g_bg, 0)) return; dm_gfx_suspend(0); hold_sync(); dm_focus_set(1); }

// ---------------- controllers: one player each, in the order they came ----------------
enum { NPAD = DM_PLAYERS };
static struct { SDL_JoystickID id; SDL_Gamepad *g; uint64_t btn; int16_t ax[6]; } g_pad[NPAD];
static uint64_t g_kb_btn; static int g_kb_w, g_kb_a, g_kb_s, g_kb_d;   // the keyboard: player 1, with the first controller
static uint64_t btn_bit(int b) {   // by position, as the Android and iOS front ends map: bottom B, right A, left Y, top X
    switch (b) {
    case SDL_GAMEPAD_BUTTON_SOUTH: return DM_BTN_B; case SDL_GAMEPAD_BUTTON_EAST: return DM_BTN_A; case SDL_GAMEPAD_BUTTON_WEST: return DM_BTN_Y; case SDL_GAMEPAD_BUTTON_NORTH: return DM_BTN_X;
    case SDL_GAMEPAD_BUTTON_LEFT_SHOULDER: return DM_BTN_L; case SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER: return DM_BTN_R;
    case SDL_GAMEPAD_BUTTON_LEFT_STICK: return DM_BTN_LS; case SDL_GAMEPAD_BUTTON_RIGHT_STICK: return DM_BTN_RS;
    case SDL_GAMEPAD_BUTTON_START: return DM_BTN_PLUS; case SDL_GAMEPAD_BUTTON_BACK: return DM_BTN_MINUS;
    case SDL_GAMEPAD_BUTTON_DPAD_UP: return DM_BTN_UP; case SDL_GAMEPAD_BUTTON_DPAD_DOWN: return DM_BTN_DOWN; case SDL_GAMEPAD_BUTTON_DPAD_LEFT: return DM_BTN_LEFT; case SDL_GAMEPAD_BUTTON_DPAD_RIGHT: return DM_BTN_RIGHT;
    default: return 0;
    }
}
static int32_t stick(int16_t v) { float f = v / 32767.0f; if (f > -0.12f && f < 0.12f) return 0; if (f < -1) f = -1; return (int32_t)(f * DM_STICK_MAX); }
static int pad_slot(SDL_JoystickID id) { for (int i = 0; i < NPAD; i++) if (g_pad[i].g && g_pad[i].id == id) return i; return -1; }
static void pads_publish(void) {
    uint32_t mask = 0;
    for (int i = 0; i < NPAD; i++) {
        if (!g_pad[i].g && i) continue;
        uint64_t b = g_pad[i].btn; const int16_t *a = g_pad[i].ax;
        if (a[SDL_GAMEPAD_AXIS_LEFT_TRIGGER] > 16384) b |= DM_BTN_ZL; if (a[SDL_GAMEPAD_AXIS_RIGHT_TRIGGER] > 16384) b |= DM_BTN_ZR;
        int32_t lx = stick(a[SDL_GAMEPAD_AXIS_LEFTX]), ly = -stick(a[SDL_GAMEPAD_AXIS_LEFTY]), rx = stick(a[SDL_GAMEPAD_AXIS_RIGHTX]), ry = -stick(a[SDL_GAMEPAD_AXIS_RIGHTY]);   // SDL's y grows downward
        if (!i) {   // player 1: the first controller and the keyboard
            b |= g_kb_btn;
            if (g_kb_a != g_kb_d) lx = g_kb_d ? DM_STICK_MAX : -DM_STICK_MAX;
            if (g_kb_w != g_kb_s) ly = g_kb_w ? DM_STICK_MAX : -DM_STICK_MAX;
            dm_pad_set(b, lx, ly, rx, ry);
        } else dm_pad_set_player(i, b, lx, ly, rx, ry);
        if (g_pad[i].g) mask |= 1u << i;
    }
    dm_pad_players(mask | 1);
}
static void pad_added(SDL_JoystickID id) {
    if (pad_slot(id) >= 0) return;
    int i = 0; while (i < NPAD && g_pad[i].g) i++;
    if (i == NPAD) { LOG("[input] a controller beyond %d players: not used", NPAD); return; }
    SDL_Gamepad *g = SDL_OpenGamepad(id); if (!g) { LOG("[RED] [input] cannot open controller %u: %s", (unsigned)id, SDL_GetError()); return; }
    memset(&g_pad[i], 0, sizeof g_pad[i]); g_pad[i].id = id; g_pad[i].g = g;
    const char *nm = SDL_GetGamepadName(g); LOG("[input] controller %u (%s): player %d", (unsigned)id, nm ? nm : "?", i + 1);
    if (!i) dm_haptics_sdl_pad(g);
    pads_publish();
}
static void pad_removed(SDL_JoystickID id) {
    int i = pad_slot(id); if (i < 0) return;
    if (!i) dm_haptics_sdl_pad(NULL);
    SDL_CloseGamepad(g_pad[i].g); memset(&g_pad[i], 0, sizeof g_pad[i]);
    LOG("[input] controller %u detached (player %d)", (unsigned)id, i + 1);
    if (!i) for (int k = 1; k < NPAD; k++) if (g_pad[k].g) { g_pad[0] = g_pad[k]; memset(&g_pad[k], 0, sizeof g_pad[k]); dm_haptics_sdl_pad(g_pad[0].g); LOG("[input] player %d's controller is player 1's now", k + 1); break; }
    pads_publish();
}

// ---------------- the overlay's interface (gfx_vk_present.c): no touch gamepad on SteamOS, only the menu, the text entry and the frame rate counter ----------------
int dm_overlay_visible(void) { return 0; }
float dm_overlay_scale(void) { return 1; }
void dm_overlay_knob(int stick_, float *dx, float *dy) { (void)stick_; *dx = *dy = 0; }
int64_t dm_overlay_pressed(int ctl, uint64_t bit) { (void)ctl; (void)bit; return 0; }
void dm_overlay_menu_xy(float *x, float *y) { *x = *y = 0; }

// ---------------- the pause menu: the game is held while it is open; controllers, keys and touches go to it ----------------
static _Atomic int g_menu_hi = -1;   // the highlighted item, -1 none
static _Atomic int g_tx_ver;
static int g_menu_finger = -1, g_menu_row = -2;
int dm_menu_state(void) {
    if (atomic_load(&g_tx_on)) return 1 << 16 | (atomic_load(&g_tx_ver) & 0xffff);   // the text entry's panel (gfx_vk_present.c ov_text_panel)
    if (!atomic_load(&g_menu)) return -1;
    return atomic_load(&g_set_overlay) | atomic_load(&g_set_rumble) << 1 | (dm_opmode() != 0) << 2 | atomic_load(&g_set_fps) << 3 | (atomic_load(&g_menu_hi) + 1) << 4;
}
int dm_overlay_fps(void) { return atomic_load(&g_set_fps) && !atomic_load(&g_menu); }
static void release_all(void) {   // nothing stays pressed in the game
    for (int i = 0; i < NPAD; i++) { g_pad[i].btn = 0; memset(g_pad[i].ax, 0, sizeof g_pad[i].ax); }
    g_kb_btn = 0; g_kb_w = g_kb_a = g_kb_s = g_kb_d = 0; pads_publish(); dm_input_set(0, 0, 0, 0, 0); dm_touch_set(NULL, 0);
}
static void menu_open(void) {
    if (!atomic_load(&g_running) || atomic_exchange(&g_menu, 1)) return;
    atomic_store(&g_menu_hi, 0); g_menu_finger = -1; release_all();
    dm_haptics_suspend(4, 1); hold_sync(); dm_overlay_redraw();
    LOG("[menu] opened: the game is paused");
}
static void menu_close(void) {
    if (!atomic_exchange(&g_menu, 0)) return;
    g_menu_finger = -1; release_all(); hold_sync(); dm_haptics_suspend(4, 0); dm_overlay_redraw();
    LOG("[menu] closed: the game goes on (rumble %s, frame rate %s, %s)", atomic_load(&g_set_rumble) ? "on" : "off", atomic_load(&g_set_fps) ? "shown" : "hidden", dm_opmode() ? "docked" : "handheld");
}
static void menu_do(int item) {
    if (item == OVM_OVERLAY) { atomic_store(&g_set_overlay, !atomic_load(&g_set_overlay)); settings_save(); }
    else if (item == OVM_RUMBLE) { int on = !atomic_load(&g_set_rumble); atomic_store(&g_set_rumble, on); dm_haptics_sdl_set(on); settings_save(); }
    else if (item == OVM_FPS) { atomic_store(&g_set_fps, !atomic_load(&g_set_fps)); settings_save(); }
    else if (item == OVM_MODE) { dm_set_opmode(dm_opmode() ? 0 : 1); settings_save(); }   // the game takes it when it goes on
    if (item == OVM_RESUME) menu_close(); else dm_overlay_redraw();
}
static void menu_key(uint64_t bit) {
    int hi = atomic_load(&g_menu_hi);
    if (bit == DM_BTN_UP || bit == DM_BTN_DOWN) { hi = hi < 0 ? 0 : (hi + (bit == DM_BTN_UP ? OVM_N - 1 : 1)) % OVM_N; atomic_store(&g_menu_hi, hi); dm_overlay_redraw(); }
    else if (bit == DM_BTN_A) menu_do(hi < 0 ? OVM_RESUME : hi);
    else if (bit == DM_BTN_B || bit == DM_BTN_PLUS) menu_close();
}

// ---------------- text entry: the game's software keyboard (hle_swkbd.c → front_text_input), the overlay's on-screen keyboard as on Android ----------------
// Controller: the d-pad moves the highlight, A presses it, B erases, Y shifts, + accepts, − cancels. A keyboard types: Steam's own on-screen keyboard too, which SDL
// opens itself in Game Mode (SDL_ENABLE_STEAM_SCREEN_KEYBOARD) and STEAM + X anywhere; Backspace erases, Enter accepts, Esc cancels. Touch: tap the keys.
static pthread_mutex_t g_tx_mu = PTHREAD_MUTEX_INITIALIZER; static pthread_cond_t g_tx_cv = PTHREAD_COND_INITIALIZER;
static struct { char title[128], text[256]; int n, max, done, ok, sel, shift, ptr; } g_tx;
int dm_text_entry(char *title, int tcap, char *text, int xcap, int *sel, int *shift) {
    if (!atomic_load(&g_tx_on)) return 0;
    pthread_mutex_lock(&g_tx_mu);
    snprintf(title, (size_t)tcap, "%s", g_tx.title); snprintf(text, (size_t)xcap, "%s", g_tx.text); *sel = g_tx.sel; *shift = g_tx.shift;
    pthread_mutex_unlock(&g_tx_mu);
    return 1;
}
static void tx_changed(void) { atomic_fetch_add(&g_tx_ver, 1); dm_overlay_redraw(); }
static void tx_add(int c) {   // the caller holds g_tx_mu; a capital ends the shift
    if (c < 32 || c > 126 || g_tx.n >= g_tx.max) return;
    g_tx.text[g_tx.n++] = (char)c; g_tx.text[g_tx.n] = 0;
    if (c >= 'A' && c <= 'Z') g_tx.shift = 0;
}
static void tx_finish(int ok) { pthread_mutex_lock(&g_tx_mu); g_tx.ok = ok; g_tx.done = 1; pthread_cond_broadcast(&g_tx_cv); pthread_mutex_unlock(&g_tx_mu); }
static void tx_press(int i) {   // an on-screen key
    if (i == OVK_OK) { tx_finish(1); return; }
    pthread_mutex_lock(&g_tx_mu);
    if (i == OVK_SHIFT) g_tx.shift = !g_tx.shift;
    else if (i == OVK_SPACE) tx_add(' ');
    else if (i == OVK_DEL) { if (g_tx.n) g_tx.text[--g_tx.n] = 0; }
    else tx_add(ov_kb_char(i, g_tx.shift));
    pthread_mutex_unlock(&g_tx_mu);
    tx_changed();
}
static void tx_bit(uint64_t bit) {   // a controller button (or its keyboard key) while the entry is open
    if (bit == DM_BTN_PLUS) { tx_finish(1); return; }
    if (bit == DM_BTN_MINUS) { tx_finish(0); return; }
    if (bit == DM_BTN_A) { int s; pthread_mutex_lock(&g_tx_mu); s = g_tx.sel; pthread_mutex_unlock(&g_tx_mu); tx_press(s); return; }
    pthread_mutex_lock(&g_tx_mu);
    int dx = bit == DM_BTN_LEFT ? -1 : bit == DM_BTN_RIGHT ? 1 : 0, dy = bit == DM_BTN_UP ? -1 : bit == DM_BTN_DOWN ? 1 : 0;
    if (dx || dy) g_tx.sel = ov_kb_move(g_tx.sel, dx, dy);
    else if (bit == DM_BTN_B) { if (g_tx.n) g_tx.text[--g_tx.n] = 0; }
    else if (bit == DM_BTN_Y) g_tx.shift = !g_tx.shift;
    pthread_mutex_unlock(&g_tx_mu);
    tx_changed();
}
static void tx_text(const char *utf8) {   // typed text (a keyboard, Steam's keyboard): its ASCII characters, the overlay's font has no others
    pthread_mutex_lock(&g_tx_mu); for (const unsigned char *s = (const unsigned char *)utf8; *s; s++) if (*s < 128) tx_add(*s); pthread_mutex_unlock(&g_tx_mu);
    tx_changed();
}
int front_text_input(const char *title, const char *sub, const char *guide, const char *initial, int max_chars, char *out, int out_cap) {
    if (!out || out_cap <= 0) return -1;
    if (!dm_front_window()) return -1;   // headless: nothing is shown
    pthread_mutex_lock(&g_tx_mu);
    const char *t = title && *title ? title : guide && *guide ? guide : sub && *sub ? sub : "Enter text";
    snprintf(g_tx.title, sizeof g_tx.title, "%s", t);
    g_tx.n = 0; g_tx.text[0] = 0;
    for (const char *s = initial ? initial : ""; *s && g_tx.n < (int)sizeof g_tx.text - 1; s++) if ((unsigned char)*s >= 32 && (unsigned char)*s < 127) g_tx.text[g_tx.n++] = *s;
    g_tx.text[g_tx.n] = 0;
    g_tx.max = max_chars > 0 && max_chars < (int)sizeof g_tx.text - 1 ? max_chars : (int)sizeof g_tx.text - 1;
    if (g_tx.max > out_cap - 1) g_tx.max = out_cap - 1;
    g_tx.done = g_tx.ok = 0; g_tx.sel = 11; g_tx.shift = g_tx.n == 0; g_tx.ptr = -1;
    pthread_mutex_unlock(&g_tx_mu);
    release_all();
    atomic_store(&g_tx_on, 1); hold_sync(); tx_changed();
    SDL_Event e = {0}; e.type = SDL_EVENT_USER; e.user.code = 1; SDL_PushEvent(&e);   // the main thread starts SDL's text input
    LOG("[text] entry open: \"%s\" (at most %d characters)", g_tx.title, g_tx.max);
    pthread_mutex_lock(&g_tx_mu);
    while (!g_tx.done) pthread_cond_wait(&g_tx_cv, &g_tx_mu);
    int ok = g_tx.ok; if (ok) snprintf(out, (size_t)out_cap, "%s", g_tx.text);
    pthread_mutex_unlock(&g_tx_mu);
    atomic_store(&g_tx_on, 0); hold_sync(); dm_overlay_redraw();
    e.user.code = 2; SDL_PushEvent(&e);
    LOG("[text] entry %s", ok ? "accepted" : "cancelled");
    return ok;
}

// ---------------- touch: the game's touch screen, the menu's rows, the entry's keys ----------------
static struct { SDL_FingerID id; int used; float x, y; } g_fing[16];
static void touches_publish(void) {
    float pic[4] = {0, 0, 1, 1}; dm_picture_rect(pic); if (pic[2] <= 0 || pic[3] <= 0) pic[2] = pic[3] = 1;
    dm_touch t[16]; int n = 0;
    for (int i = 0; i < 16; i++) if (g_fing[i].used) { t[n].id = (uint32_t)i; t[n].x = (g_fing[i].x - pic[0]) / pic[2]; t[n].y = (g_fing[i].y - pic[1]) / pic[3]; n++; }   // relative to the game's picture
    dm_touch_set(t, n);
}
static void finger(int kind, SDL_FingerID id, float x, float y) {   // kind 0 down, 1 move, 2 up; x, y in [0, 1] of the window
    float W = (float)atomic_load(&g_win_w), H = (float)atomic_load(&g_win_h);
    if (atomic_load(&g_tx_on)) {   // a key is pressed when the finger that went down on it lifts there
        float p[4]; ov_menu_rect(W, H, p); int key = ov_kb_at(x * W, y * H, p);
        if (kind == 0) { pthread_mutex_lock(&g_tx_mu); g_tx.ptr = key; if (key >= 0) g_tx.sel = key; pthread_mutex_unlock(&g_tx_mu); if (key >= 0) tx_changed(); }
        else if (kind == 2) { int down; pthread_mutex_lock(&g_tx_mu); down = g_tx.ptr; g_tx.ptr = -1; pthread_mutex_unlock(&g_tx_mu); if (key >= 0 && key == down) tx_press(key); }
        return;
    }
    if (atomic_load(&g_menu)) {   // a row is done when the finger lifts on the row it went down on; a tap outside the panel closes
        int row = ov_menu_row_at(x * W, y * H, W, H);
        if (kind == 0 && g_menu_finger < 0) { g_menu_finger = (int)(id & 0x7fffffff); g_menu_row = row; if (row >= 0) { atomic_store(&g_menu_hi, row); dm_overlay_redraw(); } }
        else if (kind == 2 && g_menu_finger == (int)(id & 0x7fffffff)) { g_menu_finger = -1; if (row == g_menu_row && row >= 0) menu_do(row); else if (row == g_menu_row && row == -2) menu_close(); }
        return;
    }
    int k = -1; for (int i = 0; i < 16; i++) if (g_fing[i].used && g_fing[i].id == id) k = i;
    if (kind == 0 && k < 0) { for (int i = 0; i < 16 && k < 0; i++) if (!g_fing[i].used) k = i; if (k < 0) return; g_fing[k].used = 1; g_fing[k].id = id; }
    if (k < 0) return;
    if (kind == 2) g_fing[k].used = 0; else { g_fing[k].x = x; g_fing[k].y = y; }
    touches_publish();
}

// ---------------- keyboard (Desktop Mode) ----------------
static uint64_t key_bit(SDL_Scancode k) {   // as the Mac host (front_mac.m key_bit)
    switch (k) {
    case SDL_SCANCODE_J: return DM_BTN_A; case SDL_SCANCODE_K: return DM_BTN_B; case SDL_SCANCODE_U: return DM_BTN_X; case SDL_SCANCODE_I: return DM_BTN_Y;
    case SDL_SCANCODE_F: return DM_BTN_LS; case SDL_SCANCODE_G: return DM_BTN_RS;
    case SDL_SCANCODE_Q: return DM_BTN_L; case SDL_SCANCODE_E: return DM_BTN_R; case SDL_SCANCODE_Z: return DM_BTN_ZL; case SDL_SCANCODE_C: return DM_BTN_ZR;
    case SDL_SCANCODE_RETURN: return DM_BTN_PLUS; case SDL_SCANCODE_TAB: return DM_BTN_MINUS;
    case SDL_SCANCODE_LEFT: return DM_BTN_LEFT; case SDL_SCANCODE_UP: return DM_BTN_UP; case SDL_SCANCODE_RIGHT: return DM_BTN_RIGHT; case SDL_SCANCODE_DOWN: return DM_BTN_DOWN;
    case SDL_SCANCODE_SPACE: return DM_BTN_SHAKE;   // the controller shaken while held
    default: return 0;
    }
}
static void key_event(const SDL_KeyboardEvent *k) {
    SDL_Scancode sc = k->scancode; int down = k->down, rep = k->repeat;
    if (atomic_load(&g_tx_on)) {
        if (!down) return;
        if (sc == SDL_SCANCODE_RETURN || sc == SDL_SCANCODE_KP_ENTER) tx_finish(1);
        else if (sc == SDL_SCANCODE_ESCAPE) tx_finish(0);
        else if (sc == SDL_SCANCODE_BACKSPACE) { pthread_mutex_lock(&g_tx_mu); if (g_tx.n) g_tx.text[--g_tx.n] = 0; pthread_mutex_unlock(&g_tx_mu); tx_changed(); }
        else { uint64_t b = sc == SDL_SCANCODE_LEFT ? DM_BTN_LEFT : sc == SDL_SCANCODE_RIGHT ? DM_BTN_RIGHT : sc == SDL_SCANCODE_UP ? DM_BTN_UP : sc == SDL_SCANCODE_DOWN ? DM_BTN_DOWN : 0; if (b) tx_bit(b); }
        return;   // characters come as text input
    }
    if (down && !rep && (sc == SDL_SCANCODE_ESCAPE || sc == SDL_SCANCODE_F1)) { if (atomic_load(&g_menu)) menu_close(); else menu_open(); return; }
    if (down && !rep && sc == SDL_SCANCODE_F11 && g_win) { SDL_SetWindowFullscreen(g_win, !(SDL_GetWindowFlags(g_win) & SDL_WINDOW_FULLSCREEN)); return; }
    if (atomic_load(&g_menu)) {
        if (!down || rep) return;
        uint64_t b = sc == SDL_SCANCODE_UP ? DM_BTN_UP : sc == SDL_SCANCODE_DOWN ? DM_BTN_DOWN : sc == SDL_SCANCODE_RETURN || sc == SDL_SCANCODE_J ? DM_BTN_A : sc == SDL_SCANCODE_K ? DM_BTN_B : 0;
        if (b) menu_key(b);
        return;
    }
    switch (sc) { case SDL_SCANCODE_W: g_kb_w = down; break; case SDL_SCANCODE_A: g_kb_a = down; break; case SDL_SCANCODE_S: g_kb_s = down; break; case SDL_SCANCODE_D: g_kb_d = down; break; default: break; }
    uint64_t m = key_bit(sc);
    g_kb_btn = down ? (g_kb_btn | m) : (g_kb_btn & ~m);
    pads_publish();
}
static void button_event(const SDL_GamepadButtonEvent *b) {
    int i = pad_slot(b->which); if (i < 0) return;
    uint64_t bit = btn_bit(b->button);
    if (b->button == SDL_GAMEPAD_BUTTON_GUIDE) { if (b->down) { if (atomic_load(&g_menu)) menu_close(); else if (!atomic_load(&g_tx_on)) menu_open(); } return; }   // where Steam leaves it to the game
    if (atomic_load(&g_tx_on)) { if (b->down && bit) tx_bit(bit); return; }
    if (atomic_load(&g_menu)) { if (b->down && bit) menu_key(bit); return; }
    if (!bit) return;
    if (b->down) g_pad[i].btn |= bit; else g_pad[i].btn &= ~bit;
    if (b->down && (g_pad[i].btn & (DM_BTN_MINUS | DM_BTN_PLUS)) == (DM_BTN_MINUS | DM_BTN_PLUS)) { g_pad[i].btn = 0; menu_open(); return; }   // Minus + Plus: the menu
    pads_publish();
}
static void axis_event(const SDL_GamepadAxisEvent *a) {
    int i = pad_slot(a->which); if (i < 0 || a->axis >= 6) return;
    int16_t was = g_pad[i].ax[a->axis]; g_pad[i].ax[a->axis] = a->value;
    if (atomic_load(&g_menu) || atomic_load(&g_tx_on)) {   // the left stick moves the highlight, one step per push
        if (a->axis == SDL_GAMEPAD_AXIS_LEFTY || a->axis == SDL_GAMEPAD_AXIS_LEFTX) {
            uint64_t bit = 0;
            if (a->value < -20000 && was >= -20000) bit = a->axis == SDL_GAMEPAD_AXIS_LEFTY ? DM_BTN_UP : DM_BTN_LEFT;
            else if (a->value > 20000 && was <= 20000) bit = a->axis == SDL_GAMEPAD_AXIS_LEFTY ? DM_BTN_DOWN : DM_BTN_RIGHT;
            if (bit) { if (atomic_load(&g_tx_on)) tx_bit(bit); else if (bit == DM_BTN_UP || bit == DM_BTN_DOWN) menu_key(bit); }
        }
        return;
    }
    pads_publish();
}

// ---------------- the host ----------------
static int g_argc; static char **g_argv;
static void *host_thread(void *a) {
    (void)a; atomic_store(&g_running, 1);
    int rc = dm_host_main(g_argc, g_argv);
    LOG("host returned %d", rc); fflush(stderr);
    _exit(rc);
}
static pthread_t host_start(void) {
    // The game's main thread: a 16 MB stack mapped here and handed over whole, so the range pthread_getattr_np reports (svcQueryMemory's main thread stack,
    // which IL2CPP's garbage collector scans from its bottom) is exactly mapped memory, with a guard page below it (front_android.c does the same)
    size_t ssz = 16 << 20, guard = 64 << 10;
    uint8_t *stk = mmap(NULL, ssz + guard, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    pthread_attr_t at; pthread_attr_init(&at);
    if (stk != MAP_FAILED) { mprotect(stk, guard, PROT_NONE); pthread_attr_setstack(&at, stk + guard, ssz); } else pthread_attr_setstacksize(&at, ssz);
    pthread_t t; if (pthread_create(&t, &at, host_thread, NULL)) die("cannot start the host thread");
    pthread_attr_destroy(&at);
    return t;
}
static int fullscreen_wanted(void) {   // Game Mode (gamescope) or asked for; a Desktop Mode window otherwise
    const char *e = getenv("DM_FULLSCREEN"); if (e) return atoi(e) != 0;
    return getenv("GAMESCOPE_WAYLAND_DISPLAY") || getenv("SteamGamepadUI") || getenv("SteamDeck");
}
static int sdl_start(void) {
    if (dm_sdl_load()) return -1;
    SDL_SetHint(SDL_HINT_NO_SIGNAL_HANDLERS, "1");   // SIGTERM stays the host's (hle_lifecycle.c: a graceful exit)
    SDL_SetHint(SDL_HINT_VIDEO_ALLOW_SCREENSAVER, "0");
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
    SDL_SetHint("SDL_TOUCH_MOUSE_EVENTS", "0");      // touches come once, as touches
    SDL_SetHint("SDL_MOUSE_TOUCH_EVENTS", "1");      // a mouse click in Desktop Mode is a touch
    SDL_SetHint("SDL_ENABLE_STEAM_SCREEN_KEYBOARD", "1");   // SDL 3.4.12+: SDL_StartTextInput opens Steam's on-screen keyboard in Game Mode (steam://open/keyboard)
    if (!getenv("SDL_AUDIO_DEVICE_SAMPLE_FRAMES")) SDL_SetHint(SDL_HINT_AUDIO_DEVICE_SAMPLE_FRAMES, "240");   // 5 ms at 48 kHz, as AAudio on Android (audio_sdl.c)
    SDL_SetAppMetadata(DM_GAME_NAME, NULL, "nx2steamos." DM_GAME_ID);
    if (!SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMEPAD | SDL_INIT_EVENTS)) { LOG("[RED] [sdl] SDL_Init: %s", SDL_GetError()); return -1; }
    if (!SDL_Init(SDL_INIT_AUDIO)) LOG("[RED] [sdl] no sound: %s", SDL_GetError());
    int fs = fullscreen_wanted();
    g_win = SDL_CreateWindow(DM_GAME_NAME, 1280, 720, SDL_WINDOW_VULKAN | SDL_WINDOW_RESIZABLE | SDL_WINDOW_HIGH_PIXEL_DENSITY | (fs ? SDL_WINDOW_FULLSCREEN : 0));
    if (!g_win) { LOG("[RED] [sdl] no window: %s", SDL_GetError()); return -1; }
    if (fs) SDL_HideCursor();
    window_measure(); g_sdl = 1;
    LOG("[sdl] window %dx%d px on %s, display %d Hz%s", atomic_load(&g_win_w), atomic_load(&g_win_h), SDL_GetCurrentVideoDriver(), atomic_load(&g_hz), fs ? ", fullscreen" : "");
    return 0;
}
static void events(void) {
    for (;;) {
        SDL_Event e;
        if (!SDL_WaitEventTimeout(&e, 100)) continue;
        switch (e.type) {
        case SDL_EVENT_QUIT: case SDL_EVENT_WINDOW_CLOSE_REQUESTED: LOG("[sdl] the window was closed"); dm_host_shutdown("window closed"); break;
        case SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED: case SDL_EVENT_WINDOW_DISPLAY_CHANGED: window_measure(); dm_overlay_redraw(); break;   // the backend makes its swapchain again at the new size
        case SDL_EVENT_WINDOW_MINIMIZED: case SDL_EVENT_WINDOW_HIDDEN: go_background(); break;
        case SDL_EVENT_WINDOW_RESTORED: case SDL_EVENT_WINDOW_SHOWN: case SDL_EVENT_WINDOW_MAXIMIZED: go_foreground(); break;
        case SDL_EVENT_WINDOW_FOCUS_GAINED: if (atomic_load(&g_running) && !atomic_load(&g_bg)) dm_focus_set(1); break;
        case SDL_EVENT_WINDOW_FOCUS_LOST: if (atomic_load(&g_running) && !atomic_load(&g_bg)) dm_focus_set(2); break;
        case SDL_EVENT_KEY_DOWN: case SDL_EVENT_KEY_UP: key_event(&e.key); break;
        case SDL_EVENT_TEXT_INPUT: if (atomic_load(&g_tx_on)) tx_text(e.text.text); break;
        case SDL_EVENT_GAMEPAD_ADDED: pad_added(e.gdevice.which); break;
        case SDL_EVENT_GAMEPAD_REMOVED: pad_removed(e.gdevice.which); break;
        case SDL_EVENT_GAMEPAD_BUTTON_DOWN: case SDL_EVENT_GAMEPAD_BUTTON_UP: button_event(&e.gbutton); break;
        case SDL_EVENT_GAMEPAD_AXIS_MOTION: axis_event(&e.gaxis); break;
        case SDL_EVENT_FINGER_DOWN: finger(0, e.tfinger.fingerID, e.tfinger.x, e.tfinger.y); break;
        case SDL_EVENT_FINGER_MOTION: finger(1, e.tfinger.fingerID, e.tfinger.x, e.tfinger.y); break;
        case SDL_EVENT_FINGER_UP: case SDL_EVENT_FINGER_CANCELED: finger(2, e.tfinger.fingerID, e.tfinger.x, e.tfinger.y); break;
        case SDL_EVENT_USER: if (e.user.code == 1) SDL_StartTextInput(g_win); else if (e.user.code == 2) SDL_StopTextInput(g_win); break;
        default: break;
        }
    }
}

int main(int argc, char **argv) {
    // SIGTERM (Steam's Exit game, a session's end) blocked in every thread from the first: hle_lifecycle.c's sigwait thread takes it for the graceful exit
    sigset_t term; sigemptyset(&term); sigaddset(&term, SIGTERM); pthread_sigmask(SIG_BLOCK, &term, NULL);
    setup(argv[0]);
    settings_load();
    // the host's command line: the modules folder (the launcher passes it; the game folder's mods/ otherwise), then whatever follows
    static char mods[PATH_MAX + 8]; snprintf(mods, sizeof mods, "%s/mods", g_game);
    g_argv = calloc((size_t)argc + 2, sizeof *g_argv); g_argv[0] = argv[0]; g_argc = 1;
    if (argc < 2 || !strcmp(argv[1], "--")) g_argv[g_argc++] = mods;
    for (int i = 1; i < argc; i++) g_argv[g_argc++] = argv[i];
    LOG("nx2steamos host: %s (%s, SteamOS profile %s), game folder %s, state %s%s", DM_GAME_NAME, DM_GAME_ID, DM_STEAMOS_STATUS, g_game, g_root, g_headless ? ", headless" : "");
    if (g_headless || sdl_start()) {
        if (!g_headless) LOG("[RED] no window: the game runs headless");
        g_headless = 1;
        pthread_t t = host_start(); pthread_join(t, NULL); return 0;
    }
    dm_haptics_sdl_start(atomic_load(&g_set_rumble));
    { int n = 0; SDL_JoystickID *ids = SDL_GetGamepads(&n); for (int i = 0; ids && i < n; i++) pad_added(ids[i]); SDL_free(ids); }
    pads_publish();
    host_start();
    events();
    return 0;
}
