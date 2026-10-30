// vkzc: zero-copy guest memory pools on Linux (SteamOS: Mesa Turnip; the dev container: lavapipe).
// nxnative's Metal backend wraps the game's own memory as GPU buffers; nx2android places AHardwareBuffer memory at the guest address (its probes/vkzc.c).
// Linux has no AHardwareBuffer, so this tries every way to make [ga, ga + size) GPU memory:
//   placed : VK_EXT_map_memory_placed: vkMapMemory2KHR with VK_MEMORY_MAP_PLACED_BIT_EXT at the guest address (MAP_FIXED semantics), unmapped back to a reservation
//   dmabuf : export the allocation as a dma-buf (VK_EXT_external_memory_dma_buf) and mmap(MAP_SHARED | MAP_FIXED) that fd at the guest address
//   opaque : the same with an OPAQUE_FD (on Mesa it is often a dma-buf too)
//   host   : VK_EXT_external_memory_host: import the guest's own anonymous memory (what the Metal backend does with newBufferWithBytesNoCopy)
//   remap  : vkMapMemory, then mremap(MREMAP_FIXED) the driver's mapping into the guest reservation
// For each: the CPU writes 64 MB at guest address S, the GPU copies S -> D, the CPU checks D; then the CPU rewrites S and the GPU copies again (no flush, as a
// game would). The CPU read / write speed through the mapping, against plain anonymous memory, tells a cached mapping from a write-combined one.
// Build on the device or in the arm64 dev container: cc -O1 vkzc.c -o vkzc -ldl   (or zig cc -target aarch64-linux-gnu.2.34 ...)
// Run: ./vkzc (every method, each in a process of its own) or ./vkzc <method>
#define _GNU_SOURCE
#define VK_NO_PROTOTYPES
#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <vulkan/vulkan.h>

#define VKF(n) static PFN_##n n
VKF(vkGetInstanceProcAddr); VKF(vkCreateInstance); VKF(vkEnumeratePhysicalDevices); VKF(vkGetPhysicalDeviceMemoryProperties); VKF(vkGetPhysicalDeviceQueueFamilyProperties);
VKF(vkGetPhysicalDeviceProperties2); VKF(vkGetPhysicalDeviceFeatures2); VKF(vkEnumerateDeviceExtensionProperties);
VKF(vkCreateDevice); VKF(vkGetDeviceProcAddr); VKF(vkGetDeviceQueue); VKF(vkCreateBuffer); VKF(vkAllocateMemory); VKF(vkBindBufferMemory); VKF(vkGetBufferMemoryRequirements);
VKF(vkCreateCommandPool); VKF(vkAllocateCommandBuffers); VKF(vkBeginCommandBuffer); VKF(vkEndCommandBuffer); VKF(vkCmdCopyBuffer); VKF(vkQueueSubmit); VKF(vkQueueWaitIdle);
VKF(vkCmdPipelineBarrier); VKF(vkMapMemory); VKF(vkGetMemoryFdKHR); VKF(vkResetCommandBuffer); VKF(vkMapMemory2KHR); VKF(vkUnmapMemory2KHR);
static PFN_vkGetMemoryHostPointerPropertiesEXT vkGetMemoryHostPointerPropertiesEXT;

static VkDevice dev; static VkQueue q; static VkCommandBuffer cb; static VkPhysicalDeviceMemoryProperties mp;
static const size_t POOL = 64UL << 20;
enum { M_PLACED, M_DMABUF, M_OPAQUE, M_HOST, M_REMAP, M_N };
static const char *MNAME[] = {"placed", "dmabuf", "opaque", "host", "remap"};
static int has[M_N];   // the extensions each method needs are enabled on the device
static VkDeviceMemory g_mem[2]; static int g_nmem;

