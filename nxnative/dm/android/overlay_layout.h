// Android port: the on-screen gamepad's layout, shared by the touch handling (front_android.c) and its drawing (gfx_vk_present.c).
// Positions are fractions of the window: x of its width, y of its height; sizes are fractions of its height (round controls stay round on any aspect).
#pragma once
#include <math.h>
#include "dm_input.h"
enum { OV_STICK_L, OV_STICK_R, OV_BUTTON, OV_DPAD, OV_MENU };   // OV_MENU: the menu button (always shown, 70%): pauses the game and opens the settings
typedef struct { int kind; float x, y, r; uint64_t btn; const char *label; } ov_ctl;
// The layout: dm/android/overlay_default.tsv with the game profile's moves (android.tsv overlay rows), compiled by build.py into android_profile.h
#include "android_profile.h"
static const ov_ctl OV_CTLS[] = {DM_AOV_TABLE};
#define OV_N ((int)(sizeof OV_CTLS / sizeof *OV_CTLS))
#define OV_KNOB 0.42f        // a stick's knob radius, of the stick's
#define OV_MENU_ALPHA 0.70f
#define OV_FLASH_NS 180000000ll   // a pressed button's flash fades to its held glow in this time
// front_android.c → gfx_vk_present.c: what to draw (0 nothing: a controller is attached, and its keys open the menu; 1 the gamepad; 2 only the menu
// button: the player hid the gamepad, or keys came from a device not counted as a controller), where a stick's knob is (its offset from the stick's centre
// in stick radii, -1..1), and when each button was pressed (0 = not held; for the d-pad, its four directions by DM_BTN_* bit)
int dm_overlay_visible(void);
void dm_overlay_knob(int stick, float *dx, float *dy);
int64_t dm_overlay_pressed(int ctl, uint64_t bit);   // CLOCK_MONOTONIC ns of the press, 0 = not held
// the menu button can be dragged where it hides nothing of the game's (front_android.c keeps the spot in settings.txt): its centre, as the table's x, y
void dm_overlay_menu_xy(float *x, float *y);
#define OV_DRAG_SLOP 0.02f   // a press on the menu button that moves this far (of the window's height) drags it instead of opening the menu
// the controls' size factor: 1, or more on a small screen so a face button stays at least OV_MIN_MM wide (sizes follow the window's height, which on a
// 6.7" phone made them 7 mm against 12 on an 8.8" tablet). Positions stay where they are
#define OV_MIN_MM 10.0f
float dm_overlay_scale(void);
static inline float ov_r(const ov_ctl *c) { return c->r * dm_overlay_scale(); }
static inline void ov_xy(const ov_ctl *c, float *x, float *y) { if (c->kind == OV_MENU) dm_overlay_menu_xy(x, y); else { *x = c->x; *y = c->y; } }   // where it is now
#define OV_REACH_MIN 0.045f   // a small button (-, +, the menu button: half the face buttons' size, as on a Joy-Con) still takes touches this far from its centre
// L, R, ZL and ZR are drawn as the Switch's shoulder buttons, not discs: a box whose top outer corner is a quarter ellipse (L, R: a wide, low bumper whose
// top sweeps down to the outer side, as a Joy-Con's; ZL, ZR: a taller trigger with its lower inner corner round too). ov_shoulder: -1 a left one, 1 a right
// one, 0 not a shoulder button
#define OV_SHOULDER_EXT 1.6f   // the widest shape's half width, in the control's radii
static inline int ov_shoulder(const ov_ctl *c) {
    if (c->kind != OV_BUTTON) return 0;
    return c->btn == DM_BTN_L || c->btn == DM_BTN_ZL ? -1 : c->btn == DM_BTN_R || c->btn == DM_BTN_ZR ? 1 : 0;
}
// the signed distance from (dx, dy), an offset from the control's centre in its radii, to its edge (negative inside), in its radii
static inline float ov_sdf(const ov_ctl *c, float dx, float dy) {
    int side = ov_shoulder(c); if (!side) return sqrtf(dx * dx + dy * dy) - 1;
    int trig = c->btn == DM_BTN_ZL || c->btn == DM_BTN_ZR;
    float hx = trig ? 1.45f : OV_SHOULDER_EXT, hy = trig ? 0.95f : 0.72f, x = side < 0 ? dx : -dx;   // x < 0: the outer side
    float ex = trig ? 1.1f : 1.35f, ey = trig ? 1.1f : 1.0f, ox = ex - hx, oy = ey - hy;   // the top outer corner's ellipse: its radii and centre
    if (x < ox && dy < oy) {   // its distance, to first order (exact enough for a ring a few pixels wide)
        float u = (x - ox) / ex, v = (dy - oy) / ey, l = sqrtf(u * u + v * v);
        if (l < 1e-6f) return -fminf(ex, ey);
        return (l - 1) * l / sqrtf(u * u / (ex * ex) + v * v / (ey * ey));
    }
    float rad = dy >= 0 && x >= 0 && trig ? 0.60f : 0.22f;   // the other corners
    float qx = fabsf(x) - hx + rad, qy = fabsf(dy) - hy + rad, px = qx > 0 ? qx : 0, py = qy > 0 ? qy : 0;
    return sqrtf(px * px + py * py) + fminf(fmaxf(qx, qy), 0) - rad;
}
// a d-pad direction's circle: centre offset and radius, in stick radii (k: 0 right, 1 left, 2 down, 3 up)
static inline void ov_dpad_dir(int k, float *ox, float *oy, float *r) { float s = 0.42f; *ox = k == 0 ? s * 1.2f : k == 1 ? -s * 1.2f : 0; *oy = k == 2 ? s * 1.2f : k == 3 ? -s * 1.2f : 0; *r = s * 0.75f; }
static const uint64_t OV_DPAD_BITS[4] = {DM_BTN_RIGHT, DM_BTN_LEFT, DM_BTN_DOWN, DM_BTN_UP};

