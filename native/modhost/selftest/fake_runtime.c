// Self-test only: a stand-in for savagewind.exe. It is built as a DLL named savagewind.exe that exports the runtime
// functions modhost imports, so modhost.dll can be loaded and driven outside the game (tools/build_native.py
// selftest, which loads it with Python's ctypes). Nothing here comes from the game.
//
// Like Savage Wind it has the built-in commands `help` (player safe) and `ping` (developer only), skips a duplicate
// name or a full table in control_register, and its `help` prints one "<name> <args>  <help>" line per command (all
// of them for the socket, only the player safe ones for the console). Its table is tiny (9 commands, or
// FAKE_MAX_CMDS) so that the self-test can fill it. Words are split like Savage Wind's: double quotes keep spaces in
// one word, and \" and \\ work inside them.
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "../platform_min.h"

#define EXPORT __declspec(dllexport)
#define CMDS_CAP 64
#define MAX_CMDS 9  // help, ping, mods, modload, modunload, modreload, hello, probe, probe_more: probe's next is refused
#define MAX_OVERRIDES 64
#define MAX_CALLS 16

struct Cpu;
typedef void (*Fn)(struct Cpu *);
typedef struct Reply Reply;
typedef void (*CmdFn)(int argc, char **argv, Reply *r);
typedef struct {
    const char *name, *args, *help;
    CmdFn fn;
    unsigned flags;
} Cmd;
typedef struct {  // Savage Wind's ControlConsoleResult
    char *text, *copy;
    bool failed, exit_after;
} ConsoleResult;

struct Reply {
    char data[16384];
    size_t len;
    char text[8192];  // the control_text lines, separated by \n
    size_t text_len;
    char error[512];
    bool failed;
};

static Cmd g_cmds[CMDS_CAP];
static int g_ncmds;
static int g_max_cmds = MAX_CMDS;
static bool g_console;  // the command running was started by the console
static struct {
    uint64_t addr;
    Fn fn;
    const char *name;
} g_overrides[MAX_OVERRIDES];
static int g_noverrides;
static struct {
    struct Cpu *cpu;
    uint64_t addr;
} g_calls[MAX_CALLS];
static int g_ncalls;

static void put(Reply *r, const char *s) {
    size_t n = strlen(s);
    if (r->len + n + 1 >= sizeof r->data) return;
    memcpy(r->data + r->len, s, n + 1);
    r->len += n;
}
static void put_string(Reply *r, const char *s) {
    put(r, "\"");
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        char c[8];
        if (*p == '"' || *p == '\\') snprintf(c, sizeof c, "\\%c", *p);
        else if (*p < 0x20) snprintf(c, sizeof c, "\\u%04x", *p);
        else snprintf(c, sizeof c, "%c", *p);
        put(r, c);
    }
    put(r, "\"");
}
static void key(Reply *r, const char *k) {
    if (r->len) put(r, ",");
    put_string(r, k);
    put(r, ":");
}

EXPORT const char *gamelib_id(void) { return "selftest-id"; }

static void add_cmds(const Cmd *cmds, int count) {
    for (int i = 0; i < count; i++) {
        if (!cmds[i].name || !*cmds[i].name || !cmds[i].fn) continue;
        bool dup = false;
        for (int k = 0; k < g_ncmds; k++) dup |= !strcmp(g_cmds[k].name, cmds[i].name);
        if (!dup && g_ncmds < g_max_cmds) g_cmds[g_ncmds++] = cmds[i];
    }
}

