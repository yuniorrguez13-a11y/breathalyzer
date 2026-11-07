// win_min.h - the Win32 declarations the Mod menu mod needs beyond modhost's platform_min.h (which stays as modhost
// uses it, so other code that declares some of these itself is not affected). The documented Win32 interface
// (Microsoft docs), declared by hand: the zig that ships with Savage Wind has no windows.h.
#pragma once
#include "platform_min.h"

#define SW_PAGE_READWRITE 0x04ul
#define SW_PAGE_EXECUTE_READWRITE 0x40ul
#define SW_MEM_COMMIT 0x1000ul
#define SW_GENERIC_READ 0x80000000ul
#define SW_GENERIC_WRITE 0x40000000ul
#define SW_FILE_SHARE_ALL 0x7ul                    // FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
#define SW_CREATE_ALWAYS 2ul
#define SW_OPEN_EXISTING 3ul
#define SW_FILE_ATTRIBUTE_NORMAL 0x80ul
#define SW_MOVEFILE_REPLACE_EXISTING 0x1ul
#define SW_MOVEFILE_WRITE_THROUGH 0x8ul
#define SW_WAIT_OBJECT_0 0ul
#define SW_INFINITE 0xFFFFFFFFul

typedef struct {  // MEMORY_BASIC_INFORMATION (64-bit: 48 bytes)
    void *base, *allocation_base;
    DWORD allocation_protect;
    unsigned short partition_id;
    size_t region_size;
    DWORD state, protect, type;
} SwMemInfo;

typedef struct {  // WIN32_FILE_ATTRIBUTE_DATA
    DWORD attributes;
    SwFileTime created, accessed, written;
    DWORD size_high, size_low;
} SwFileAttrData;

typedef struct {  // SRWLOCK
    void *ptr;
} SwSrwLock;

typedef DWORD(WINAPI *SwThreadFn)(void *arg);

__declspec(dllimport) HMODULE WINAPI GetModuleHandleW(const wchar_t *name);
__declspec(dllimport) BOOL WINAPI VirtualProtect(void *addr, size_t size, DWORD protect, DWORD *old);
__declspec(dllimport) size_t WINAPI VirtualQuery(const void *addr, SwMemInfo *out, size_t size);
__declspec(dllimport) HANDLE WINAPI GetCurrentProcess(void);
__declspec(dllimport) BOOL WINAPI FlushInstructionCache(HANDLE process, const void *addr, size_t size);
__declspec(dllimport) HANDLE WINAPI CreateFileW(const wchar_t *path, DWORD access, DWORD share, void *security,
                                                DWORD disposition, DWORD flags, HANDLE templ);
__declspec(dllimport) BOOL WINAPI ReadFile(HANDLE h, void *buf, DWORD size, DWORD *read, void *overlapped);
__declspec(dllimport) BOOL WINAPI WriteFile(HANDLE h, const void *buf, DWORD size, DWORD *written, void *overlapped);
__declspec(dllimport) BOOL WINAPI FlushFileBuffers(HANDLE h);
__declspec(dllimport) BOOL WINAPI CloseHandle(HANDLE h);
__declspec(dllimport) BOOL WINAPI MoveFileExW(const wchar_t *from, const wchar_t *to, DWORD flags);
__declspec(dllimport) BOOL WINAPI DeleteFileW(const wchar_t *path);
__declspec(dllimport) BOOL WINAPI CreateDirectoryW(const wchar_t *path, void *security);
__declspec(dllimport) BOOL WINAPI GetFileAttributesExW(const wchar_t *path, int level, SwFileAttrData *out);
__declspec(dllimport) HANDLE WINAPI CreateThread(void *security, size_t stack, SwThreadFn fn, void *arg, DWORD flags,
                                                 DWORD *id);
__declspec(dllimport) DWORD WINAPI WaitForSingleObject(HANDLE h, DWORD ms);
__declspec(dllimport) void WINAPI InitializeSRWLock(SwSrwLock *lock);
__declspec(dllimport) void WINAPI AcquireSRWLockExclusive(SwSrwLock *lock);
__declspec(dllimport) void WINAPI ReleaseSRWLockExclusive(SwSrwLock *lock);
