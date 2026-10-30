// media_test: movie_av.c (H.264) and audioconv_av.c (AAC) against the system's FFmpeg, with streams the ffmpeg command made (tools/media_test.sh does it all
// in the dev container). It checks the ABI-stable fields those files rely on, on a real libavcodec:
//   H.264: access units in decode order with their presentation times (B-frames) → pictures in presentation order with those times, each picture's NV12 equal to
//          ffmpeg's own yuv420p decode of the same frame
//   AAC:   ADTS frames' payloads → 16-bit stereo PCM of the 440 Hz sine that was encoded (length, level, pitch)
// Usage: media_test <annexb.h264> <pts.txt> <ref.yuv> <w> <h> <adts.aac>
#include "dm.h"
#include "hle_movie_avc.h"
#include <AudioToolbox/AudioToolbox.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint8_t *slurp(const char *p, size_t *n) {
    FILE *f = fopen(p, "rb"); if (!f) { perror(p); exit(2); }
    fseek(f, 0, SEEK_END); *n = (size_t)ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *b = malloc(*n + 1); if (fread(b, 1, *n, f) != *n) { perror(p); exit(2); } fclose(f); return b;
}
static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; printf("FAIL " __VA_ARGS__); printf("\n"); } } while (0)

static int is_aud(const uint8_t *p, size_t i, size_t n) { return i + 4 < n && !p[i] && !p[i + 1] && ((p[i + 2] == 1 && (p[i + 3] & 31) == 9) || (!p[i + 2] && p[i + 3] == 1 && i + 4 < n && (p[i + 4] & 31) == 9)); }

static int test_h264(const char *h264, const char *ptsf, const char *refp, int w, int h) {
    size_t n, rn; uint8_t *s = slurp(h264, &n), *ref = slurp(refp, &rn);
    int64_t pts[256]; int np = 0; FILE *f = fopen(ptsf, "r"); double t; while (np < 256 && fscanf(f, "%lf", &t) == 1) pts[np++] = (int64_t)llround(t * 1e6); fclose(f);
    size_t au[257]; int na = 0;   // access units start at their access unit delimiters (x264 aud=1)
    for (size_t i = 0; i < n && na < 256; i++) if (is_aud(s, i, n)) { au[na++] = i; i += 4; }
    au[na] = n;
    CHECK(na == np, "H.264: %d access units, %d times", na, np);
    mavc *a = mavc_new(); size_t fs = (size_t)w * h * 3 / 2; uint8_t *pic = malloc(fs); int got = 0; int64_t last = -1, sorted[256];
    memcpy(sorted, pts, sizeof(int64_t) * (size_t)np);
    for (int i = 1; i < np; i++) for (int k = i; k > 0 && sorted[k - 1] > sorted[k]; k--) { int64_t x = sorted[k]; sorted[k] = sorted[k - 1]; sorted[k - 1] = x; }
    for (int i = 0; i <= na; i++) {
        if (i < na) CHECK(mavc_decode(a, s + au[i], (int)(au[i + 1] - au[i]), pts[i]) == 0, "H.264: unit %d refused", i);
        int64_t pt;
        while (mavc_take(a, i == na, pic, w, h, &pt) == 1) {
            CHECK(pt > last, "H.264: picture %d time %lld after %lld", got, (long long)pt, (long long)last);
            CHECK(got < np && pt == sorted[got], "H.264: picture %d time %lld, presentation order wants %lld", got, (long long)pt, got < np ? (long long)sorted[got] : -1LL);
            const uint8_t *ry = ref + (size_t)got * fs, *ru = ry + (size_t)w * h, *rv = ru + (size_t)w * h / 4;
            int bad = memcmp(pic, ry, (size_t)w * h) != 0;
            for (int y = 0; y < h / 2 && !bad; y++) for (int x = 0; x < w / 2; x++) bad |= pic[(size_t)w * h + (size_t)y * w + 2 * x] != ru[y * (w / 2) + x] || pic[(size_t)w * h + (size_t)y * w + 2 * x + 1] != rv[y * (w / 2) + x];
            if (bad) {   // which of ffmpeg's frames it is, if any (an order problem), and how far its luma is from the one in its place
                int same = -1; for (int r = 0; r < np && same < 0; r++) if (!memcmp(pic, ref + (size_t)r * fs, (size_t)w * h)) same = r;
                long d = 0; for (size_t k = 0; k < (size_t)w * h; k++) d += abs(pic[k] - ry[k]);
                CHECK(0, "H.264: picture %d (time %lld) differs from ffmpeg's decode: its luma is frame %d's, mean difference %.2f", got, (long long)pt, same, (double)d / (w * h));
            }
            last = pt; got++;
        }
    }
    CHECK(got == np, "H.264: %d pictures out of %d units", got, np);
    printf("H.264: %d access units (decode order) → %d pictures in presentation order, %dx%d NV12, %s\n", na, got, w, h, fails ? "MISMATCHES" : "identical to ffmpeg's decode");
    mavc_free(a); free(pic); free(s); free(ref);
    return got;
}