static uint32_t pick_type(uint32_t bits, VkMemoryPropertyFlags want) {
    for (uint32_t t = 0; t < mp.memoryTypeCount; t++) if ((bits >> t & 1) && (mp.memoryTypes[t].propertyFlags & want) == want) return t;
    return UINT32_MAX;
}
static uint32_t pick_host_type(uint32_t bits) {   // cached first: the game reads its pools from the CPU
    uint32_t t = pick_type(bits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT);
    return t != UINT32_MAX ? t : pick_type(bits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
}
static const char *flags_str(VkMemoryPropertyFlags f) {
    static char b[96]; snprintf(b, sizeof b, "%s%s%s%s", f & 1 ? " DEVICE_LOCAL" : "", f & 2 ? " HOST_VISIBLE" : "", f & 4 ? " HOST_COHERENT" : "", f & 8 ? " HOST_CACHED" : "");
    return b;
}
static VkBuffer new_buf(VkExternalMemoryHandleTypeFlags ht) {
    VkExternalMemoryBufferCreateInfo e = {VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_BUFFER_CREATE_INFO, NULL, ht};
    VkBufferCreateInfo bi = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, ht ? &e : NULL, 0, POOL, VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT |
        VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_VERTEX_BUFFER_BIT | VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT, VK_SHARING_MODE_EXCLUSIVE};
    VkBuffer b; VkResult r = vkCreateBuffer(dev, &bi, NULL, &b); if (r) { printf("    vkCreateBuffer = %d\n", r); return VK_NULL_HANDLE; }
    return b;
}
// A GPU buffer whose CPU view is exactly [ga, ga + POOL); ga lies in a PROT_NONE reservation
static VkBuffer make_pool(int method, uint8_t *ga) {
    VkResult r; VkDeviceMemory mem;
    if (method == M_HOST) {
        if (mprotect(ga, POOL, PROT_READ | PROT_WRITE)) { printf("    mprotect: %s\n", strerror(errno)); return VK_NULL_HANDLE; }
        VkMemoryHostPointerPropertiesEXT hp = {VK_STRUCTURE_TYPE_MEMORY_HOST_POINTER_PROPERTIES_EXT};
        if ((r = vkGetMemoryHostPointerPropertiesEXT(dev, VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT, ga, &hp))) { printf("    host pointer properties = %d\n", r); return VK_NULL_HANDLE; }
        VkBuffer b = new_buf(VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT); if (!b) return b;
        VkMemoryRequirements req; vkGetBufferMemoryRequirements(dev, b, &req);
        uint32_t t = pick_host_type(hp.memoryTypeBits & req.memoryTypeBits); if (t == UINT32_MAX) t = pick_type(hp.memoryTypeBits & req.memoryTypeBits, 0);
        VkImportMemoryHostPointerInfoEXT imp = {VK_STRUCTURE_TYPE_IMPORT_MEMORY_HOST_POINTER_INFO_EXT, NULL, VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT, ga};
        VkMemoryAllocateInfo ai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, &imp, POOL, t};
        if ((r = vkAllocateMemory(dev, &ai, NULL, &mem))) { printf("    import host memory = %d\n", r); return VK_NULL_HANDLE; }
        printf("    imported the guest's own memory as type %u (%s)\n", t, flags_str(mp.memoryTypes[t].propertyFlags));
        vkBindBufferMemory(dev, b, mem, 0); return b;
    }
    VkExternalMemoryHandleTypeFlagBits ht = method == M_DMABUF ? VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT : method == M_OPAQUE ? VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT : 0;
    VkBuffer b = new_buf(ht); if (!b) return b;
    VkMemoryRequirements req; vkGetBufferMemoryRequirements(dev, b, &req);
    uint32_t t = pick_host_type(req.memoryTypeBits);
    if (t == UINT32_MAX) { printf("    no host-visible memory type\n"); return VK_NULL_HANDLE; }
    VkExportMemoryAllocateInfo ex = {VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO, NULL, ht};
    VkMemoryAllocateInfo ai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, ht ? &ex : NULL, req.size, t};
    if ((r = vkAllocateMemory(dev, &ai, NULL, &mem))) { printf("    vkAllocateMemory = %d\n", r); return VK_NULL_HANDLE; }
    printf("    memory type %u (%s)\n", t, flags_str(mp.memoryTypes[t].propertyFlags));
    vkBindBufferMemory(dev, b, mem, 0);
    if (method == M_PLACED) {
        VkMemoryMapPlacedInfoEXT pl = {VK_STRUCTURE_TYPE_MEMORY_MAP_PLACED_INFO_EXT, NULL, ga};
        VkMemoryMapInfoKHR mi = {VK_STRUCTURE_TYPE_MEMORY_MAP_INFO_KHR, &pl, VK_MEMORY_MAP_PLACED_BIT_EXT, mem, 0, VK_WHOLE_SIZE};
        void *p = NULL; if ((r = vkMapMemory2KHR(dev, &mi, &p))) { printf("    vkMapMemory2KHR(placed at %p) = %d\n", ga, r); return VK_NULL_HANDLE; }
        printf("    placed map at %p%s\n", p, p == ga ? "" : " (NOT the guest address)");
        if (p != ga) return VK_NULL_HANDLE;
        g_mem[g_nmem++ & 1] = mem;
    } else if (method == M_REMAP) {
        void *p; if ((r = vkMapMemory(dev, mem, 0, VK_WHOLE_SIZE, 0, &p))) { printf("    vkMapMemory = %d\n", r); return VK_NULL_HANDLE; }
        void *m = mremap(p, req.size, req.size, MREMAP_MAYMOVE | MREMAP_FIXED, ga);
        if (m == MAP_FAILED) { printf("    mremap %p -> %p failed: %s\n", p, ga, strerror(errno)); return VK_NULL_HANDLE; }
        printf("    driver mapping %p moved to %p\n", p, m);
    } else {
        VkMemoryGetFdInfoKHR gi = {VK_STRUCTURE_TYPE_MEMORY_GET_FD_INFO_KHR, NULL, mem, ht};
        int fd; if ((r = vkGetMemoryFdKHR(dev, &gi, &fd))) { printf("    vkGetMemoryFdKHR = %d\n", r); return VK_NULL_HANDLE; }
        void *m = mmap(ga, req.size, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_FIXED, fd, 0);
        if (m == MAP_FAILED) { printf("    mmap(%s fd %d) at %p failed: %s\n", MNAME[method], fd, ga, strerror(errno)); return VK_NULL_HANDLE; }
        printf("    %s fd %d mapped at %p\n", MNAME[method], fd, m);
    }
    return b;
}
static double now_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1e3 + t.tv_nsec / 1e6; }
static size_t gpu_copy_check(VkBuffer s, VkBuffer d, uint32_t *S, uint32_t *D, uint32_t seed, double *ms) {
    for (size_t i = 0; i < POOL / 4; i++) S[i] = (uint32_t)(i * 2654435761u) ^ seed;   // plain CPU stores, no flush: what a game does
    memset(D, 0, POOL);
    vkResetCommandBuffer(cb, 0);
    VkCommandBufferBeginInfo bbi = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO}; vkBeginCommandBuffer(cb, &bbi);
    VkBufferCopy reg = {0, 0, POOL}; vkCmdCopyBuffer(cb, s, d, 1, &reg);
    VkMemoryBarrier mb = {VK_STRUCTURE_TYPE_MEMORY_BARRIER, NULL, VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_HOST_READ_BIT};
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 1, &mb, 0, NULL, 0, NULL);
    vkEndCommandBuffer(cb);
    VkSubmitInfo si = {VK_STRUCTURE_TYPE_SUBMIT_INFO, NULL, 0, NULL, NULL, 1, &cb, 0, NULL};
    double t0 = now_ms(); vkQueueSubmit(q, 1, &si, VK_NULL_HANDLE); vkQueueWaitIdle(q); *ms = now_ms() - t0;
    size_t bad = 0; for (size_t i = 0; i < POOL / 4; i++) bad += D[i] != ((uint32_t)(i * 2654435761u) ^ seed);
    return bad;
}
static int has_ext(const VkExtensionProperties *e, uint32_t n, const char *name) { for (uint32_t i = 0; i < n; i++) if (!strcmp(e[i].extensionName, name)) return 1; return 0; }

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);   // a method that crashes still shows how far it got
    if (argc < 2) {   // every method in a process of its own: a method can take the driver down (remap moves the driver's own mapping away)
        for (int m = 0; m < M_N; m++) {
            pid_t pid = fork();
            if (!pid) { setenv("VKZC_QUIET", m ? "1" : "", 1); execl("/proc/self/exe", argv[0], MNAME[m], (char *)NULL); _exit(127); }
            int st = 0; waitpid(pid, &st, 0);
            if (WIFSIGNALED(st)) printf("RESULT %s: CRASHED (signal %d)\n", MNAME[m], WTERMSIG(st));
        }
        return 0;
    }
    int quiet = getenv("VKZC_QUIET") && *getenv("VKZC_QUIET");
    void *lib = dlopen("libvulkan.so.1", RTLD_NOW); if (!lib) lib = dlopen("libvulkan.so", RTLD_NOW);
    if (!lib) { printf("no libvulkan.so.1: %s\n", dlerror()); return 1; }
    vkGetInstanceProcAddr = (PFN_vkGetInstanceProcAddr)dlsym(lib, "vkGetInstanceProcAddr");
    VkInstance inst = VK_NULL_HANDLE;
