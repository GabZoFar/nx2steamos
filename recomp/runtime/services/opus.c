// hwopus: the hardware Opus decoder (IHardwareOpusDecoderManager, IHardwareOpusDecoder). The console's decoder is
// libopus behind a service; here it is the host's libopus, opened at run time (libopus.so.0 on Linux: SteamOS has
// it; libopus.0.dylib / opus.dll elsewhere), so the runtime needs no Opus to build. Without it every packet decodes
// to silence. Tears of the Kingdom streams its music and voices through it (nn::codec::...WithHardware).
// Commands as switchbrew's "Audio services" page lists them. Each packet is an OpusDataHeader (big-endian size,
// final range) followed by the packet; the output is interleaved int16.
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <dlfcn.h>
#endif

#include "../hle.h"
#include "../ipc.h"

static struct {
    bool tried;
    void *(*create)(int32_t rate, int channels, int *err);
    void *(*ms_create)(int32_t rate, int channels, int streams, int coupled, const unsigned char *mapping, int *err);
    int (*decode)(void *st, const unsigned char *data, int32_t len, int16_t *pcm, int frame_size, int fec);
    int (*ms_decode)(void *st, const unsigned char *data, int32_t len, int16_t *pcm, int frame_size, int fec);
    int (*ctl)(void *st, int request, ...);
    int (*ms_ctl)(void *st, int request, ...);
    void (*destroy)(void *st);
    void (*ms_destroy)(void *st);
} lib;

static void *lib_open(const char *name) {
#ifdef _WIN32
    return (void *)LoadLibraryA(name);
#else
    return dlopen(name, RTLD_NOW | RTLD_LOCAL);
#endif
}
static void *lib_sym(void *h, const char *name) {
#ifdef _WIN32
    return (void *)GetProcAddress((HMODULE)h, name);
#else
    return dlsym(h, name);
#endif
}

static bool opus_lib(void) {
    if (lib.tried) return lib.decode != NULL;
    lib.tried = true;
    static const char *names[] = {"libopus.so.0", "libopus.so", "libopus.0.dylib", "/opt/homebrew/lib/libopus.0.dylib",
                                  "opus.dll", "libopus-0.dll"};
    void *h = NULL;
    for (size_t i = 0; i < sizeof names / sizeof *names && !h; i++) h = lib_open(names[i]);
    if (!h) { LOG("[RED] [opus] no libopus on this machine: Opus audio plays as silence\n"); return false; }
    lib.create = lib_sym(h, "opus_decoder_create");
    lib.ms_create = lib_sym(h, "opus_multistream_decoder_create");
    lib.decode = lib_sym(h, "opus_decode");
    lib.ms_decode = lib_sym(h, "opus_multistream_decode");
    lib.ctl = lib_sym(h, "opus_decoder_ctl");
    lib.ms_ctl = lib_sym(h, "opus_multistream_decoder_ctl");
    lib.destroy = lib_sym(h, "opus_decoder_destroy");
    lib.ms_destroy = lib_sym(h, "opus_multistream_decoder_destroy");
    if (!lib.create || !lib.ms_create || !lib.decode || !lib.ms_decode || !lib.ctl || !lib.ms_ctl || !lib.destroy ||
        !lib.ms_destroy) {
        LOG("[RED] [opus] libopus lacks the decoder functions: Opus audio plays as silence\n");
        lib.decode = NULL;
        return false;
    }
    LOG("[opus] decoding with the host's libopus\n");
    return true;
}

#define OPUS_RESET_STATE 4028
#define R_OPUS_INVALID_ARGUMENT MAKE_RESULT(111, 6)
#define R_OPUS_INVALID_PACKET MAKE_RESULT(111, 17)

typedef struct {
    void *st;       // OpusDecoder / OpusMSDecoder (NULL without libopus: silence)
    bool ms;        // multistream
    int rate, channels;
} OpusDec;

static uint32_t be32(const uint8_t *p) { return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3]; }

