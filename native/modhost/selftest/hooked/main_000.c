#include "cpu.h"
void f_7000001000(Cpu *c);
// Self-test only: made-up functions in the shape of Savage Wind's generator output (tools/gen.py: one C function
// per guest function, called directly by name). Not game code. build_native.py selftest runs tools/gen_hooks.py on
// a copy, then compiles it into a fake game library.
void f_7000001000(Cpu *c) {
    FN_ENTER(0x7000001000ull);
    c->x[0] = c->x[0] + c->x[1];
    return;
}
void f_7000001010(Cpu *c) {
    FN_ENTER(0x7000001010ull);
    c->x[30] = 0x7000001018ull; f_7000001000(c);
    c->x[0] = c->x[0] * 2;
    return;
}
void f_7000001020(Cpu *c) {
    FN_ENTER(0x7000001020ull);
    c->x[0] = 77;
    return;
}
