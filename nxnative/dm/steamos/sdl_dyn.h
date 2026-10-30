// SteamOS port: SDL3, opened at run time (dm_sdl_load): the host is linked against glibc alone, so one build runs on any SteamOS image, and SDL comes from the
// system (Steam's own runtime libraries ship it too) or from a copy beside the host (lib/libSDL3.so.0). The headers are SDL 3.2's (third_party/SDL3), so nothing
// newer than 3.2 is called; SDL3 keeps its ABI within 3.x. Code includes this header and calls SDL by its own names: each is a pointer filled by dm_sdl_load.
#pragma once
#include <SDL3/SDL.h>
#include <SDL3/SDL_vulkan.h>

#define DM_SDL_FNS(X) \
    X(SDL_Init) X(SDL_Quit) X(SDL_GetError) X(SDL_SetHint) X(SDL_SetAppMetadata) X(SDL_GetVersion) X(SDL_free) \
    X(SDL_CreateWindow) X(SDL_DestroyWindow) X(SDL_GetWindowSizeInPixels) X(SDL_SetWindowFullscreen) X(SDL_GetWindowFlags) X(SDL_GetDisplayForWindow) \
    X(SDL_GetCurrentDisplayMode) X(SDL_GetDesktopDisplayMode) X(SDL_GetPrimaryDisplay) X(SDL_HideCursor) X(SDL_ShowCursor) X(SDL_GetCurrentVideoDriver) \
    X(SDL_Vulkan_GetInstanceExtensions) X(SDL_Vulkan_CreateSurface) \
    X(SDL_WaitEventTimeout) X(SDL_PollEvent) X(SDL_PushEvent) X(SDL_RegisterEvents) X(SDL_RunOnMainThread) \
    X(SDL_GetGamepads) X(SDL_OpenGamepad) X(SDL_CloseGamepad) X(SDL_GetGamepadID) X(SDL_GetGamepadName) X(SDL_GetGamepadButton) X(SDL_GetGamepadAxis) \
    X(SDL_RumbleGamepad) X(SDL_GetGamepadFromID) X(SDL_GetGamepadButtonLabel) \
    X(SDL_StartTextInput) X(SDL_StopTextInput) \
    X(SDL_OpenAudioDeviceStream) X(SDL_DestroyAudioStream) X(SDL_ResumeAudioStreamDevice) X(SDL_PauseAudioStreamDevice) X(SDL_PutAudioStreamData) \
    X(SDL_LockAudioStream) X(SDL_UnlockAudioStream)

#define DM_SDL_PTR(f) __typeof__(f) *f;
struct dm_sdl { DM_SDL_FNS(DM_SDL_PTR) };
#undef DM_SDL_PTR
extern struct dm_sdl dm_sdl;
int dm_sdl_load(void);    // 0 = loaded (every function found); any thread, once
int dm_sdl_ready(void);   // loaded and SDL_Init done (front_sdl.c)

#ifndef DM_SDL_NO_REDIRECT
#define SDL_Init (dm_sdl.SDL_Init)
#define SDL_Quit (dm_sdl.SDL_Quit)
#define SDL_GetError (dm_sdl.SDL_GetError)
#define SDL_SetHint (dm_sdl.SDL_SetHint)
#define SDL_SetAppMetadata (dm_sdl.SDL_SetAppMetadata)
#define SDL_GetVersion (dm_sdl.SDL_GetVersion)
#define SDL_free (dm_sdl.SDL_free)
#define SDL_CreateWindow (dm_sdl.SDL_CreateWindow)
#define SDL_DestroyWindow (dm_sdl.SDL_DestroyWindow)
#define SDL_GetWindowSizeInPixels (dm_sdl.SDL_GetWindowSizeInPixels)
#define SDL_SetWindowFullscreen (dm_sdl.SDL_SetWindowFullscreen)
#define SDL_GetWindowFlags (dm_sdl.SDL_GetWindowFlags)
#define SDL_GetDisplayForWindow (dm_sdl.SDL_GetDisplayForWindow)
#define SDL_GetCurrentDisplayMode (dm_sdl.SDL_GetCurrentDisplayMode)
#define SDL_GetDesktopDisplayMode (dm_sdl.SDL_GetDesktopDisplayMode)
#define SDL_GetPrimaryDisplay (dm_sdl.SDL_GetPrimaryDisplay)
#define SDL_HideCursor (dm_sdl.SDL_HideCursor)
#define SDL_ShowCursor (dm_sdl.SDL_ShowCursor)
#define SDL_GetCurrentVideoDriver (dm_sdl.SDL_GetCurrentVideoDriver)
#define SDL_Vulkan_GetInstanceExtensions (dm_sdl.SDL_Vulkan_GetInstanceExtensions)
#define SDL_Vulkan_CreateSurface (dm_sdl.SDL_Vulkan_CreateSurface)
#define SDL_WaitEventTimeout (dm_sdl.SDL_WaitEventTimeout)
#define SDL_PollEvent (dm_sdl.SDL_PollEvent)
#define SDL_PushEvent (dm_sdl.SDL_PushEvent)
#define SDL_RegisterEvents (dm_sdl.SDL_RegisterEvents)
#define SDL_RunOnMainThread (dm_sdl.SDL_RunOnMainThread)
#define SDL_GetGamepads (dm_sdl.SDL_GetGamepads)
#define SDL_OpenGamepad (dm_sdl.SDL_OpenGamepad)
#define SDL_CloseGamepad (dm_sdl.SDL_CloseGamepad)
#define SDL_GetGamepadID (dm_sdl.SDL_GetGamepadID)
#define SDL_GetGamepadName (dm_sdl.SDL_GetGamepadName)
#define SDL_GetGamepadButton (dm_sdl.SDL_GetGamepadButton)
#define SDL_GetGamepadAxis (dm_sdl.SDL_GetGamepadAxis)
#define SDL_RumbleGamepad (dm_sdl.SDL_RumbleGamepad)
#define SDL_GetGamepadFromID (dm_sdl.SDL_GetGamepadFromID)
#define SDL_GetGamepadButtonLabel (dm_sdl.SDL_GetGamepadButtonLabel)
#define SDL_StartTextInput (dm_sdl.SDL_StartTextInput)
#define SDL_StopTextInput (dm_sdl.SDL_StopTextInput)
#define SDL_OpenAudioDeviceStream (dm_sdl.SDL_OpenAudioDeviceStream)
#define SDL_DestroyAudioStream (dm_sdl.SDL_DestroyAudioStream)
#define SDL_ResumeAudioStreamDevice (dm_sdl.SDL_ResumeAudioStreamDevice)
#define SDL_PauseAudioStreamDevice (dm_sdl.SDL_PauseAudioStreamDevice)
#define SDL_PutAudioStreamData (dm_sdl.SDL_PutAudioStreamData)
#define SDL_LockAudioStream (dm_sdl.SDL_LockAudioStream)
#define SDL_UnlockAudioStream (dm_sdl.SDL_UnlockAudioStream)
#endif