// DecodeInterleaved*: A (or X) the packets' bytes, B (or C) the samples -> consumed bytes, samples per channel.
static void opus_decode_packet(OpusDec *d, IpcReq *req, IpcResp *resp, bool reset, bool perf) {
    IpcBuf *in = in_buf(req, 0), *out = out_buf(req, 0);
    if (!in || !out || in->size < 8) { resp->result = R_OPUS_INVALID_ARGUMENT; return; }
    const uint8_t *src = (const uint8_t *)(uintptr_t)in->addr;
    uint32_t size = be32(src);
    if (size > in->size - 8) { resp->result = R_OPUS_INVALID_PACKET; return; }
    int frames_max = (int)(out->size / (2 * (uint64_t)d->channels));
    int16_t *pcm = (int16_t *)(uintptr_t)out->addr;
    int n;
    if (!d->st) {
        n = d->rate / 50;  // (no libopus: 20 ms of silence, the usual frame)
        if (n > frames_max) n = frames_max;
        memset(pcm, 0, (size_t)n * 2 * d->channels);
    } else {
        if (reset) d->ms ? lib.ms_ctl(d->st, OPUS_RESET_STATE) : lib.ctl(d->st, OPUS_RESET_STATE);
        n = d->ms ? lib.ms_decode(d->st, src + 8, (int32_t)size, pcm, frames_max, 0)
                  : lib.decode(d->st, src + 8, (int32_t)size, pcm, frames_max, 0);
        if (n < 0) {
            static int logged;
            if (logged++ < 8) LOG("[RED] [opus] packet of %u bytes: libopus error %d\n", size, n);
            resp->result = R_OPUS_INVALID_PACKET;
            return;
        }
    }
    OUT(uint32_t, 0) = 8 + size;
    OUT(uint32_t, 4) = (uint32_t)n;
    if (perf) OUT(uint64_t, 8) = 0;  // the decode time, in microseconds
    resp_size(resp, perf ? 16 : 8);
}

static void opus_decoder(Service *s, IpcReq *req, IpcResp *resp) {
    OpusDec *d = s->state;
    switch (req->cmd) {
    case 0: case 2: opus_decode_packet(d, req, resp, false, false); break;  // DecodeInterleaved(ForMultiStream)Old
    case 4: case 5: opus_decode_packet(d, req, resp, false, true); break;   // ...WithPerfOld
    case 6: case 7: case 8: case 9:  // ...WithPerfAndResetOld, DecodeInterleaved(ForMultiStream) (bool reset)
        opus_decode_packet(d, req, resp, IN(uint8_t, 0) & 1, true);
        break;
    case 1: case 3: break;  // SetContext(ForMultiStream)
    default: LOG("[opus] IHardwareOpusDecoder cmd %u (ok)\n", req->cmd);
    }
}

static void opus_decoder_close(Service *s) {
    OpusDec *d = s->state;
    if (d->st) d->ms ? lib.ms_destroy(d->st) : lib.destroy(d->st);
    free(d);
    s->state = NULL;
}

static void opus_open(IpcResp *resp, int rate, int channels, bool ms, int streams, int coupled, const uint8_t *map) {
    if (channels < 1 || channels > (ms ? 255 : 2) || (rate != 8000 && rate != 12000 && rate != 16000 &&
                                                       rate != 24000 && rate != 48000)) {
        LOG("[RED] [opus] decoder for %d Hz, %d channels: not valid\n", rate, channels);
        resp->result = R_OPUS_INVALID_ARGUMENT;
        return;
    }
    OpusDec *d = calloc(1, sizeof *d);
    d->ms = ms;
    d->rate = rate;
    d->channels = channels;
    int err = 0;
    if (opus_lib()) {
        d->st = ms ? lib.ms_create(rate, channels, streams, coupled, map, &err) : lib.create(rate, channels, &err);
        if (!d->st) LOG("[RED] [opus] decoder for %d Hz, %d channels: libopus error %d (silence)\n", rate, channels, err);
    }
    Service *svc = service_new("IHardwareOpusDecoder", opus_decoder, d);
    svc->on_close = opus_decoder_close;
    ipc_return_object(resp, svc);
}

// OpusMultiStreamParameters(Ex), in a buffer: sample rate, channels, streams, stereo streams, (Ex: a flag and 7 bytes
// of padding), then the 256-byte channel mapping.
static void opus_open_ms(IpcReq *req, IpcResp *resp, bool ex) {
    IpcBuf *b = in_buf(req, 0);
    uint32_t need = (ex ? 0x18 : 0x10) + 0x100;
    if (!b || b->size < need) { resp->result = R_OPUS_INVALID_ARGUMENT; return; }
    const int32_t *p = (const int32_t *)(uintptr_t)b->addr;
    opus_open(resp, p[0], p[1], true, p[2], p[3], (const uint8_t *)(uintptr_t)b->addr + (ex ? 0x18 : 0x10));
}

SERVICE("hwopus", hwopus) {
    (void)s;
    switch (req->cmd) {
    case 0: case 4:  // OpenHardwareOpusDecoder(Ex)(parameters, work buffer size, transfer memory)
        opus_open(resp, IN(int32_t, 0), IN(int32_t, 4), false, 0, 0, NULL);
        break;
    case 2: case 6: opus_open_ms(req, resp, req->cmd == 6); break;  // ...ForMultiStream(Ex)
    // GetWorkBufferSize*: the game gives that much transfer memory; the host's decoder keeps its state itself
    case 1: case 5: case 8: OUT(uint32_t, 0) = 0x20000; resp_size(resp, 4); break;
    case 3: case 7: case 9: OUT(uint32_t, 0) = 0x80000; resp_size(resp, 4); break;
    default: LOG("[opus] hwopus cmd %u (ok)\n", req->cmd);
    }
}
