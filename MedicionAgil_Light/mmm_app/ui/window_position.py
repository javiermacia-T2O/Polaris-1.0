"""Place application dialogs over their owner on the same Windows monitor."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys


def _work_area(parent) -> tuple[int, int, int, int]:
    if sys.platform == "win32":
        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD),
                        ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD)]

        try:
            user32 = ctypes.windll.user32
            user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
            user32.MonitorFromWindow.restype = wintypes.HANDLE
            user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE,
                                               ctypes.POINTER(MONITORINFO)]
            user32.GetMonitorInfoW.restype = wintypes.BOOL
            monitor = user32.MonitorFromWindow(parent.winfo_id(), 2)
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(info)
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                area = info.rcWork
                return area.left, area.top, area.right, area.bottom
        except (AttributeError, OSError, ValueError):
            pass
    return (0, 0, int(parent.winfo_screenwidth()),
            int(parent.winfo_screenheight()))


def center_popup(window, parent, width: int, height: int) -> tuple[int, int]:
    """Center one popup above its owner and keep it inside that monitor."""
    parent.update_idletasks()
    left, top, right, bottom = _work_area(parent)
    width = max(1, min(int(width), right - left))
    height = max(1, min(int(height), bottom - top))
    px = int(parent.winfo_rootx())
    py = int(parent.winfo_rooty())
    pw = max(1, int(parent.winfo_width()))
    ph = max(1, int(parent.winfo_height()))
    x = max(left, min(px + (pw - width) // 2, right - width))
    y = max(top, min(py + (ph - height) // 2, bottom - height))
    window.geometry(f"{width}x{height}+{x}+{y}")
    return x, y
