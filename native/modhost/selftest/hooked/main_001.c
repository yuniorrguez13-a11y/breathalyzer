#include "cpu.h"
void f_7000001000(Cpu *c);
// Self-test only: made-up functions in the generator's shape (see main_000.c). Not game code.
// f_7000002000 ends in a tail jump; f_7000002020 has a cold resume copy (f_<addr>_r), as functions with jump tables
// do; f_7000cc7410 stores s0 at [x0 + 0xa4], like the setter examples/stamina_hook hooks.
static void f_7000002020_r(Cpu *c);
void f_7000002000(Cpu *c) {
    FN_ENTER(0x7000002000ull);
    c->x[1] = c->x[1] + 1;
    __attribute__((musttail)) return f_7000001000(c);
}
void f_7000002020(Cpu *c) {
    FN_ENTER(0x7000002020ull);
    if (c->x[2] == 0x7000002024ull) { c->pc = c->x[2]; __attribute__((musttail)) return f_7000002020_r(c); }
    c->x[0] = c->x[0] * 3;
    return;
}
__attribute__((cold, noinline, optnone)) static void f_7000002020_r(Cpu *c) {
    c->x[0] = 0;
    return;
}
void f_7000002030(Cpu *c) {
    FN_ENTER(0x7000002030ull);
    c->x[0] = 5;
    return;
}
void f_7000cc7410(Cpu *c) {
    FN_ENTER(0x7000cc7410ull);
    ST32(c->x[0] + 0xa4ull, (uint32_t)c->v[0]);
    return;
}
