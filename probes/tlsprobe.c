// Is tpidr_el0[1] (glibc's tcbhead_t.private) left alone by glibc, and where does the executable's static TLS start?
#define _GNU_SOURCE
#include <dlfcn.h>
#include <locale.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <gnu/libc-version.h>

static __thread uint64_t first_tls = 0x1111;   // the executable's TLS block
static uint64_t **tp(void) { uint64_t **t; __asm__ volatile("mrs %0, tpidr_el0" : "=r"(t)); return t; }
static _Atomic int bad;
static void check(const char *where, void *want) { if (tp()[1] != want) { printf("CHANGED at %s: %p != %p\n", where, (void *)tp()[1], want); bad = 1; } }
static void on_sig(int s) { char b[32]; snprintf(b, sizeof b, "%d", s); (void)!write(-1, b, 1); }
static void *worker(void *a) {
    void *init = tp()[1];
    void *mark = (void *)(0x5A5A0000ull | (uintptr_t)a);
    tp()[1] = mark;
    for (int i = 0; i < 200000; i++) {
        void *p = malloc(1 + (i % 4096)); memset(p, 1, 1); free(p);
        if (i % 1000 == 0) { char b[128]; snprintf(b, sizeof b, "%f %s %ls", i * 1.5, "x", L"w"); strtod("1.5", NULL); localtime(&(time_t){i}); }
        if (i % 5000 == 0) { struct timespec ts = {0, 1000}; nanosleep(&ts, NULL); }
        if (i % 20000 == 0) { void *h = dlopen("libvulkan.so.1", RTLD_NOW); if (h) dlclose(h); }
        if (i % 10000 == 0) raise(SIGUSR1);
        if (i % 50000 == 0) { pthread_t t; pthread_create(&t, NULL, (void *(*)(void *))strlen, (void *)"x"); pthread_join(t, NULL); }
    }
    check("worker end", mark);
    return init;
}
int main(void) {
    printf("glibc %s\n", gnu_get_libc_version());
    printf("tp=%p tp[0](dtv)=%p tp[1]=%p  first executable TLS at tp%+td\n", (void *)tp(), (void *)tp()[0], (void *)tp()[1], (char *)&first_tls - (char *)tp());
    signal(SIGUSR1, on_sig); setlocale(LC_ALL, "C.UTF-8");
    void *mark = (void *)0xABCD0000ull; tp()[1] = mark;
    pthread_t th[16]; void *init[16];
    for (int i = 0; i < 16; i++) pthread_create(&th[i], NULL, worker, (void *)(uintptr_t)i);
    for (int i = 0; i < 16; i++) pthread_join(th[i], &init[i]);
    printf("new threads start with tp[1] = %p (thread 0) %p (thread 15)\n", init[0], init[15]);
    check("main end", mark);
    printf("%s\n", bad ? "FAIL: tp[1] was written" : "OK: tp[1] kept by glibc across malloc, stdio, locale, dlopen(libvulkan), signals, thread create/join");
    return bad;
}
