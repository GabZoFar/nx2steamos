// SteamOS port: H.264 for the SDK's movie library (dm/host/hle_movie_avc.h) on FFmpeg's decoder, opened at run time (av_dyn.h); the counterpart of
// dm/android/movie_avc.c (MediaCodec), whose parameter-set handling, picture queue and NV12 / RGBA output this keeps. Access units go in as Annex B with the SPS / PPS
// the game configured before the first; FFmpeg gives pictures in presentation order, and each takes the smallest input time not yet given out, so nothing
// version-dependent of AVFrame is read (an H.264 stream gives one picture per access unit).
#include "dm.h"
#include "hle_movie_avc.h"
#include "av_dyn.h"
#include <stdlib.h>
#include <string.h>

#define QMAX 16
#define PMAX 64
struct mavc {
    uint8_t sps[512], pps[512]; int nsps, npps, dirty;
    AVCodecContext *cx; dm_avpacket *pkt; dm_avframe *frm; int eos_sent, failed;
    struct { uint8_t *nv12; int w, h; int64_t pts; } q[QMAX]; int nq;
    int64_t pend[PMAX]; int npend;   // input times not given to a picture yet, ascending
    uint8_t *au; size_t aucap, aun;  // the Annex B copy of the access unit being decoded
    int red, rgba;
};

mavc *mavc_new(void) { mavc *a = calloc(1, sizeof *a); if (!a) die("movie: AVC decoder allocation failed"); return a; }
static void close_codec(mavc *a) {
    if (a->cx) dm_av.avcodec_free_context(&a->cx);
    if (a->pkt) dm_av.av_packet_free(&a->pkt);
    if (a->frm) dm_av.av_frame_free(&a->frm);
    a->eos_sent = 0; a->npend = 0;
}
void mavc_drop(mavc *a) {
    for (int i = 0; i < a->nq; i++) free(a->q[i].nv12);
    a->nq = 0; a->npend = 0;
    if (a->cx) { dm_av.avcodec_flush_buffers(a->cx); a->eos_sent = 0; }   // a seek: pictures in the decoder belong to the old position
}
void mavc_free(mavc *a) { if (!a) return; mavc_drop(a); close_codec(a); free(a->au); free(a); }
int mavc_queued(const mavc *a) { return a->nq; }
void mavc_set_rgba(mavc *a) { a->rgba = 1; }

// ---- parameter sets and NAL units (as dm/android/movie_avc.c) ----
static void keep_ps(mavc *a, const uint8_t *nal, size_t n) {   // a parameter set NAL unit (without start code)
    int t = nal[0] & 31; uint8_t *dst = t == 7 ? a->sps : a->pps; int *dn = t == 7 ? &a->nsps : &a->npps;
    if (n > sizeof a->sps) { if (!a->red++) LOG("[RED] movie: AVC parameter set of %zu bytes, too long", n); return; }
    if ((size_t)*dn == n && !memcmp(dst, nal, n)) return;
    memcpy(dst, nal, n); *dn = (int)n; a->dirty = 1;
}
static size_t next_sc(const uint8_t *p, size_t n, size_t i) { for (; i + 3 <= n; i++) if (!p[i] && !p[i + 1] && p[i + 2] == 1) return i; return n; }
static void each_nal(const uint8_t *p, size_t n, void (*fn)(void *, const uint8_t *, size_t), void *ctx) {   // Annex B start codes, or else 4-byte lengths
    size_t s = next_sc(p, n, 0);
    if (s < n && s <= 1) {
        while (s < n) {
            size_t b = s + 3, e = next_sc(p, n, b), end = e;
            while (end > b && !p[end - 1]) end--;
            if (end > b) fn(ctx, p + b, end - b);
            s = e;
        }
    } else for (size_t i = 0; i + 4 <= n;) {
        size_t len = (size_t)p[i] << 24 | (size_t)p[i + 1] << 16 | (size_t)p[i + 2] << 8 | p[i + 3]; i += 4;
        if (!len || len > n - i) break;
        fn(ctx, p + i, len); i += len;
    }
}
static void ps_only(void *ctx, const uint8_t *nal, size_t n) { int t = nal[0] & 31; if (t == 7 || t == 8) keep_ps(ctx, nal, n); }
void mavc_param_sets(mavc *a, const uint8_t *p, size_t n) { each_nal(p, n, ps_only, a); }
void mavc_avcc(mavc *a, const uint8_t *p, size_t n) {   // AVCDecoderConfigurationRecord (the MP4 avcC box)
    if (n < 7 || p[0] != 1) { if (!a->red++) LOG("[RED] movie: unrecognized avcC record (%zu bytes, version %d)", n, n ? p[0] : -1); return; }
    size_t i = 5;
    for (int set = 0; set < 2 && i < n; set++) {
        int cnt = set ? p[i] : p[i] & 31; i++;
        for (int k = 0; k < cnt && i + 2 <= n; k++) { size_t len = (size_t)p[i] << 8 | p[i + 1]; i += 2; if (len > n - i) return; if (len) keep_ps(a, p + i, len); i += len; }
    }
}
static void au_put(mavc *a, const uint8_t *nal, size_t n) {   // a NAL unit with a start code, into the access unit's copy
    if (a->aun + n + 4 + DM_AV_PADDING > a->aucap) { size_t c = (a->aun + n + 4 + DM_AV_PADDING) * 2; uint8_t *b = realloc(a->au, c); if (!b) return; a->au = b; a->aucap = c; }
    static const uint8_t sc[4] = {0, 0, 0, 1}; memcpy(a->au + a->aun, sc, 4); memcpy(a->au + a->aun + 4, nal, n); a->aun += n + 4;
}
static void au_nal(void *ctx, const uint8_t *nal, size_t n) { mavc *a = ctx; int t = nal[0] & 31; if (t == 7 || t == 8) keep_ps(a, nal, n); au_put(a, nal, n); }

