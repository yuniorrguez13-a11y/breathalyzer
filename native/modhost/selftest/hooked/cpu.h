// Self-test only: a stand-in for Savage Wind's gamelib-src/cpu.h, just what the made-up "generated" files here use.
// The Cpu layout is the release's (x at 0, sp at 256, v at 512), so mods read registers the same way as in the game.
#pragma once
#include <stddef.h>
#include <stdint.h>

typedef struct __attribute__((aligned(16))) Cpu {
    uint64_t x[32];
    uint64_t sp, pc, nzcv, tpidr, tpidrro, excl_addr, excl_val;
    void *thread;
    uint64_t fpcr, fpsr;
    uint8_t _pad[512 - 336];
    __uint128_t v[32];
} Cpu;
_Static_assert(offsetof(Cpu, sp) == 256 && offsetof(Cpu, v) == 512, "Cpu layout");

typedef void (*GuestFn)(Cpu *);

#define FN_ENTER(a) ((void)(a))
#define ST32(a, v) (*(uint32_t *)(uintptr_t)(a) = (uint32_t)(v))