#define IGET(n) n = (PFN_##n)vkGetInstanceProcAddr(inst, #n)
    IGET(vkCreateInstance);
    VkApplicationInfo app = {VK_STRUCTURE_TYPE_APPLICATION_INFO, NULL, "vkzc", 1, NULL, 0, VK_API_VERSION_1_3};
    VkInstanceCreateInfo ici = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, 0, &app};
    if (vkCreateInstance(&ici, NULL, &inst)) { printf("vkCreateInstance failed\n"); return 1; }
    IGET(vkEnumeratePhysicalDevices); IGET(vkGetPhysicalDeviceMemoryProperties); IGET(vkGetPhysicalDeviceQueueFamilyProperties); IGET(vkCreateDevice); IGET(vkGetDeviceProcAddr);
    IGET(vkGetPhysicalDeviceProperties2); IGET(vkGetPhysicalDeviceFeatures2); IGET(vkEnumerateDeviceExtensionProperties);
    uint32_t n = 1; VkPhysicalDevice pd; vkEnumeratePhysicalDevices(inst, &n, &pd); if (!n) { printf("no physical device\n"); return 1; }
    vkGetPhysicalDeviceMemoryProperties(pd, &mp);
    VkPhysicalDeviceMapMemoryPlacedPropertiesEXT plp = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAP_MEMORY_PLACED_PROPERTIES_EXT};
    VkPhysicalDeviceDriverProperties drv = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES, &plp};
    VkPhysicalDeviceProperties2 props = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, &drv}; vkGetPhysicalDeviceProperties2(pd, &props);
    if (!quiet) printf("%s, %s %s, page size %ld\n", props.properties.deviceName, drv.driverName, drv.driverInfo, sysconf(_SC_PAGESIZE));
    for (uint32_t i = 0; i < mp.memoryTypeCount && !quiet; i++) printf("  memory type %u (heap %u):%s\n", i, mp.memoryTypes[i].heapIndex, flags_str(mp.memoryTypes[i].propertyFlags));
    uint32_t qn = 16; VkQueueFamilyProperties qp[16]; vkGetPhysicalDeviceQueueFamilyProperties(pd, &qn, qp);
    uint32_t qf = 0; while (qf < qn && !(qp[qf].queueFlags & VK_QUEUE_GRAPHICS_BIT)) qf++;

    // enable what the device has; each method runs only with its extensions
    uint32_t ne = 0; vkEnumerateDeviceExtensionProperties(pd, NULL, &ne, NULL);
    VkExtensionProperties *ex = calloc(ne, sizeof *ex); vkEnumerateDeviceExtensionProperties(pd, NULL, &ne, ex);
    static const char *WANT[] = {"VK_KHR_external_memory_fd", "VK_EXT_external_memory_dma_buf", "VK_EXT_external_memory_host", "VK_KHR_map_memory2", "VK_EXT_map_memory_placed"};
    const char *on[8]; uint32_t non = 0;
    for (unsigned i = 0; i < sizeof WANT / sizeof *WANT; i++) { int h = has_ext(ex, ne, WANT[i]); if (!quiet) printf("  %s %s\n", h ? "yes" : "NO ", WANT[i]); if (h) on[non++] = WANT[i]; }
    VkPhysicalDeviceMapMemoryPlacedFeaturesEXT plf = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAP_MEMORY_PLACED_FEATURES_EXT};
    VkPhysicalDeviceFeatures2 f2 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, has_ext(ex, ne, "VK_EXT_map_memory_placed") ? &plf : NULL}; vkGetPhysicalDeviceFeatures2(pd, &f2);
    if (f2.pNext && !quiet) printf("  memoryMapPlaced %d  memoryMapRangePlaced %d  memoryUnmapReserve %d  minPlacedMemoryMapAlignment %llu\n", plf.memoryMapPlaced, plf.memoryMapRangePlaced,
                         plf.memoryUnmapReserve, (unsigned long long)plp.minPlacedMemoryMapAlignment);
    has[M_PLACED] = plf.memoryMapPlaced && has_ext(ex, ne, "VK_KHR_map_memory2");
    has[M_DMABUF] = has_ext(ex, ne, "VK_EXT_external_memory_dma_buf") && has_ext(ex, ne, "VK_KHR_external_memory_fd");
    has[M_OPAQUE] = has_ext(ex, ne, "VK_KHR_external_memory_fd");
    has[M_HOST] = has_ext(ex, ne, "VK_EXT_external_memory_host");
    has[M_REMAP] = 1;
    float prio = 1; VkDeviceQueueCreateInfo qci = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, NULL, 0, qf, 1, &prio};
    VkPhysicalDeviceMapMemoryPlacedFeaturesEXT plon = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAP_MEMORY_PLACED_FEATURES_EXT, NULL, plf.memoryMapPlaced, 0, plf.memoryUnmapReserve};
    VkDeviceCreateInfo dci = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, has[M_PLACED] ? &plon : NULL, 0, 1, &qci, 0, NULL, non, on};
    VkResult r = vkCreateDevice(pd, &dci, NULL, &dev); if (r) { printf("vkCreateDevice = %d\n", r); return 1; }
