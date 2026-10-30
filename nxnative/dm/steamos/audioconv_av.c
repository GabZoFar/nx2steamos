// SteamOS port: AudioConverter (AAC → interleaved 16-bit PCM) on FFmpeg's AAC decoder (av_dyn.h), for the SDK's movie library (dm/host/hle_movie.c md_aac); the
// counterpart of dm/android/compat/audioconverter_mediacodec.c, with the same AudioSpecificConfig taken from the ES descriptor hle_movie.c gives as its magic cookie.
// FFmpeg decodes synchronously: each fill decodes the packets the input procedure gives and keeps what does not fit for the next fill.
#include "dm.h"
#include <AudioToolbox/AudioToolbox.h>
#include "av_dyn.h"
#include <stdlib.h>
#include <string.h>

struct dm_audio_converter {
    AudioStreamBasicDescription in, out;
    uint8_t asc[64]; int nasc;
    AVCodecContext *cx; dm_avpacket *pkt; dm_avframe *frm; int failed, red;
    uint8_t *pend; size_t npend, cappend;   // decoded PCM not handed out yet
    uint8_t *ib; size_t ibcap;              // the packet's copy, with the decoder's padding
};

OSStatus AudioConverterNew(const AudioStreamBasicDescription *in, const AudioStreamBasicDescription *out, AudioConverterRef *c) {
    *c = NULL;
    if (in->mFormatID != kAudioFormatMPEG4AAC || out->mFormatID != kAudioFormatLinearPCM || out->mBitsPerChannel != 16 || (out->mFormatFlags & (kAudioFormatFlagIsFloat | kAudioFormatFlagIsNonInterleaved)) ||
        in->mChannelsPerFrame != out->mChannelsPerFrame || !in->mChannelsPerFrame || in->mChannelsPerFrame > 8) return kAudio_UnimplementedError;
    AudioConverterRef a = calloc(1, sizeof *a); if (!a) return -1;
    a->in = *in; a->out = *out; *c = a; return noErr;
}
// one descriptor of an ES descriptor: its tag and its body; the length is in 7-bit groups, a set top bit meaning more follow
static const uint8_t *descr(const uint8_t *p, const uint8_t *e, int *tag, size_t *n) {
    if (p >= e) return NULL;
    *tag = *p++; size_t len = 0;
    for (int i = 0; i < 4 && p < e; i++) { uint8_t b = *p++; len = len << 7 | (b & 0x7f); if (!(b & 0x80)) break; }
    if (len > (size_t)(e - p)) return NULL;
    *n = len; return p;
}
OSStatus AudioConverterSetProperty(AudioConverterRef c, AudioConverterPropertyID id, UInt32 size, const void *data) {
    if (id != kAudioConverterDecompressionMagicCookie) return kAudio_UnimplementedError;
    const uint8_t *p = data, *e = p + size; int tag; size_t n;
    // ES descriptor (3): ES ID (2 bytes) and flags (1) before its decoder configuration (4): 13 bytes before its DecoderSpecificInfo (5) = the AudioSpecificConfig
    if (!(p = descr(p, e, &tag, &n)) || tag != 3 || n < 3) return -50;
    e = p + n; p += 3;
    if (!(p = descr(p, e, &tag, &n)) || tag != 4 || n < 13) return -50;
    e = p + n; p += 13;
    if (!(p = descr(p, e, &tag, &n)) || tag != 5 || !n || n > sizeof c->asc) return -50;
    memcpy(c->asc, p, n); c->nasc = (int)n; return noErr;
}
static void close_codec(AudioConverterRef c) {
    if (c->cx) dm_av.avcodec_free_context(&c->cx);
    if (c->pkt) dm_av.av_packet_free(&c->pkt);
    if (c->frm) dm_av.av_frame_free(&c->frm);
}
static int open_codec(AudioConverterRef c) {
    if (c->cx) return 1;
    if (c->failed || dm_av_load()) return 0;
    c->failed = 1;
    int rate = (int)c->in.mSampleRate, ch = (int)c->in.mChannelsPerFrame;
    if (!c->nasc) {   // no cookie: an AAC-LC configuration from the sample rate and channel count
        static const int rates[] = {96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350};
        int fi = 3; for (int i = 0; i < 13; i++) if (rates[i] == rate) fi = i;
        c->asc[0] = (uint8_t)(2 << 3 | fi >> 1); c->asc[1] = (uint8_t)((fi & 1) << 7 | (ch & 15) << 3); c->nasc = 2;
    }
    const AVCodec *codec = dm_av.avcodec_find_decoder(DM_AV_CODEC_AAC);
    if (!codec) { LOG("[RED] movie: this FFmpeg has no AAC decoder, outputting silence"); return 0; }
    c->cx = dm_av.avcodec_alloc_context3(codec); c->pkt = dm_av.av_packet_alloc(); c->frm = dm_av.av_frame_alloc();
    dm_avpar *par = dm_av.avcodec_parameters_alloc();
    if (!c->cx || !c->pkt || !c->frm || !par) { if (par) dm_av.avcodec_parameters_free(&par); close_codec(c); return 0; }
    par->codec_type = DM_AV_MEDIA_AUDIO; par->codec_id = DM_AV_CODEC_AAC;
    par->extradata = dm_av.av_mallocz((size_t)c->nasc + DM_AV_PADDING);   // the decoder's configuration: the AudioSpecificConfig (parameters_free frees it)
    if (par->extradata) { memcpy(par->extradata, c->asc, (size_t)c->nasc); par->extradata_size = c->nasc; }
    int r = dm_av.avcodec_parameters_to_context(c->cx, par); dm_av.avcodec_parameters_free(&par);
    if (r < 0 || dm_av.avcodec_open2(c->cx, codec, NULL) < 0) { LOG("[RED] movie: the AAC decoder does not open (%d Hz, %d channels)", rate, ch); close_codec(c); return 0; }
    c->failed = 0; return 1;
}
static int16_t s16f(float v) { v *= 32767.0f; return (int16_t)(v > 32767.0f ? 32767 : v < -32768.0f ? -32768 : v); }
static void keep_frame(AudioConverterRef c, const dm_avframe *f) {   // the decoder's samples as interleaved 16-bit PCM of the converter's channel count
    int ch = (int)c->out.mChannelsPerFrame, n = f->nb_samples; size_t fb = 2u * (size_t)ch;
    if (n <= 0) return;
    if (c->npend + (size_t)n * fb > c->cappend) { size_t cap = (c->npend + (size_t)n * fb) * 2; uint8_t *p = realloc(c->pend, cap); if (!p) return; c->pend = p; c->cappend = cap; }
    int16_t *o = (int16_t *)(c->pend + c->npend);
    const uint8_t *pl[8]; for (int k = 0; k < ch; k++) pl[k] = f->data[k] ? f->data[k] : f->data[0];   // a plane the stream lacks: the first one's (a mono stream for stereo)
    switch (f->format) {
    case DM_AV_SAMPLE_FLTP: for (int i = 0; i < n; i++) for (int k = 0; k < ch; k++) o[i * ch + k] = s16f(((const float *)pl[k])[i]); break;
    case DM_AV_SAMPLE_S16P: for (int i = 0; i < n; i++) for (int k = 0; k < ch; k++) o[i * ch + k] = ((const int16_t *)pl[k])[i]; break;
    case DM_AV_SAMPLE_FLT: for (int i = 0; i < n * ch; i++) o[i] = s16f(((const float *)f->data[0])[i]); break;
    case DM_AV_SAMPLE_S16: memcpy(o, f->data[0], (size_t)n * fb); break;
    default: if (!c->red++) LOG("[RED] movie: AAC samples in format %d, played as silence", f->format); memset(o, 0, (size_t)n * fb);
    }
    c->npend += (size_t)n * fb;
}
OSStatus AudioConverterFillComplexBuffer(AudioConverterRef c, AudioConverterComplexInputDataProc proc, void *ref, UInt32 *packets, AudioBufferList *out, AudioStreamPacketDescription *desc) {
    (void)desc;
    UInt32 want = *packets; *packets = 0;
    if (!open_codec(c)) return kAudio_UnimplementedError;
    size_t fb = 2u * c->out.mChannelsPerFrame, cap = out->mBuffers[0].mDataByteSize / fb;
    if (want > cap) want = (UInt32)cap;
    OSStatus st = noErr;
    // packets in while what is decoded is short of the request, and always the first one offered: hle_movie.c offers one packet per fill and asks for one packet's
    // frames, so a packet left untaken would be lost
    for (int first = 1; first || c->npend / fb < want; first = 0) {
        UInt32 np = 1; AudioBufferList bl = {1, {{c->in.mChannelsPerFrame, 0, NULL}}}; AudioStreamPacketDescription *pd = NULL;
        st = proc(c, &np, &bl, &pd, ref);
        if (st || !np || !bl.mBuffers[0].mData || !bl.mBuffers[0].mDataByteSize) break;
        size_t len = bl.mBuffers[0].mDataByteSize;
        if (len + DM_AV_PADDING > c->ibcap) { uint8_t *b = realloc(c->ib, len + DM_AV_PADDING); if (!b) break; c->ib = b; c->ibcap = len + DM_AV_PADDING; }
        memcpy(c->ib, bl.mBuffers[0].mData, len); memset(c->ib + len, 0, DM_AV_PADDING);
        c->pkt->data = c->ib; c->pkt->size = (int)len;
        int r = dm_av.avcodec_send_packet(c->cx, c->pkt); c->pkt->data = NULL; c->pkt->size = 0;
        if (r < 0) { if (!c->red++) LOG("[RED] movie: the AAC decoder refused a packet (%d)", r); continue; }
        while (dm_av.avcodec_receive_frame(c->cx, c->frm) == 0) { keep_frame(c, c->frm); dm_av.av_frame_unref(c->frm); }
    }
    size_t frames = c->npend / fb; if (frames > want) frames = want;
    memcpy(out->mBuffers[0].mData, c->pend, frames * fb);
    memmove(c->pend, c->pend + frames * fb, c->npend - frames * fb); c->npend -= frames * fb;
    out->mBuffers[0].mDataByteSize = (UInt32)(frames * fb);
    *packets = (UInt32)frames;
    return frames ? noErr : st;
}
OSStatus AudioConverterDispose(AudioConverterRef c) {
    if (!c) return noErr;
    close_codec(c); free(c->pend); free(c->ib); free(c); return noErr;
}