struct aac_feed { const uint8_t *s; size_t n, i; };
static OSStatus feed(AudioConverterRef c, UInt32 *np, AudioBufferList *bl, AudioStreamPacketDescription **pd, void *ud) {
    (void)c; (void)pd; struct aac_feed *f = ud;
    if (f->i + 7 > f->n || f->s[f->i] != 0xff) { *np = 0; return 1; }
    size_t len = ((size_t)(f->s[f->i + 3] & 3) << 11) | ((size_t)f->s[f->i + 4] << 3) | (f->s[f->i + 5] >> 5), hdr = (f->s[f->i + 1] & 1) ? 7 : 9;
    bl->mBuffers[0].mData = (void *)(f->s + f->i + hdr); bl->mBuffers[0].mDataByteSize = (UInt32)(len - hdr); *np = 1; f->i += len;
    return 0;
}
static void test_aac(const char *aac) {
    size_t n; uint8_t *s = slurp(aac, &n);
    AudioStreamBasicDescription in = {48000, kAudioFormatMPEG4AAC, 0, 0, 1024, 0, 2, 0, 0}, out = {48000, kAudioFormatLinearPCM, kAudioFormatFlagIsSignedInteger | kAudioFormatFlagIsPacked, 4, 1, 4, 2, 16, 0};
    AudioConverterRef c; CHECK(AudioConverterNew(&in, &out, &c) == noErr, "AAC: no converter");   // no cookie: an AAC-LC configuration from the rate and channels
    struct aac_feed f = {s, n, 0}; static int16_t pcm[48000 * 3 * 2]; size_t frames = 0;
    for (int k = 0; k < 400; k++) {
        int16_t buf[1024 * 2]; AudioBufferList ob = {1, {{2, sizeof buf, buf}}}; UInt32 want = 1024;
        OSStatus st = AudioConverterFillComplexBuffer(c, feed, &f, &want, &ob, NULL);
        if (!want && st) break;
        if (frames + want <= 48000 * 3) memcpy(pcm + frames * 2, buf, (size_t)want * 4);
        frames += want;
    }
    double rms = 0; int cross = 0; size_t from = 4096, to = frames > 48000 ? 48000 : frames;   // past the encoder's priming
    for (size_t i = from; i < to; i++) { rms += (double)pcm[i * 2] * pcm[i * 2]; cross += (pcm[i * 2] >= 0) != (pcm[(i - 1) * 2] >= 0); }
    rms = sqrt(rms / (double)(to - from)); double hz = cross / 2.0 / ((double)(to - from) / 48000);
    CHECK(frames >= 46000 && frames <= 50000, "AAC: %zu frames for 1 s", frames);
    CHECK(rms > 1000, "AAC: level %.0f", rms); CHECK(fabs(hz - 440) < 10, "AAC: pitch %.1f Hz", hz);
    printf("AAC: %zu stereo frames, level %.0f RMS, %.1f Hz (440 encoded)\n", frames, rms, hz);
    AudioConverterDispose(c); free(s);
}

int main(int argc, char **argv) {
    if (argc != 7) { fprintf(stderr, "usage: media_test <annexb.h264> <pts.txt> <ref.yuv> <w> <h> <adts.aac>\n"); return 2; }
    test_h264(argv[1], argv[2], argv[3], atoi(argv[4]), atoi(argv[5]));
    test_aac(argv[6]);
    printf("%s\n", fails ? "FAILED" : "OK");
    return fails != 0;
}