EXPORT void control_field_str(Reply *r, const char *k, const char *v) { key(r, k); put_string(r, v); }
EXPORT void control_field_int(Reply *r, const char *k, int64_t v) {
    char s[32];
    snprintf(s, sizeof s, "%lld", (long long)v);
    key(r, k);
    put(r, s);
}
EXPORT void control_field_num(Reply *r, const char *k, double v) {
    char s[64];
    snprintf(s, sizeof s, "%.17g", v);
    key(r, k);
    put(r, s);
}
EXPORT void control_field_bool(Reply *r, const char *k, bool v) { key(r, k); put(r, v ? "true" : "false"); }
EXPORT void control_text(Reply *r, const char *fmt, ...) {
    char line[1024];
    sw_va_list ap;
    sw_va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    sw_va_end(ap);
    size_t n = strlen(line), sep = r->text_len ? 1 : 0;
    if (r->text_len + sep + n + 1 > sizeof r->text) return;
    if (sep) r->text[r->text_len++] = '\n';
    memcpy(r->text + r->text_len, line, n + 1);
    r->text_len += n;
}
EXPORT void control_fail(Reply *r, const char *fmt, ...) {
    if (r->failed) return;
    r->failed = true;
    sw_va_list ap;
    sw_va_start(ap, fmt);
    vsnprintf(r->error, sizeof r->error, fmt, ap);
    sw_va_end(ap);
}
EXPORT bool control_enabled(void) { return true; }

static void cmd_help(int argc, char **argv, Reply *r) {
    (void)argc;
    (void)argv;
    for (int i = 0; i < g_ncmds; i++) {
        if (g_console && !(g_cmds[i].flags & 1u)) continue;
        char usage[160];
        snprintf(usage, sizeof usage, "%s%s%s", g_cmds[i].name, *g_cmds[i].args ? " " : "", g_cmds[i].args);
        control_text(r, "%-12s  %s", usage, g_cmds[i].help);
    }
}
static void cmd_ping(int argc, char **argv, Reply *r) {
    (void)argc;
    (void)argv;
    control_field_bool(r, "pong", true);
}

static void builtins(void) {
    static bool done;
    if (done) return;
    done = true;
    const char *max = getenv("FAKE_MAX_CMDS");
    if (max && atoi(max) > 0) g_max_cmds = atoi(max) < CMDS_CAP ? atoi(max) : CMDS_CAP;
    static const Cmd cmds[] = {
        {"help", "", "List the commands", cmd_help, 1u},
        {"ping", "", "Check that the game answers", cmd_ping, 0},
    };
    add_cmds(cmds, 2);
}

EXPORT void control_register(const Cmd *cmds, int count) {
    builtins();
    add_cmds(cmds, count);
}

// Split a line into words on spaces (in place), with double quotes as in Savage Wind's tokenize; returns argc.
static int split(char *buf, char **argv, int max) {
    int argc = 0;
    char *src = buf, *dst = buf;
    while (*src && argc < max) {
        while (*src == ' ') src++;
        if (!*src) break;
        argv[argc++] = dst;
        bool quoted = false;
        while (*src && (quoted || *src != ' ')) {
            if (*src == '"') {
                quoted = !quoted;
                src++;
            } else if (quoted && *src == '\\' && (src[1] == '"' || src[1] == '\\')) {
                src++;
                *dst++ = *src++;
            } else {
                *dst++ = *src++;
            }
        }
        if (*src) src++;
        *dst++ = 0;
    }
    return argc;
}

static void execute(int argc, char **argv, bool console, Reply *r) {
    CmdFn fn = NULL;
    for (int i = 0; i < g_ncmds && !fn; i++)
        if (!strcmp(g_cmds[i].name, argv[0]) && (!console || (g_cmds[i].flags & 1u))) fn = g_cmds[i].fn;
    if (!fn) {
        control_fail(r, "Unknown command: %s", argv[0]);
        return;
    }
    g_console = console;
    fn(argc, argv, r);
    g_console = false;
}

static char *dup_str(const char *s) {
    size_t n = strlen(s);
    char *d = malloc(n + 1);
    if (d) memcpy(d, s, n + 1);
    return d;
}

EXPORT void control_exec_line(const char *line, bool console, ConsoleResult *out) {
    builtins();
    static Reply r;  // (not reentrant: modhost doesn't run a line from inside one)
    memset(&r, 0, sizeof r);
    memset(out, 0, sizeof *out);
    char buf[512], *argv[16];
    snprintf(buf, sizeof buf, "%s", line);
    int argc = split(buf, argv, 16);
    if (!argc) control_fail(&r, "Empty command");
    else execute(argc, argv, console, &r);
    out->failed = r.failed;
    out->text = dup_str(r.failed ? r.error : r.text_len ? r.text : r.len ? r.data : "ok");
}
EXPORT void control_console_free(ConsoleResult *r) {
    free(r->text);
    free(r->copy);
    memset(r, 0, sizeof *r);
}

