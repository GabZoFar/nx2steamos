// SteamOS port: an AudioUnit output unit on an SDL3 audio stream (PipeWire on SteamOS), the counterpart of dm/android/compat/audiotoolbox_aaudio.c with the same
// AudioToolbox subset (dm/android/compat/include/AudioToolbox/AudioToolbox.h): the unit's format (interleaved 16-bit or float PCM at its rate and channel count) is the
// stream's, and SDL's callback asks the unit's render callback for the frames it needs, so the host's pull code (hle_audio.c pull_device) is unchanged.
// The device follows the system's default output (SDL moves the stream when it changes: headphones, a dock's HDMI). A stop holds the stream with silence and a start
// takes it back, as on Android; a stream that stopped calling back since the stop (the host's watchdog retrying after a stall) is opened again.
#include "dm.h"
#include <AudioToolbox/AudioToolbox.h>
#include "sdl_dyn.h"
#include <sched.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

struct dm_audio_component { int unused; };
struct dm_audio_unit {
    AudioStreamBasicDescription fmt; int have_fmt;
    AURenderCallbackStruct cb;
    int32_t frames_hint;
    SDL_AudioStream *st;
    _Atomic uint64_t sample_time;
    uint64_t stop_time;    // sample_time at the last stop: unchanged at the next start = the stream stopped calling back, so it is opened again
    _Atomic int held;      // stopped: the callback gives silence and never calls into the host
    uint8_t *buf; size_t cap;
};
static struct dm_audio_component g_output;

AudioComponent AudioComponentFindNext(AudioComponent after, const AudioComponentDescription *d) {
    return !after && d && d->componentType == kAudioUnitType_Output ? &g_output : NULL;
}
OSStatus AudioComponentInstanceNew(AudioComponent c, AudioComponentInstance *out) {
    if (c != &g_output) return kAudio_UnimplementedError;
    *out = calloc(1, sizeof **out); return *out ? noErr : -1;
}
OSStatus AudioUnitSetProperty(AudioUnit u, AudioUnitPropertyID id, AudioUnitScope scope, AudioUnitElement el, const void *data, UInt32 size) {
    (void)scope; (void)el;
    switch (id) {
    case kAudioUnitProperty_StreamFormat:
        if (size < sizeof u->fmt) return -50;
        memcpy(&u->fmt, data, sizeof u->fmt);
        if (u->fmt.mFormatID != kAudioFormatLinearPCM || (u->fmt.mFormatFlags & kAudioFormatFlagIsNonInterleaved)) return kAudioUnitErr_FormatNotSupported;
        if (!(u->fmt.mFormatFlags & kAudioFormatFlagIsFloat) && u->fmt.mBitsPerChannel != 16) return kAudioUnitErr_FormatNotSupported;
        u->have_fmt = 1; return noErr;
    case kAudioUnitProperty_SetRenderCallback: if (size < sizeof u->cb) return -50; memcpy(&u->cb, data, sizeof u->cb); return noErr;
    case kAudioDevicePropertyBufferFrameSize: if (size >= 4) u->frames_hint = (int32_t)*(const UInt32 *)data; return noErr;
    default: return noErr;
    }
}
OSStatus AudioUnitInitialize(AudioUnit u) { return u->have_fmt && u->cb.inputProc ? noErr : -50; }

enum { CHUNK = 1024 };   // frames per render call at most
static void SDLCALL on_data(void *ud, SDL_AudioStream *st, int additional, int total) {   // SDL's audio thread, with the stream locked
    (void)total; AudioUnit u = ud;
    UInt32 bpf = u->fmt.mBytesPerFrame; if (!bpf || additional <= 0) return;
    int frames = (additional + (int)bpf - 1) / (int)bpf;
    while (frames > 0) {
        int n = frames < CHUNK ? frames : CHUNK; size_t bytes = (size_t)n * bpf;
        if (atomic_load(&u->held)) memset(u->buf, 0, bytes);   // stopped: silence, and the host's output (which it may have freed) is not touched
        else {
            AudioBufferList bl = {1, {{u->fmt.mChannelsPerFrame, (UInt32)bytes, u->buf}}};
            AudioUnitRenderActionFlags fl = 0; struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
            AudioTimeStamp ts = {.mSampleTime = (Float64)atomic_load(&u->sample_time), .mHostTime = (UInt64)t.tv_sec * 1000000000ull + (UInt64)t.tv_nsec};
            if (u->cb.inputProc(u->cb.inputProcRefCon, &fl, &ts, 0, (UInt32)n, &bl)) memset(u->buf, 0, bytes);
        }
        atomic_fetch_add(&u->sample_time, (uint64_t)n);
        SDL_PutAudioStreamData(st, u->buf, (int)bytes);
        frames -= n;
    }
}
static void close_stream(AudioUnit u) { if (u->st) { SDL_DestroyAudioStream(u->st); u->st = NULL; } }   // waits for a callback in progress

OSStatus AudioOutputUnitStart(AudioUnit u) {
    if (u->st && (!atomic_load(&u->held) || atomic_load(&u->sample_time) != u->stop_time)) { atomic_store(&u->held, 0); return noErr; }   // the same stream goes on
    close_stream(u);
    atomic_store(&u->held, 0);
    if (!dm_sdl_ready()) return -1;   // headless: no sound device (the host's silent sink stands in)
    if (!u->buf) { u->cap = (size_t)CHUNK * (u->fmt.mBytesPerFrame ? u->fmt.mBytesPerFrame : 8); u->buf = malloc(u->cap); if (!u->buf) return -1; }
    SDL_AudioSpec spec = {u->fmt.mFormatFlags & kAudioFormatFlagIsFloat ? SDL_AUDIO_F32 : SDL_AUDIO_S16, (int)u->fmt.mChannelsPerFrame, (int)u->fmt.mSampleRate};
    u->st = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec, on_data, u);
    if (!u->st) { static int said; if (!said++) LOG("[RED] [audio] cannot open the output (%d Hz, %d channels): %s", spec.freq, spec.channels, SDL_GetError()); return -1; }
    if (!SDL_ResumeAudioStreamDevice(u->st)) { LOG("[RED] [audio] cannot start the output: %s", SDL_GetError()); close_stream(u); return -1; }
    static int said; if (!said++) LOG("[audio] output: %d Hz, %d channels, %s", spec.freq, spec.channels, spec.format == SDL_AUDIO_F32 ? "float" : "16-bit");
    return noErr;
}
// A stop holds the stream (silence): once it returns no callback is inside the host's render callback, and none enters it until the next start (the callback
// runs with the stream locked, so taking the lock waits for one in progress)
OSStatus AudioOutputUnitStop(AudioUnit u) {
    if (!u->st) return noErr;
    atomic_store(&u->held, 1);
    SDL_LockAudioStream(u->st); SDL_UnlockAudioStream(u->st);
    u->stop_time = atomic_load(&u->sample_time); return noErr;
}
OSStatus AudioUnitUninitialize(AudioUnit u) { close_stream(u); return noErr; }
OSStatus AudioComponentInstanceDispose(AudioComponentInstance u) { if (u) { close_stream(u); free(u->buf); free(u); } return noErr; }
