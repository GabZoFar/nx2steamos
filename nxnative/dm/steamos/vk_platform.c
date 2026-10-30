// SteamOS port (nx2steamos): the Linux platform of the Android port's Vulkan backend (dm/android/gfx_vk_*.c, the vkp_* calls in gfx_vk.h).
//   Window: SDL's surface for the front end's window (front_sdl.c), made on the main thread as SDL wants; its size in pixels from the front end.
//   Memory pools: NVN gives the GPU the game's own memory (GPU address = CPU address). As Android places an AHardwareBuffer's pages at the pool's address, Linux makes
//   the pool's pages Vulkan memory, one of two ways (DM_VK_POOLS picks: auto, dmabuf, placed or copy):
//     dmabuf: the memory exported as a dma-buf (VK_EXT_external_memory_dma_buf) and that fd mmapped MAP_SHARED | MAP_FIXED over the pool; the same fd maps it again
//             read-only for nvn_dirty_defrag. Mesa's drivers export their buffer objects this way (Turnip: msm's GEM objects)
//     placed: VK_EXT_map_memory_placed: the driver maps the memory at the pool's address itself (MAP_FIXED semantics), and unmaps it back to a reservation
//   probes/vkzc.c says which works on a device, and whether the mapping is CPU-cached. A pool that neither takes keeps its anonymous pages and a GPU copy (gfx_vk_core.c).
#define _GNU_SOURCE
#include "gfx_vk.h"
#include "sdl_dyn.h"
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

void dm_front_pixel_size(uint32_t *w, uint32_t *h);   // front_sdl.c
dm_window_t *dm_front_window(void);                  // front_sdl.c: the SDL window, NULL headless

// ---------------- window ----------------
static const char *const *g_iext; static uint32_t g_niext;
static void get_iext(void *u) { (void)u; Uint32 n = 0; g_iext = SDL_Vulkan_GetInstanceExtensions(&n); g_niext = g_iext ? n : 0; }
const char *const *vkp_instance_exts(uint32_t *n) {
    *n = 0;
    if (!dm_front_window() || !dm_sdl_ready()) return NULL;   // headless: an instance without surfaces
    if (!g_iext) SDL_RunOnMainThread(get_iext, NULL, true);
    *n = g_niext; return g_iext;
}
struct mk { dm_window_t *w; VkSurfaceKHR s; int ok; const char *err; };
static void make_surface(void *u) { struct mk *m = u; m->ok = SDL_Vulkan_CreateSurface((SDL_Window *)m->w, V.inst, NULL, &m->s); if (!m->ok) m->err = SDL_GetError(); }
int vkp_surface(dm_window_t *w, VkSurfaceKHR *out) {
    struct mk m = {w, VK_NULL_HANDLE, 0, NULL};
    if (!dm_sdl_ready() || !SDL_RunOnMainThread(make_surface, &m, true) || !m.ok) { VKRED_ONCE(1, "no Vulkan surface for the window: %s", m.err ? m.err : "SDL"); return -1; }
    *out = m.s; return 0;
}
void vkp_window_size(dm_window_t *w, uint32_t *width, uint32_t *height) { (void)w; dm_front_pixel_size(width, height); }