EXPORT Fn dispatch_lookup(uint64_t addr) {
    (void)addr;
    return NULL;  // (the real one is empty until the game boots: modhost must not need it)
}
EXPORT void hle_override(uint64_t addr, Fn fn, const char *name) {
    if (g_noverrides < MAX_OVERRIDES) {
        g_overrides[g_noverrides].addr = addr;
        g_overrides[g_noverrides].fn = fn;
        g_overrides[g_noverrides++].name = name;
    }
}
EXPORT void guest_call(struct Cpu *c, uint64_t addr) {
    if (g_ncalls < MAX_CALLS) {
        g_calls[g_ncalls].cpu = c;
        g_calls[g_ncalls++].addr = addr;
    }
}
EXPORT struct Cpu *kernel_current_cpu(void) { return NULL; }

// Run one command line (words split on spaces) and write the JSON reply the socket would send, plus "text": the
// control_text lines. "#overrides" lists the hle_override calls instead, "#guest_calls" the guest_call calls and
// "#commands" the registered command names. "#call <addr> [x0] [x1]" calls the override the dispatcher would pick for
// addr (the first one for it, as in Savage Wind) on a fresh Cpu and answers its x0.
EXPORT int fake_run(const char *line, char *out, int cap) {
    builtins();
    static Reply r, e;
    memset(&r, 0, sizeof r);
    memset(&e, 0, sizeof e);
    char buf[512], *argv[16];
    snprintf(buf, sizeof buf, "%s", line);
    int argc = split(buf, argv, 16);
    if (!argc) return -1;
    if (!strcmp(argv[0], "#overrides")) {
        control_field_int(&r, "count", g_noverrides);
        for (int i = 0; i < g_noverrides; i++) {
            char k[32];
            snprintf(k, sizeof k, "%d.addr", i);
            control_field_int(&r, k, (int64_t)g_overrides[i].addr);
            snprintf(k, sizeof k, "%d.name", i);
            control_field_str(&r, k, g_overrides[i].name);
        }
    } else if (!strcmp(argv[0], "#guest_calls")) {
        control_field_int(&r, "count", g_ncalls);
        for (int i = 0; i < g_ncalls; i++) {
            char k[32];
            snprintf(k, sizeof k, "%d.cpu", i);
            control_field_int(&r, k, (int64_t)(intptr_t)g_calls[i].cpu);
            snprintf(k, sizeof k, "%d.addr", i);
            control_field_int(&r, k, (int64_t)g_calls[i].addr);
        }
    } else if (!strcmp(argv[0], "#commands")) {
        for (int i = 0; i < g_ncmds; i++) {
            char k[32];
            snprintf(k, sizeof k, "%d", i);
            control_field_str(&r, k, g_cmds[i].name);
        }
    } else if (!strcmp(argv[0], "#call") && argc > 1) {
        uint64_t addr = strtoull(argv[1], NULL, 0);
        int found = -1;
        for (int i = 0; i < g_noverrides && found < 0; i++)
            if (g_overrides[i].addr == addr) found = i;
        if (found < 0) {
            control_fail(&r, "no override for %s", argv[1]);
        } else {
            static uint64_t cpu[128] __attribute__((aligned(16)));  // (the release's Cpu layout is 1 KiB)
            memset(cpu, 0, sizeof cpu);
            cpu[0] = argc > 2 ? strtoull(argv[2], NULL, 0) : 0;
            cpu[1] = argc > 3 ? strtoull(argv[3], NULL, 0) : 0;
            g_overrides[found].fn((struct Cpu *)(void *)cpu);
            control_field_int(&r, "x0", (int64_t)cpu[0]);
            control_field_str(&r, "name", g_overrides[found].name);
        }
    } else {
        execute(argc, argv, false, &r);
    }
    if (r.failed) {
        put_string(&e, r.error);
        return snprintf(out, (size_t)cap, "{\"ok\":false,\"error\":%s}", e.data);
    }
    put_string(&e, r.text);
    return snprintf(out, (size_t)cap, "{\"ok\":true,\"data\":{%s},\"text\":%s}", r.data, e.data);
}
