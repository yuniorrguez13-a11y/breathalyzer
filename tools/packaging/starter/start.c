/* Start Breathalyzer.exe: opens Breathalyzer with the Python that comes in its own app\runtime folder, so players
   need no Python install (docs/launcher.md).

   It starts  app\runtime\pythonw.exe -I app\tools\launcher.py  (app\tools\modman_gui.py while there is no
   launcher.py), passes on its own arguments, and ends. Nothing else: no network, no registry, no admin rights, no
   files written, no C runtime (only kernel32 and user32).

   Built by tools/packaging/build_starter.py with the zig that ships with Savage Wind: the same bytes every build,
   and tools/packaging/starter.lock.json holds their SHA-256 (make_release.py checks it). */
#include <stddef.h>

/* The few Windows declarations it needs (the zig that ships with Savage Wind has no windows.h). */
typedef wchar_t WCHAR;
typedef unsigned long DWORD;
typedef int BOOL;
typedef unsigned int UINT;
typedef void *HANDLE;
typedef size_t SIZE_T;

typedef struct {
    DWORD cb;
    WCHAR *lpReserved, *lpDesktop, *lpTitle;
    DWORD dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
    unsigned short wShowWindow, cbReserved2;
    unsigned char *lpReserved2;
    HANDLE hStdInput, hStdOutput, hStdError;
} STARTUPINFOW;

typedef struct {
    HANDLE hProcess, hThread;
    DWORD dwProcessId, dwThreadId;
} PROCESS_INFORMATION;

#define IMPORT __declspec(dllimport)
IMPORT DWORD GetModuleFileNameW(HANDLE module, WCHAR *name, DWORD size);
IMPORT DWORD GetFileAttributesW(const WCHAR *path);
IMPORT WCHAR *GetCommandLineW(void);
IMPORT BOOL CreateProcessW(const WCHAR *app, WCHAR *cmd, void *process_attrs, void *thread_attrs, BOOL inherit,
                           DWORD flags, void *env, const WCHAR *dir, STARTUPINFOW *si, PROCESS_INFORMATION *pi);
IMPORT BOOL CloseHandle(HANDLE h);
IMPORT DWORD GetLastError(void);
IMPORT __attribute__((noreturn)) void ExitProcess(UINT code);
IMPORT int MessageBoxW(HANDLE window, const WCHAR *text, const WCHAR *caption, UINT type);
IMPORT BOOL AllowSetForegroundWindow(DWORD pid);
IMPORT int wsprintfW(WCHAR *out, const WCHAR *format, ...);

#define FALSE 0
#define INVALID_FILE_ATTRIBUTES 0xFFFFFFFFul
#define FILE_ATTRIBUTE_DIRECTORY 0x10ul
#define MB_OK 0x0u
#define MB_ICONWARNING 0x30u
#define MB_SETFOREGROUND 0x10000u

#define BUF 32768  /* the longest path Windows has, in characters */

static WCHAR folder[BUF], app[BUF], python[BUF], script[BUF], cmd[3 * BUF], text[1024];
static STARTUPINFOW si;
static PROCESS_INFORMATION pi;

static SIZE_T length(const WCHAR *s)
{
    SIZE_T n = 0;
    while (s[n])
        n++;
    return n;
}

/* Copy s to d (with its ending 0); returns where the 0 went, so calls can be chained. */
static WCHAR *put(WCHAR *d, const WCHAR *s)
{
    while (*s)
        *d++ = *s++;
    *d = 0;
    return d;
}

static int is_file(const WCHAR *path)
{
    DWORD a = GetFileAttributesW(path);
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

static void say(const WCHAR *message)
{
    MessageBoxW(NULL, message, L"Breathalyzer", MB_OK | MB_ICONWARNING | MB_SETFOREGROUND);
}

/* What came after this program's own name on its command line. */
static const WCHAR *own_args(void)
{
    const WCHAR *c = GetCommandLineW();
    if (*c == L'"') {
        c++;
        while (*c && *c != L'"')
            c++;
        if (*c)
            c++;
    } else {
        while (*c && *c != L' ' && *c != L'\t')
            c++;
    }
    while (*c == L' ' || *c == L'\t')
        c++;
    return c;
}

static const WCHAR UNPACK[] =
    L"Breathalyzer can't find its files.\n\n"
    L"Unpack the whole zip first: right-click it, choose Extract All, then open Start Breathalyzer in the new "
    L"folder.";

void start(void)
{
    DWORD n = GetModuleFileNameW(NULL, folder, BUF);
    WCHAR *slash = NULL, *p, *end;
    const WCHAR *args;

    if (n == 0 || n >= BUF - 64) {
        say(L"Breathalyzer's folder has a path that is too long. Move the Breathalyzer folder to a shorter place, "
            L"like Documents.");
        ExitProcess(1);
    }
    for (p = folder; *p; p++)
        if (*p == L'\\' || *p == L'/')
            slash = p;
    if (!slash) {
        say(UNPACK);
        ExitProcess(1);
    }
    *slash = 0; /* folder is now the folder this program is in */

    put(put(app, folder), L"\\app");
    put(put(python, app), L"\\runtime\\pythonw.exe");
    put(put(script, app), L"\\tools\\launcher.py");
    if (!is_file(script))
        put(put(script, app), L"\\tools\\modman_gui.py");
    if (!is_file(python) || !is_file(script)) {
        say(UNPACK);
        ExitProcess(1);
    }

    args = own_args();
    if (length(python) + length(script) + length(args) + 16 >= sizeof cmd / sizeof cmd[0]) {
        say(L"Breathalyzer was started with too much text after its name.");
        ExitProcess(1);
    }
    end = put(cmd, L"\"");
    end = put(end, python);
    end = put(end, L"\" -I \"");
    end = put(end, script);
    end = put(end, L"\"");
    if (*args) {
        end = put(end, L" ");
        put(end, args);
    }

    si.cb = sizeof si;
    if (!CreateProcessW(python, cmd, NULL, NULL, FALSE, 0, NULL, app, &si, &pi)) {
        wsprintfW(text,
                  L"Breathalyzer couldn't start (Windows error %lu).\n\n"
                  L"If your antivirus stopped it, allow Breathalyzer's folder, or open "
                  L"app\\Start Breathalyzer (backup).cmd instead. If that doesn't help, unpack the whole zip again.",
                  GetLastError());
        say(text);
        ExitProcess(1);
    }
    AllowSetForegroundWindow(pi.dwProcessId); /* (so its window opens in front, not behind this folder) */
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    ExitProcess(0);
}
