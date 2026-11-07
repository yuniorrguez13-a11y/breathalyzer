// The few Win32 and C runtime declarations modhost needs. The zig that ships with Savage Wind is trimmed down to what
// the game code needs: it has string.h and stdlib.h but no windows.h, stdio.h or stdarg.h. Everything below is the
// documented Win32 / UCRT interface (Microsoft docs), declared by hand.
#pragma once
#include <stddef.h>
#include <stdint.h>

// ---- varargs (clang builtins)
typedef __builtin_va_list sw_va_list;
#define sw_va_start(ap, last) __builtin_va_start(ap, last)
#define sw_va_end(ap) __builtin_va_end(ap)

// ---- C runtime (ucrtbase, shared with savagewind.exe, so stderr is the stream it sends to savagewind.log)
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wbuiltin-requires-header"  // (these are normally in stdio.h)
struct _iobuf;
struct _iobuf *__acrt_iob_func(unsigned index);  // 0 stdin, 1 stdout, 2 stderr
size_t fwrite(const void *p, size_t size, size_t n, struct _iobuf *f);
int fflush(struct _iobuf *f);
int vsnprintf(char *buf, size_t n, const char *fmt, sw_va_list ap);
int snprintf(char *buf, size_t n, const char *fmt, ...);
#pragma clang diagnostic pop

// ---- Win32 (kernel32)
typedef void *HMODULE;
typedef void *HANDLE;
typedef unsigned long DWORD;
typedef int BOOL;

#define WINAPI __stdcall
#define SW_INVALID_HANDLE ((HANDLE)(intptr_t)-1)
#define SW_INVALID_ATTRIBUTES 0xFFFFFFFFul
#define SW_ATTR_DIRECTORY 0x10ul
#define SW_CP_UTF8 65001u
#define SW_LOAD_SEARCH_DLL_DIR 0x00000100ul       // LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR
#define SW_LOAD_SEARCH_DEFAULT_DIRS 0x00001000ul  // LOAD_LIBRARY_SEARCH_DEFAULT_DIRS
#define SW_MODULE_FROM_ADDRESS 0x4ul              // GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS
#define SW_MODULE_KEEP_REFCOUNT 0x2ul             // GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT

typedef struct {
    DWORD low, high;
} SwFileTime;

typedef struct {  // WIN32_FIND_DATAW (592 bytes)
    DWORD attributes;
    SwFileTime created, accessed, written;
    DWORD size_high, size_low, reserved0, reserved1;
    wchar_t name[260];
    wchar_t short_name[14];
} SwFindData;

__declspec(dllimport) HMODULE WINAPI LoadLibraryExW(const wchar_t *path, HANDLE file, DWORD flags);
__declspec(dllimport) void *WINAPI GetProcAddress(HMODULE h, const char *name);
__declspec(dllimport) BOOL WINAPI GetModuleHandleExW(DWORD flags, const wchar_t *name, HMODULE *out);
__declspec(dllimport) DWORD WINAPI GetModuleFileNameW(HMODULE h, wchar_t *out, DWORD size);
__declspec(dllimport) DWORD WINAPI GetLastError(void);
__declspec(dllimport) DWORD WINAPI GetEnvironmentVariableW(const wchar_t *name, wchar_t *out, DWORD size);
__declspec(dllimport) DWORD WINAPI GetFullPathNameW(const wchar_t *path, DWORD size, wchar_t *out, wchar_t **file);
__declspec(dllimport) DWORD WINAPI GetFileAttributesW(const wchar_t *path);
__declspec(dllimport) HANDLE WINAPI FindFirstFileW(const wchar_t *pattern, SwFindData *out);
__declspec(dllimport) BOOL WINAPI FindNextFileW(HANDLE h, SwFindData *out);
__declspec(dllimport) BOOL WINAPI FindClose(HANDLE h);
__declspec(dllimport) int WINAPI WideCharToMultiByte(unsigned cp, DWORD flags, const wchar_t *in, int n, char *out,
                                                     int size, const char *dflt, BOOL *used_dflt);
__declspec(dllimport) int WINAPI MultiByteToWideChar(unsigned cp, DWORD flags, const char *in, int n, wchar_t *out,
                                                     int size);
__declspec(dllimport) BOOL WINAPI FreeLibrary(HMODULE h);
__declspec(dllimport) void WINAPI Sleep(DWORD ms);
__declspec(dllimport) unsigned long long WINAPI GetTickCount64(void);
__declspec(dllimport) DWORD WINAPI GetCurrentThreadId(void);
