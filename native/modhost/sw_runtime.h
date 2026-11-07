// Declarations of the Savage Wind runtime that modhost uses: the game code library's tables and the functions
// savagewind.exe exports (through the savagewind.dll.a import library that ships with the release).
//
// Copied from Savage Wind Recomp 0.4.1-alpha.4 (commit 76e61af): runtime/gamelib.h, runtime/control.h, runtime/rt.h.
// Savage Wind Recomp is MIT licensed:
//   Copyright (c) 2026 Revivedx and the Savage Wind Recomp contributors
//   Copyright (c) 2026 Peiikai (LAS Recomp, on which this project is built)
// Only declarations are copied, no code. Mods do not include this file: they use swmod.h.
//
// These are runtime internals, not a stable API: they are only known to match 0.4.1-alpha.4. Rebuild modhost for any
// other Savage Wind version and check these against its sources first.
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define SW_IMPORT __declspec(dllimport)

struct Cpu;                                  // gamelib-src/cpu.h
typedef void (*GuestFn)(struct Cpu *);       // a recompiled guest function (cpu.h)
typedef void (*HostFn)(struct Cpu *);        // a host function standing in for one (rt.h)

// ---- runtime/gamelib.h
#define GAMELIB_ABI 3
#define GAMELIB_ENTRY "swgame_tables"
#define GAMELIB_EXPORT __declspec(dllexport)

typedef struct {
    const char *name;        // its exefs file: main, subsdk0..9, sdk
    uint64_t base, size;     // guest address, image size (page-rounded)
    const char *build_id;    // NSO build id (64 hex digits) of the file the code was generated from
    const uint64_t *addrs;   // sorted guest addresses
    const GuestFn *fns;      // the functions at them
    const uint64_t *count;
} GameLibModule;

typedef struct {
    const char *name;
    uint64_t addr;
} GameLibSite;

typedef struct {
    uint32_t abi;    // GAMELIB_ABI
    const char *id;  // GAMELIB_ID
    const GameLibModule *modules;  // in load order, main first
    const uint32_t *nmodules;
    const char *version;  // the game version ("1.9.0")
    const GameLibSite *sites;
    const uint32_t *nsites;
} GameLibTables;

typedef const GameLibTables *(*GameLibEntry)(void);

SW_IMPORT const char *gamelib_id(void);  // the id a library for this CPU must have

// ---- runtime/control.h
typedef struct ControlReply ControlReply;
typedef void (*ControlFn)(int argc, char **argv, ControlReply *r);
#define CONTROL_PLAYER 1u

typedef struct {
    const char *name;
    const char *args;
    const char *help;
    ControlFn fn;
    unsigned flags;
} ControlCmd;

SW_IMPORT void control_register(const ControlCmd *cmds, int count);
SW_IMPORT void control_field_str(ControlReply *r, const char *key, const char *value);
SW_IMPORT void control_field_int(ControlReply *r, const char *key, int64_t value);
SW_IMPORT void control_field_num(ControlReply *r, const char *key, double value);
SW_IMPORT void control_field_bool(ControlReply *r, const char *key, bool value);
SW_IMPORT void control_text(ControlReply *r, const char *fmt, ...);
SW_IMPORT void control_fail(ControlReply *r, const char *fmt, ...);
SW_IMPORT bool control_enabled(void);

typedef struct {
    char *text;  // what the console would print: lines separated by \n (free with control_console_free)
    char *copy;
    bool failed;
    bool exit_after;
} ControlConsoleResult;
// Run one command line now, on the calling thread. console=false: with the socket's rights (every command).
SW_IMPORT void control_exec_line(const char *line, bool console, ControlConsoleResult *out);
SW_IMPORT void control_console_free(ControlConsoleResult *r);

// ---- runtime/rt.h
SW_IMPORT GuestFn dispatch_lookup(uint64_t addr);
SW_IMPORT void hle_override(uint64_t guest_addr, HostFn fn, const char *name);
SW_IMPORT void guest_call(struct Cpu *c, uint64_t addr);
SW_IMPORT struct Cpu *kernel_current_cpu(void);