#define DGET(n) n = (PFN_##n)vkGetDeviceProcAddr(dev, #n)
    DGET(vkGetDeviceQueue); DGET(vkCreateBuffer); DGET(vkAllocateMemory); DGET(vkBindBufferMemory); DGET(vkGetBufferMemoryRequirements); DGET(vkCreateCommandPool);
    DGET(vkAllocateCommandBuffers); DGET(vkBeginCommandBuffer); DGET(vkEndCommandBuffer); DGET(vkCmdCopyBuffer); DGET(vkQueueSubmit); DGET(vkQueueWaitIdle);
    DGET(vkCmdPipelineBarrier); DGET(vkMapMemory); DGET(vkGetMemoryFdKHR); DGET(vkResetCommandBuffer); DGET(vkMapMemory2KHR); DGET(vkUnmapMemory2KHR); DGET(vkGetMemoryHostPointerPropertiesEXT);
    vkGetDeviceQueue(dev, qf, 0, &q);
    VkCommandPoolCreateInfo pci = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, NULL, VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, qf}; VkCommandPool pool; vkCreateCommandPool(dev, &pci, NULL, &pool);
    VkCommandBufferAllocateInfo cai = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, NULL, pool, VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1}; vkAllocateCommandBuffers(dev, &cai, &cb);

    // the guest address space: one 8 GB PROT_NONE reservation, as nxnative's host makes
    uint8_t *as = mmap(NULL, 8UL << 30, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (as == MAP_FAILED) { printf("8 GB reservation failed: %s\n", strerror(errno)); return 1; }
    if (!quiet) printf("guest reservation %p + 8 GB\n", as);
    double base_r, base_w;
    {   // baseline: ordinary anonymous memory, same loops, to tell whether a pool mapping is CPU-cached
        uint64_t *a = mmap(NULL, POOL, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0); memset(a, 1, POOL);
        double c0 = now_ms(); volatile uint64_t sum = 0; for (size_t i = 0; i < POOL / 8; i++) sum += a[i]; base_r = now_ms() - c0;
        c0 = now_ms(); for (size_t i = 0; i < POOL / 8; i++) ((volatile uint64_t *)a)[i] = i; base_w = now_ms() - c0;
        if (!quiet) printf("baseline anonymous memory: read 64 MB %.1f ms, write %.1f ms\n", base_r, base_w);
    }
    for (int m = 0; m < M_N; m++) {
        if (argc > 1 && strcmp(argv[1], MNAME[m])) continue;
        uint8_t *S = as + (1UL << 30) * (m + 1), *D = S + POOL + 0x10000;   // 4 KB-aligned, not size-aligned, like a game's heap allocation
        printf("== %s (guest pools at %p and %p)\n", MNAME[m], S, D);
        if (!has[m]) { printf("RESULT %s: not available (extension or feature missing)\n", MNAME[m]); continue; }
        g_nmem = 0;
        VkBuffer bs = make_pool(m, S), bd = bs ? make_pool(m, D) : VK_NULL_HANDLE;
        if (!bd) { printf("RESULT %s: FAILED to set up\n", MNAME[m]); continue; }
        double t1, t2; size_t bad1 = gpu_copy_check(bs, bd, (uint32_t *)S, (uint32_t *)D, 0, &t1), bad2 = gpu_copy_check(bs, bd, (uint32_t *)S, (uint32_t *)D, 0xa5a5a5a5, &t2);
        double c0 = now_ms(); volatile uint64_t sum = 0; for (size_t i = 0; i < POOL / 8; i++) sum += ((uint64_t *)S)[i]; double cr = now_ms() - c0;
        c0 = now_ms(); for (size_t i = 0; i < POOL / 8; i++) ((volatile uint64_t *)S)[i] = i; double cw = now_ms() - c0;
        printf("    pass 1: %zu wrong words, GPU copy %.2f ms; pass 2 (rewrite, no flush): %zu wrong; mapping: read 64 MB %.1f ms (%.1fx anon), write %.1f ms (%.1fx)\n",
               bad1, t1, bad2, cr, cr / base_r, cw, cw / base_w);
        if (m == M_PLACED && plf.memoryUnmapReserve) {   // finalize: the range must go back to a reservation, never to a hole another mapping could take
            VkMemoryUnmapInfoKHR ui = {VK_STRUCTURE_TYPE_MEMORY_UNMAP_INFO_KHR, NULL, VK_MEMORY_UNMAP_RESERVE_BIT_EXT, g_mem[0]};
            r = vkUnmapMemory2KHR(dev, &ui);
            void *t = mmap(S, POOL, PROT_READ, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
            printf("    unmap with VK_MEMORY_UNMAP_RESERVE_BIT_EXT = %d; the range is %s\n", r, t == MAP_FAILED && errno == EEXIST ? "still reserved (good)" : "free (a hole)");
        }
        printf("RESULT %s: %s%s\n", MNAME[m], bad1 || bad2 ? "data mismatch" : "WORKS - the guest pointer is GPU memory, zero copy",
               !(bad1 || bad2) && cr > 4 * base_r ? " (but CPU reads are slow: an uncached / write-combined mapping)" : "");
    }
    return 0;
}