// ---------------- device: what the pools need ----------------
static int g_dmabuf, g_placed, g_unmap_reserve; static VkDeviceSize g_placed_align = 4096;
static const char *pools_mode(void) { const char *e = getenv("DM_VK_POOLS"); return e && *e ? e : "auto"; }
static int has_ext(const VkExtensionProperties *e, uint32_t n, const char *name) { for (uint32_t i = 0; i < n; i++) if (!strcmp(e[i].extensionName, name)) return 1; return 0; }
void vkp_device_exts(const VkExtensionProperties *ex, uint32_t ne, const char **on, uint32_t *non, VkBaseOutStructure *tail) {
    const char *mode = pools_mode(); int any = !strcmp(mode, "auto");
    if ((any || !strcmp(mode, "dmabuf")) && has_ext(ex, ne, "VK_KHR_external_memory_fd") && has_ext(ex, ne, "VK_EXT_external_memory_dma_buf")) {
        on[(*non)++] = "VK_KHR_external_memory_fd"; on[(*non)++] = "VK_EXT_external_memory_dma_buf"; g_dmabuf = 1; }
    static VkPhysicalDeviceMapMemoryPlacedFeaturesEXT pf = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAP_MEMORY_PLACED_FEATURES_EXT};
    if ((any || !strcmp(mode, "placed")) && has_ext(ex, ne, "VK_KHR_map_memory2") && has_ext(ex, ne, "VK_EXT_map_memory_placed")) {
        VkPhysicalDeviceMapMemoryPlacedFeaturesEXT q = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAP_MEMORY_PLACED_FEATURES_EXT};
        VkPhysicalDeviceFeatures2 f2 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, &q}; vkGetPhysicalDeviceFeatures2(V.pd, &f2);
        VkPhysicalDeviceMapMemoryPlacedPropertiesEXT pp = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAP_MEMORY_PLACED_PROPERTIES_EXT};
        VkPhysicalDeviceProperties2 p2 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, &pp}; vkGetPhysicalDeviceProperties2(V.pd, &p2);
        // a placed map that ends must leave a reservation behind (memoryUnmapReserve): a hole in the middle of the guest's address space could be taken by any mmap
        if (q.memoryMapPlaced && q.memoryUnmapReserve) {
            on[(*non)++] = "VK_KHR_map_memory2"; on[(*non)++] = "VK_EXT_map_memory_placed";
            pf.memoryMapPlaced = 1; pf.memoryUnmapReserve = 1; pf.pNext = NULL; tail->pNext = (VkBaseOutStructure *)&pf; tail = (VkBaseOutStructure *)&pf;
            g_placed = 1; g_unmap_reserve = 1; if (pp.minPlacedMemoryMapAlignment) g_placed_align = pp.minPlacedMemoryMapAlignment;
        }
    }
    if (!strcmp(mode, "copy")) g_dmabuf = g_placed = 0;
    VKLOG("memory pools: %s (DM_VK_POOLS=%s; dma-buf %s, placed maps %s, alignment %llu, page %ld)", g_dmabuf ? "exported dma-bufs" : g_placed ? "placed maps" : "copied",
          mode, g_dmabuf ? "yes" : "no", g_placed ? "yes" : "no", (unsigned long long)g_placed_align, sysconf(_SC_PAGESIZE));
}
uint64_t vkp_zc_chunk(void) {   // pools are made of chunks of at most this: one Vulkan allocation each (DM_VK_ZC_CHUNK_MB; 1 GB, under every driver's allocation limit seen)
    static uint64_t c; if (!c) { const char *e = getenv("DM_VK_ZC_CHUNK_MB"); c = e && atoi(e) > 0 ? (uint64_t)atoi(e) << 20 : 1ull << 30; }
    return c;
}

