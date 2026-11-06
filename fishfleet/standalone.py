"""Windows interactive scheduled-task entry: independent of DSH process jobs.

Uses the installed Python runtime but not the DSH service or GUI lifecycle.
Launch via the FishFleet-Desktop task; retains a single instance and local logs.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import sys
import urllib.request


def existing_service() -> bool:
    # Do not start a second device controller while the usual instance is alive.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open('http://127.0.0.1:8765/api/control/config', timeout=2) as response:
            value = json.load(response)
            return value.get('protocol') == 1 and isinstance(value.get('enabled'), bool)
    except Exception:
        return False


def configure_hidden_children() -> None:
    """Only the standalone pythonw entry hides console subprocesses.

    Preserve explicit flags, pipes, and caller STARTUPINFO. Installing before
    importing the application also covers adb calls in third-party adapters.
    """
    if os.name != 'nt':
        return
    import subprocess
    if getattr(subprocess.Popen, '_fishfleet_hidden', False):
        return
    original = subprocess.Popen

    class HiddenPopen(original):
        _fishfleet_hidden = True

        def __init__(self, *args, **kwargs):
            flags = kwargs.get('creationflags', 0)
            # An explicitly requested new/detached console keeps its own policy.
            if not flags & (subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS):
                kwargs['creationflags'] = flags | subprocess.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = HiddenPopen


def main() -> int:
    if os.name != 'nt':
        raise RuntimeError('Windows desktop launcher only')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateMutexW(None, False, 'Local\\FishFleetIndependentDesktop')
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    duplicate = ctypes.get_last_error() == 183
    try:
        if duplicate or existing_service():
            ctypes.windll.user32.MessageBoxW(None, 'FishFleet 已在运行，请使用现有窗口。', 'FishFleet', 0x40)
            return 0
        root = Path(__file__).resolve().parents[1]
        os.chdir(root)
        os.environ['FISHFLEET_DEBUG'] = '0'
        os.environ['FISHFLEET_DEBUG_PORT'] = '9223'
        os.environ['FISHFLEET_LIVE_CONTROL'] = '1'
        logs = Path(os.environ.get('LOCALAPPDATA', str(root))) / 'FishFleet' / 'logs'
        logs.mkdir(parents=True, exist_ok=True)
        # pythonw has no console streams. Keep logs available for troubleshooting.
        with (logs / 'desktop.log').open('a', encoding='utf-8', buffering=1) as log:
            sys.stdout = sys.stderr = log
            from datetime import datetime
            print('\n[standalone] launch', datetime.now().isoformat())
            try:
                configure_hidden_children()
                from fishfleet.desktop import main as desktop_main
                return desktop_main()
            except Exception:
                import traceback
                traceback.print_exc()
                ctypes.windll.user32.MessageBoxW(None, f'启动失败，日志：{logs / "desktop.log"}', 'FishFleet', 0x10)
                return 1
    finally:
        kernel.CloseHandle(handle)


if __name__ == '__main__':
    raise SystemExit(main())
