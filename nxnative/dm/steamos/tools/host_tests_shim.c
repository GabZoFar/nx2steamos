// host_tests.py: what a test links against on the Mac without building it (Apple's dm_tsd_* are macros over pthread keys; dm_opmode lives in hle_oe.c, which a touch
// test leaves out). Weak: a test that builds the real one takes it.
#include <pthread.h>
__attribute__((weak)) int dm_opmode(void) { return 0; }
__attribute__((weak)) void dm_tsd_set(pthread_key_t k, const void *v) { pthread_setspecific(k, v); }
__attribute__((weak)) void *dm_tsd_get(pthread_key_t k) { return pthread_getspecific(k); }
