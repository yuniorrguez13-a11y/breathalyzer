#include "cpu.h"
void f_7000001000(Cpu *c);
void f_7000001010(Cpu *c);
void f_7000001020(Cpu *c);
void f_7000002000(Cpu *c);
void f_7000002020(Cpu *c);
void f_7000002030(Cpu *c);
void f_7000cc7410(Cpu *c);
const uint64_t main_fn_addrs[] = {
0x7000001000ull,
0x7000001010ull,
0x7000001020ull,
0x7000002000ull,
0x7000002020ull,
0x7000002030ull,
0x7000cc7410ull};
const GuestFn main_fn_ptrs[] = {
f_7000001000,
f_7000001010,
f_7000001020,
f_7000002000,
f_7000002020,
f_7000002030,
f_7000cc7410};
const uint64_t main_fn_count = 7;