// ---- the pause menu: a panel in the middle of the window, a title row and one row per item ----
enum { OVM_OVERLAY, OVM_RUMBLE, OVM_FPS, OVM_MODE, OVM_RESUME, OVM_N };   // OVM_FPS: the frame rate counter; OVM_MODE: Performance (handheld) or Resolution (docked)
#define OV_MENU_ROW 0.115f   // row height, of the window's height
#define OV_MENU_W 0.70f      // panel width, of the window's height
#define OV_MENU_PAD 0.03f
// what the menu shows (-1 = closed): bit 0 the gamepad is on, bit 1 rumble is on, bit 2 docked, bit 3 the frame rate shown, bits 4..7 the highlighted item + 1 (0 = none)
int dm_menu_state(void);
static inline float ov_menu_scale(void) { float s = dm_overlay_scale(), fit = 0.92f / ((OVM_N + 1) * OV_MENU_ROW + 2 * OV_MENU_PAD); return s < fit ? s : fit; }
static inline void ov_menu_rect(float W, float H, float r[4]) {   // x, y, width, height in window pixels
    float s = ov_menu_scale(), pw = OV_MENU_W * H * s, ph = ((OVM_N + 1) * OV_MENU_ROW + 2 * OV_MENU_PAD) * H * s;
    if (pw > W * 0.96f) pw = W * 0.96f;
    r[0] = (W - pw) / 2; r[1] = (H - ph) / 2; r[2] = pw; r[3] = ph;
}
static inline int ov_menu_row_at(float x, float y, float W, float H) {   // the item under (x, y): 0..OVM_N-1, -1 the panel elsewhere, -2 outside it
    float r[4]; ov_menu_rect(W, H, r);
    if (x < r[0] || x >= r[0] + r[2] || y < r[1] || y >= r[1] + r[3]) return -2;
    float s = ov_menu_scale(), row = (y - r[1] - OV_MENU_PAD * H * s) / (OV_MENU_ROW * H * s);
    int i = (int)floorf(row) - 1;
    return i >= 0 && i < OVM_N ? i : -1;
}

// ---- the text entry's keyboard (front_android.c front_text_input, drawn by gfx_vk_present.c ov_text_panel), in the pause menu's panel rectangle ----
// Four rows of ten character keys and a row of four wide ones; p is the panel's rectangle (x, y, width, height: the window's for touches, the raster's for drawing).
// The panel: the title (its top 13%), the text field (to 28%), the keys (to 90%), the key hints
#define OVK_N 44
static const char *const OVK_ROWS[4] = {"1234567890", "qwertyuiop", "asdfghjkl'", "zxcvbnm-._"};
enum { OVK_SHIFT = 40, OVK_SPACE, OVK_DEL, OVK_OK };
static inline void ov_kb_key(int i, const float p[4], float r[4]) {   // key i's rectangle, a small gap inside its cell
    float pad = p[2] * 0.03f, w = p[2] - 2 * pad, top = p[1] + p[3] * 0.30f, kh = p[3] * 0.60f / 5;
    if (i < 40) { float kw = w / 10; r[0] = p[0] + pad + (i % 10) * kw; r[1] = top + (i / 10) * kh; r[2] = kw; r[3] = kh; }
    else { static const float x0[4] = {0, 0.2f, 0.65f, 0.8f}, ww[4] = {0.2f, 0.45f, 0.15f, 0.2f}; r[0] = p[0] + pad + x0[i - 40] * w; r[1] = top + 4 * kh; r[2] = ww[i - 40] * w; r[3] = kh; }
    float g = kh * 0.06f; r[0] += g; r[1] += g; r[2] -= 2 * g; r[3] -= 2 * g;
}
static inline int ov_kb_at(float x, float y, const float p[4]) {   // the key under (x, y), -1 none
    for (int i = 0; i < OVK_N; i++) { float r[4]; ov_kb_key(i, p, r); if (x >= r[0] && x < r[0] + r[2] && y >= r[1] && y < r[1] + r[3]) return i; }
    return -1;
}
static inline int ov_kb_char(int i, int shift) {   // a character key's character (0 for the wide keys)
    if (i < 0 || i >= 40) return 0;
    int c = OVK_ROWS[i / 10][i % 10]; return shift && c >= 'a' && c <= 'z' ? c - 32 : c;
}
static inline int ov_kb_move(int i, int dx, int dy) {   // the d-pad's move from key i (rows wrap; the wide row maps to the columns above it)
    if (i < 0) return 0;
    static const int wide_col[4] = {0, 4, 7, 9};   // the column each wide key sits under
    int col = i < 40 ? i % 10 : wide_col[i - 40], row = i < 40 ? i / 10 : 4;
    if (dy) { row = (row + dy + 5) % 5; if (row == 4) return col < 2 ? OVK_SHIFT : col < 6 ? OVK_SPACE : col < 8 ? OVK_DEL : OVK_OK; return row * 10 + col; }
    if (row == 4) return 40 + (i - 40 + dx + 4) % 4;
    return row * 10 + (col + dx + 10) % 10;
}