// ---------------- memory pools ----------------
static uint32_t host_type(uint32_t bits) {   // cached first: the game reads its pools from the CPU
    uint32_t t = vk_mem_type(bits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT, 0);
    if (t != UINT32_MAX && (V.mem.memoryTypes[t].propertyFlags & VK_MEMORY_PROPERTY_HOST_CACHED_BIT)) return t;
    return vk_mem_type(bits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT, 0);
}
static void undo(vkpool_t *p) {
    if (p->mem) vkFreeMemory(V.dev, p->mem, NULL);
    if (p->buf) vkDestroyBuffer(V.dev, p->buf, NULL);
    p->mem = VK_NULL_HANDLE; p->buf = VK_NULL_HANDLE;
}
static int zc_dmabuf(vkpool_t *p, uint8_t *mem, uint64_t size, VkBufferUsageFlags usage) {
    VkExternalMemoryBufferCreateInfo eb = {VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_BUFFER_CREATE_INFO, NULL, VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT};
    VkBufferCreateInfo bi = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, &eb, 0, size, usage, VK_SHARING_MODE_EXCLUSIVE};
    if (vkCreateBuffer(V.dev, &bi, NULL, &p->buf)) { p->buf = VK_NULL_HANDLE; return -2; }
    VkMemoryRequirements req; vkGetBufferMemoryRequirements(V.dev, p->buf, &req);
    uint32_t t = host_type(req.memoryTypeBits); if (t == UINT32_MAX) { undo(p); return -2; }
    VkMemoryDedicatedAllocateInfo ded = {VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO, NULL, VK_NULL_HANDLE, p->buf};
    VkExportMemoryAllocateInfo exp = {VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO, &ded, VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT};
    VkMemoryAllocateInfo ai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, &exp, req.size, t};
    if (vkAllocateMemory(V.dev, &ai, NULL, &p->mem) || vkBindBufferMemory(V.dev, p->buf, p->mem, 0)) { undo(p); return -2; }
    VkMemoryGetFdInfoKHR gi = {VK_STRUCTURE_TYPE_MEMORY_GET_FD_INFO_KHR, NULL, p->mem, VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT};
    int fd = -1; if (vkGetMemoryFdKHR(V.dev, &gi, &fd) || fd < 0) { undo(p); return -1; }
    // the pool's current contents into the buffer through a temporary mapping, then the buffer's pages at the pool's address
    uint8_t *tmp = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (tmp == MAP_FAILED) { static int said; if (!said++) VKLOG("[RED] memory pools: the exported dma-buf cannot be mapped (%s)", strerror(errno)); close(fd); undo(p); return -1; }
    memcpy(tmp, mem, size); munmap(tmp, size);
    if (mmap(mem, size, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_FIXED, fd, 0) != mem) { close(fd); undo(p); return -1; }
    p->fd = fd; p->zc_kind = VKP_ZC_DMABUF; p->zero_copy = 1;
    static int said; if (!said++) VKLOG("memory pools: zero-copy through dma-bufs, memory type %u (%s)", t, V.mem.memoryTypes[t].propertyFlags & VK_MEMORY_PROPERTY_HOST_CACHED_BIT ? "CPU-cached" : "uncached");
    return 0;
}
static int zc_placed(vkpool_t *p, uint8_t *mem, uint64_t size, VkBufferUsageFlags usage) {
    if ((uintptr_t)mem % g_placed_align || size % g_placed_align) return -3;   // the driver places whole allocations at aligned addresses
    VkBufferCreateInfo bi = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, NULL, 0, size, usage, VK_SHARING_MODE_EXCLUSIVE};
    if (vkCreateBuffer(V.dev, &bi, NULL, &p->buf)) { p->buf = VK_NULL_HANDLE; return -2; }
    VkMemoryRequirements req; vkGetBufferMemoryRequirements(V.dev, p->buf, &req);
    // a whole-allocation placed map covers req.size: larger than the pool, it would cover the game's pages after it
    if (req.size != size) { undo(p); return -3; }
    uint32_t t = host_type(req.memoryTypeBits); if (t == UINT32_MAX) { undo(p); return -2; }
    VkMemoryAllocateInfo ai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, NULL, req.size, t};
    if (vkAllocateMemory(V.dev, &ai, NULL, &p->mem) || vkBindBufferMemory(V.dev, p->buf, p->mem, 0)) { undo(p); return -2; }
    void *m = NULL; if (vkMapMemory(V.dev, p->mem, 0, VK_WHOLE_SIZE, 0, &m)) { undo(p); return -1; }
    memcpy(m, mem, size); vkUnmapMemory(V.dev, p->mem);   // the contents first, through an ordinary mapping
    VkMemoryMapPlacedInfoEXT pl = {VK_STRUCTURE_TYPE_MEMORY_MAP_PLACED_INFO_EXT, NULL, mem};
    VkMemoryMapInfoKHR mi = {VK_STRUCTURE_TYPE_MEMORY_MAP_INFO_KHR, &pl, VK_MEMORY_MAP_PLACED_BIT_EXT, p->mem, 0, VK_WHOLE_SIZE};
    if (vkMapMemory2KHR(V.dev, &mi, &m) || m != mem) { undo(p); return -1; }   // a failed placed map leaves the range as it was
    p->zc_kind = VKP_ZC_PLACED; p->zero_copy = 1;
    static int said; if (!said++) VKLOG("memory pools: zero-copy through placed maps, memory type %u (%s)", t, V.mem.memoryTypes[t].propertyFlags & VK_MEMORY_PROPERTY_HOST_CACHED_BIT ? "CPU-cached" : "uncached");
    return 0;
}
int vkp_pool_zero_copy(vkpool_t *p, uint8_t *mem, uint64_t size, VkBufferUsageFlags usage) {
    p->fd = -1; p->zc_kind = VKP_ZC_NONE;
    int rc = -1;
    if (g_dmabuf && (rc = zc_dmabuf(p, mem, size, usage)) == 0) return 0;
    if (g_placed && (rc = zc_placed(p, mem, size, usage)) == 0) return 0;
    return rc;
}
int vkp_pool_remap_ro(vkpool_t *p) {
    if (p->zc_kind != VKP_ZC_DMABUF || p->fd < 0) return -1;   // a placed map cannot be mapped again without a moment of no access: defrag leaves it split
    void *at = (void *)(uintptr_t)p->lo;
    return mmap(at, p->hi - p->lo, PROT_READ, MAP_SHARED | MAP_FIXED, p->fd, 0) == at ? 0 : -1;
}
void vkp_pool_copy_back(vkpool_t *p) {   // the pages back to anonymous memory with the buffer's contents (the game may reuse the memory)
    uint64_t size = p->hi - p->lo; uint8_t *mem = (uint8_t *)(uintptr_t)p->lo;
    uint8_t *save = malloc(size); if (save) memcpy(save, mem, size);
    if (p->zc_kind == VKP_ZC_PLACED) {   // the driver's map ends first (to a reservation), or freeing the memory later would unmap whatever the game has there by then
        VkMemoryUnmapInfoKHR ui = {VK_STRUCTURE_TYPE_MEMORY_UNMAP_INFO_KHR, NULL, g_unmap_reserve ? VK_MEMORY_UNMAP_RESERVE_BIT_EXT : 0, p->mem};
        vkUnmapMemory2KHR(V.dev, &ui);
    }
    mmap(mem, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
    if (save) { memcpy(mem, save, size); free(save); }
}
void vkp_pool_release(vkpool_t *p) { if (p->zc_kind == VKP_ZC_DMABUF && p->fd >= 0) close(p->fd); p->fd = -1; }
