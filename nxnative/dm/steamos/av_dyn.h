// SteamOS port: FFmpeg's libavcodec, opened at run time for the movie library's H.264 video and AAC sound (movie_av.c, audioconv_av.c; Android uses MediaCodec).
// SteamOS ships FFmpeg (Steam, KDE and the browsers use it). Only what is ABI-stable across libavcodec 58 to 62 (FFmpeg 4 to 8) is used: opaque contexts, the codec IDs,
// the leading fields of AVPacket, AVFrame and AVCodecParameters, and functions; nothing version-dependent is read (pictures get their times by reorder, movie_av.c).
#pragma once
#include <stddef.h>
#include <stdint.h>
typedef struct AVCodec AVCodec; typedef struct AVCodecContext AVCodecContext; typedef struct AVDictionary AVDictionary;
typedef struct { void *buf; int64_t pts, dts; uint8_t *data; int size; int stream_index; int flags; } dm_avpacket;                 // AVPacket's head
typedef struct { uint8_t *data[8]; int linesize[8]; uint8_t **extended_data; int width, height, nb_samples, format; } dm_avframe;  // AVFrame's head
typedef struct { int codec_type, codec_id; uint32_t codec_tag; uint8_t *extradata; int extradata_size; } dm_avpar;                 // AVCodecParameters' head
enum { DM_AV_CODEC_H264 = 27, DM_AV_CODEC_AAC = 0x15002, DM_AV_MEDIA_VIDEO = 0, DM_AV_MEDIA_AUDIO = 1 };
enum { DM_AV_PIX_YUV420P = 0, DM_AV_PIX_YUVJ420P = 12, DM_AV_PIX_NV12 = 23 };
enum { DM_AV_SAMPLE_S16 = 1, DM_AV_SAMPLE_FLT = 3, DM_AV_SAMPLE_S16P = 6, DM_AV_SAMPLE_FLTP = 8 };
#define DM_AVERROR_EAGAIN (-11)
#define DM_AV_PADDING 64   // AV_INPUT_BUFFER_PADDING_SIZE: zero bytes after any buffer handed to a decoder
#define DM_AV_FNS(X) \
    X(unsigned, avcodec_version, (void)) X(const AVCodec *, avcodec_find_decoder, (int)) X(AVCodecContext *, avcodec_alloc_context3, (const AVCodec *)) \
    X(void, avcodec_free_context, (AVCodecContext **)) X(int, avcodec_open2, (AVCodecContext *, const AVCodec *, AVDictionary **)) \
    X(dm_avpar *, avcodec_parameters_alloc, (void)) X(void, avcodec_parameters_free, (dm_avpar **)) X(int, avcodec_parameters_to_context, (AVCodecContext *, const dm_avpar *)) \
    X(int, avcodec_send_packet, (AVCodecContext *, const dm_avpacket *)) X(int, avcodec_receive_frame, (AVCodecContext *, dm_avframe *)) X(void, avcodec_flush_buffers, (AVCodecContext *)) \
    X(dm_avpacket *, av_packet_alloc, (void)) X(void, av_packet_free, (dm_avpacket **)) X(dm_avframe *, av_frame_alloc, (void)) X(void, av_frame_free, (dm_avframe **)) \
    X(void, av_frame_unref, (dm_avframe *)) X(void *, av_mallocz, (size_t)) X(int, av_opt_set_int, (void *, const char *, int64_t, int))
#define DM_AV_PTR(ret, name, args) ret (*name) args;
struct dm_av { DM_AV_FNS(DM_AV_PTR) };
#undef DM_AV_PTR
extern struct dm_av dm_av;
int dm_av_load(void);   // 0 = libavcodec found (once, any thread)