// ---- the decoder ----
static int open_codec(mavc *a) {
    if (a->cx) return 1;
    if (a->failed || dm_av_load()) return 0;
    a->failed = 1;
    const AVCodec *c = dm_av.avcodec_find_decoder(DM_AV_CODEC_H264);
    if (!c) { LOG("[RED] movie: this FFmpeg has no H.264 decoder: movies play without pictures"); return 0; }
    a->cx = dm_av.avcodec_alloc_context3(c); a->pkt = dm_av.av_packet_alloc(); a->frm = dm_av.av_frame_alloc();
    if (!a->cx || !a->pkt || !a->frm) { close_codec(a); return 0; }
    const char *e = getenv("DM_MOVIE_THREADS"); dm_av.av_opt_set_int(a->cx, "threads", e && atoi(e) > 0 ? atoi(e) : 1, 0);   // 1: no frame-threading delay (the queue below is short)
    if (dm_av.avcodec_open2(a->cx, c, NULL) < 0) { LOG("[RED] movie: the H.264 decoder does not open"); close_codec(a); return 0; }
    a->failed = 0; return 1;
}
static void pend_add(mavc *a, int64_t t) {   // ascending
    if (a->npend == PMAX) memmove(a->pend, a->pend + 1, --a->npend * sizeof *a->pend);
    int i = a->npend; while (i > 0 && a->pend[i - 1] > t) { a->pend[i] = a->pend[i - 1]; i--; }
    a->pend[i] = t; a->npend++;
}
static int64_t pend_take(mavc *a) { if (!a->npend) return 0; int64_t t = a->pend[0]; memmove(a->pend, a->pend + 1, --a->npend * sizeof *a->pend); return t; }
static void keep_picture(mavc *a, const dm_avframe *f) {   // a compact NV12 copy (the decoder's planes have padded strides)
    int w = f->width & ~1, h = f->height & ~1;
    if (w <= 0 || h <= 0 || a->nq == QMAX) { if (a->nq == QMAX && !a->red++) LOG("[RED] movie: more than %d pictures waiting, one dropped", QMAX); pend_take(a); return; }
    uint8_t *p = malloc((size_t)w * h * 3 / 2); if (!p) return;
    for (int r = 0; r < h; r++) memcpy(p + (size_t)r * w, f->data[0] + (size_t)r * f->linesize[0], (size_t)w);
    uint8_t *uv = p + (size_t)w * h;
    if (f->format == DM_AV_PIX_NV12) for (int r = 0; r < h / 2; r++) memcpy(uv + (size_t)r * w, f->data[1] + (size_t)r * f->linesize[1], (size_t)w);
    else if (f->format == DM_AV_PIX_YUV420P || f->format == DM_AV_PIX_YUVJ420P)
        for (int r = 0; r < h / 2; r++) { const uint8_t *cu = f->data[1] + (size_t)r * f->linesize[1], *cv = f->data[2] + (size_t)r * f->linesize[2]; uint8_t *o = uv + (size_t)r * w;
            for (int x = 0; x < w / 2; x++) { o[2 * x] = cu[x]; o[2 * x + 1] = cv[x]; } }
    else { if (!a->red++) LOG("[RED] movie: H.264 pictures in pixel format %d, shown grey", f->format); memset(uv, 128, (size_t)w * h / 2); }
    a->q[a->nq].nv12 = p; a->q[a->nq].w = w; a->q[a->nq].h = h; a->q[a->nq].pts = pend_take(a); a->nq++;
}
static void receive(mavc *a) { while (dm_av.avcodec_receive_frame(a->cx, a->frm) == 0) { keep_picture(a, a->frm); dm_av.av_frame_unref(a->frm); } }
int mavc_decode(mavc *a, const uint8_t *p, int n, int64_t pts_us) {
    if (n <= 0) return 0;
    if (!open_codec(a)) return -1;
    if (a->eos_sent) { dm_av.avcodec_flush_buffers(a->cx); a->eos_sent = 0; }   // a stream after the end of the last one
    a->aun = 0;
    if (a->dirty) { if (a->nsps) au_put(a, a->sps, (size_t)a->nsps); if (a->npps) au_put(a, a->pps, (size_t)a->npps); a->dirty = 0; }   // configured before this unit: given with it
    each_nal(p, (size_t)n, au_nal, a);
    if (!a->aun) return 0;
    memset(a->au + a->aun, 0, DM_AV_PADDING);
    a->pkt->data = a->au; a->pkt->size = (int)a->aun; a->pkt->pts = a->pkt->dts = pts_us;
    int r = dm_av.avcodec_send_packet(a->cx, a->pkt);
    if (r == DM_AVERROR_EAGAIN) { receive(a); r = dm_av.avcodec_send_packet(a->cx, a->pkt); }   // full: its pictures out first
    a->pkt->data = NULL; a->pkt->size = 0;
    if (r < 0) { if (!a->red++) LOG("[RED] movie: the H.264 decoder refused an access unit (%d)", r); return -1; }
    pend_add(a, pts_us);
    receive(a);
    return 0;
}
// NV12 (video range) → RGBA rows of w pixels; BT.709 from 720 lines up, else BT.601 (as dm/android/movie_avc.c)
static void nv12_rgba(const uint8_t *y, const uint8_t *uv, int pw, int ph, uint8_t *dst, int w, int h) {
    int hd = ph >= 720, cw = pw < w ? pw : w, chh = ph < h ? ph : h;
    int rv = hd ? 459 : 409, gu = hd ? 55 : 100, gv = hd ? 136 : 208, bu = hd ? 541 : 516;   // ×256, 8-bit video range (luma × 298)
    if (cw < w || chh < h) for (size_t i = 0; i < (size_t)w * h; i++) ((uint32_t *)dst)[i] = 0xff000000u;
    for (int r = 0; r < chh; r++) {
        const uint8_t *yr = y + (size_t)r * pw, *cr = uv + (size_t)(r / 2) * pw; uint8_t *o = dst + (size_t)r * w * 4;
        for (int x = 0; x < cw; x++) {
            int c = 298 * (yr[x] - 16), u = cr[x & ~1] - 128, v = cr[(x & ~1) + 1] - 128;
            int R = (c + rv * v + 128) >> 8, G = (c - gu * u - gv * v + 128) >> 8, B = (c + bu * u + 128) >> 8;
            o[4 * x] = (uint8_t)(R < 0 ? 0 : R > 255 ? 255 : R); o[4 * x + 1] = (uint8_t)(G < 0 ? 0 : G > 255 ? 255 : G); o[4 * x + 2] = (uint8_t)(B < 0 ? 0 : B > 255 ? 255 : B); o[4 * x + 3] = 255;
        }
    }
}
int mavc_take(mavc *a, int flush, uint8_t *dst, int w, int h, int64_t *pts_us) {
    if (flush && a->cx && !a->eos_sent) {   // the end of the stream: every picture still in the decoder
        if (dm_av.avcodec_send_packet(a->cx, NULL) == 0) receive(a);
        a->eos_sent = 1;
    }
    if (!a->nq) return 0;
    uint8_t *pic = a->q[0].nv12; int pw = a->q[0].w, ph = a->q[0].h; if (pts_us) *pts_us = a->q[0].pts;
    memmove(a->q, a->q + 1, --a->nq * sizeof a->q[0]);
    const uint8_t *uv = pic + (size_t)pw * ph;
    if (a->rgba) nv12_rgba(pic, uv, pw, ph, dst, w, h);
    else {
        int cw = pw < w ? pw : w, chh = ph < h ? ph : h;
        if (cw < w || chh < h) { memset(dst, 16, (size_t)w * h); memset(dst + (size_t)w * h, 128, (size_t)w * h / 2); }
        for (int r = 0; r < chh; r++) memcpy(dst + (size_t)r * w, pic + (size_t)r * pw, (size_t)cw);
        for (int r = 0; r < chh / 2 && r < h / 2; r++) memcpy(dst + (size_t)w * h + (size_t)r * w, uv + (size_t)r * pw, (size_t)cw);
    }
    free(pic);
    return 1;
}
