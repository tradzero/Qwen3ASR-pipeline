from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


_PRIORITY_CLASSES = {
    "idle": 0x00000040,
    "belownormal": 0x00004000,
    "normal": 0x00000020,
    "abovenormal": 0x00008000,
    "high": 0x00000080,
}


def _priority_class(priority: str | None) -> int | None:
    if priority is None:
        return None
    normalized = priority.replace("-", "").replace("_", "").lower()
    return _PRIORITY_CLASSES.get(normalized)


def set_process_priority(priority: str | None, pid: int | None = None) -> bool:
    priority_class = _priority_class(priority)
    if os.name != "nt" or priority_class is None:
        return False

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.SetPriorityClass.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    close_handle = False
    if pid is None:
        handle = kernel32.GetCurrentProcess()
    else:
        process_set_information = 0x0200
        handle = kernel32.OpenProcess(process_set_information, False, int(pid))
        close_handle = True
    if not handle:
        return False

    try:
        return bool(kernel32.SetPriorityClass(handle, priority_class))
    finally:
        if close_handle:
            kernel32.CloseHandle(handle)