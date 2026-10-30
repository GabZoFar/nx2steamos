// SteamOS build: <vulkan/vulkan.h> over the vendored headers (dm/third_party/vulkan-headers ships vulkan_core.h only; the Android build takes the NDK's).
// No window-system platform header: SDL3 makes the surface (SDL_Vulkan_CreateSurface), and the loader is opened at run time (steamos/vk_loader.c).
#pragma once
#include <vulkan/vk_platform.h>
#include <vulkan/vulkan_core.h>
