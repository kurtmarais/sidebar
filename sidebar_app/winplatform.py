"""Windows integration: startup entry, global hotkey, foreground window
and DPAPI encryption.

All functions degrade to no-ops (or ``None``) on other platforms so the
rest of the app, and the tests, run anywhere.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

APP_NAME = "Sidebar"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000
VK_S = 0x53
HOTKEY_ID = 0x5B1D

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)

    _user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.UnregisterHotKey.restype = wintypes.BOOL
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wintypes.UINT]
    _user32.SetWindowPos.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.LocalFree.argtypes = [wintypes.HLOCAL]

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]

    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                    ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                    ("time", wintypes.DWORD), ("pt", wintypes.POINT)]


# --------------------------------------------------------------- startup
# "Start with Windows" uses the per-user Run registry value. Some managed
# PCs block writing it, so the fallback is a shortcut in the user's Startup
# folder. Either one counts as enabled.

def startup_parts() -> tuple[str, str, str]:
    """(program, arguments, working folder) that start this copy of the app."""
    if getattr(sys, "frozen", False):
        return sys.executable, "--startup", str(Path(sys.executable).parent)
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    root = Path(__file__).resolve().parent.parent
    return str(pythonw if pythonw.exists() else exe), f'"{root / "run_sidebar.py"}" --startup', str(root)


def startup_command() -> str:
    program, args, _ = startup_parts()
    return f'"{program}" {args}'.strip()


def startup_shortcut() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / f"{APP_NAME}.lnk"


def _registry_value() -> str | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, APP_NAME)[0]
    except OSError:
        return None


APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"


def startup_disabled_in_task_manager() -> bool:
    """Task Manager's Startup apps switch is stored separately; first byte 0x03 = disabled."""
    if not IS_WINDOWS:
        return False
    import winreg
    for sub_key, name in (("Run", APP_NAME), ("StartupFolder", f"{APP_NAME}.lnk")):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{APPROVED_KEY}\\{sub_key}") as key:
                data = winreg.QueryValueEx(key, name)[0]
        except OSError:
            continue
        if isinstance(data, (bytes, bytearray)) and data[:1] in (b"\x01", b"\x03"):
            return True
    return False


def _clear_task_manager_disable() -> None:
    import winreg
    for sub_key, name in (("Run", APP_NAME), ("StartupFolder", f"{APP_NAME}.lnk")):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{APPROVED_KEY}\\{sub_key}", 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)       # no value = enabled
        except OSError:
            pass


def is_startup_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    present = _registry_value() is not None or startup_shortcut().exists()
    return present and not startup_disabled_in_task_manager()


def _create_shortcut(lnk: Path) -> None:
    """Write a .lnk with the Windows Script Host (values passed via environment, no quoting issues)."""
    import subprocess
    program, args, workdir = startup_parts()
    script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:SB_LNK);"
              "$s.TargetPath=$env:SB_TARGET;$s.Arguments=$env:SB_ARGS;"
              "$s.WorkingDirectory=$env:SB_DIR;$s.Save()")
    lnk.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, SB_LNK=str(lnk), SB_TARGET=program, SB_ARGS=args, SB_DIR=workdir)
    proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-Command", script], env=env, capture_output=True, text=True, timeout=30,
                          creationflags=0x08000000)
    if not lnk.exists():
        raise OSError((proc.stderr or proc.stdout or "shortcut was not created").strip())


def set_startup_enabled(enabled: bool) -> str:
    """Turn Start with Windows on or off. Returns how it is set up ('registry',
    'startup folder', 'registry + startup folder' or 'off'); raises OSError if
    neither method could be set up.

    Both methods are used when possible: some PCs (often work-managed ones,
    via policy) ignore one of them at sign-in. If both run, the second copy of
    the app notices the first and exits quietly."""
    if not IS_WINDOWS:
        return "off"
    import winreg
    lnk = startup_shortcut()
    if not enabled:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, APP_NAME)
        except OSError:
            pass
        try:
            lnk.unlink()
        except OSError:
            pass
        if is_startup_enabled():
            raise OSError("The startup entry could not be removed.")
        return "off"

    command = startup_command()
    _clear_task_manager_disable()      # ticking the box means "on", even if switched off in Task Manager
    registry_error = shortcut_error = ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
    except OSError as exc:
        registry_error = str(exc)
    try:
        _create_shortcut(lnk)
    except (OSError, ValueError) as exc:
        shortcut_error = str(exc)
    methods = [m for m, ok in (("registry", _registry_value() == command), ("startup folder", lnk.exists())) if ok]
    if not methods:
        raise OSError(f"Registry: {registry_error or 'value did not stick'}\n"
                      f"Startup folder: {shortcut_error or 'shortcut was not created'}")
    return " + ".join(methods)


def refresh_startup_entry() -> bool:
    """Keep Start with Windows pointing at the copy that is running now, and add
    the Startup-folder shortcut where only the registry entry exists. Returns
    True if anything was updated."""
    if not IS_WINDOWS:
        return False
    current = _registry_value()
    if current is None and not startup_shortcut().exists():
        return False                    # Start with Windows is off
    if current == startup_command() and startup_shortcut().exists():
        return False
    set_startup_enabled(True)
    return True


def run_entries_blocked_by_policy() -> bool:
    """True if a Windows policy tells Windows to skip per-user Run entries at sign-in."""
    if not IS_WINDOWS:
        return False
    import winreg
    path = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, path) as key:
                if winreg.QueryValueEx(key, "DisableCurrentUserRun")[0]:
                    return True
        except OSError:
            continue
    return False


def package_identity() -> str:
    """Name of the Windows app package this process runs inside, or "".

    Python started through some Microsoft Store / install-manager aliases runs
    inside an app package, where Windows may redirect registry and AppData
    writes to a private copy. That matters for Start with Windows."""
    if not IS_WINDOWS:
        return ""
    length = wintypes.UINT(0)
    rc = _kernel32.GetCurrentPackageFullName(ctypes.byref(length), None)
    if rc == 15700:              # APPMODEL_ERROR_NO_PACKAGE
        return ""
    buf = ctypes.create_unicode_buffer(length.value + 1)
    if _kernel32.GetCurrentPackageFullName(ctypes.byref(length), buf) != 0:
        return "unknown package"
    return buf.value


# ---------------------------------------------------------------- hotkey
def register_hotkey(hwnd: int) -> bool:
    """Ctrl+Shift+S, delivered as WM_HOTKEY to ``hwnd``."""
    if not IS_WINDOWS:
        return False
    return bool(_user32.RegisterHotKey(hwnd, HOTKEY_ID, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_S))


GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x8
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_NOOWNERZORDER = 0x1, 0x2, 0x10, 0x200


def is_topmost(hwnd: int) -> bool:
    if sys.platform != "win32" or not hwnd:
        return True
    return bool(_user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)


def make_topmost(hwnd: int) -> bool:
    """Put the window at the top of the always-on-top band without activating it.
    Windows can drop a window from that band (after sleep, a display change, a
    full-screen app, or another program changing z-order), and Qt never restores it."""
    if sys.platform != "win32" or not hwnd:
        return False
    return bool(_user32.SetWindowPos(
        hwnd, ctypes.c_void_p(HWND_TOPMOST).value, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER))


def unregister_hotkey(hwnd: int) -> None:
    if IS_WINDOWS:
        _user32.UnregisterHotKey(hwnd, HOTKEY_ID)


def is_hotkey_message(message_ptr: int) -> bool:
    if not IS_WINDOWS:
        return False
    msg = MSG.from_address(int(message_ptr))
    return msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID


# -------------------------------------------------------------- processes
def _run_hidden(args: list[str]) -> str:
    import subprocess
    flags = 0x08000000 if sys.platform == "win32" else 0   # CREATE_NO_WINDOW
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=5, creationflags=flags)
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    return out.stdout or ""


def process_running(exe: str) -> bool:
    """True if a process with this image name (e.g. caffeine64.exe) is running."""
    if not exe:
        return False
    if IS_WINDOWS:
        out = _run_hidden(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/FO", "CSV", "/NH"])
        return f'"{exe.lower()}"' in out.lower()
    return bool(_run_hidden(["pgrep", "-x", exe]).strip())


def kill_process(exe: str) -> None:
    if IS_WINDOWS:
        _run_hidden(["taskkill", "/IM", exe, "/F"])
    else:
        _run_hidden(["pkill", "-x", exe])


# ------------------------------------------------------ foreground window
def foreground_window() -> tuple[str, str, int] | None:
    """(process exe name, window title, pid) of the window in front."""
    if not IS_WINDOWS:
        return None
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None
    length = _user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    handle = _kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
    if handle:
        try:
            size = wintypes.DWORD(1024)
            path = ctypes.create_unicode_buffer(size.value)
            if _kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                exe = os.path.basename(path.value)
        finally:
            _kernel32.CloseHandle(handle)
    return exe.lower(), buf.value, pid.value


# ------------------------------------------------------------------ DPAPI
def dpapi_available() -> bool:
    return IS_WINDOWS


def _blob(data: bytes) -> "_Blob":
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob._buf = buf  # keep alive
    return blob


def dpapi_protect(data: bytes, entropy: bytes) -> bytes:
    if not IS_WINDOWS:
        raise RuntimeError("Windows protected storage is not available")
    out = _Blob()
    if not _crypt32.CryptProtectData(ctypes.byref(_blob(data)), "Sidebar secret",
                                     ctypes.byref(_blob(entropy)), None, None, 0x1,
                                     ctypes.byref(out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def dpapi_unprotect(data: bytes, entropy: bytes) -> bytes:
    if not IS_WINDOWS:
        raise RuntimeError("Windows protected storage is not available")
    out = _Blob()
    if not _crypt32.CryptUnprotectData(ctypes.byref(_blob(data)), None,
                                       ctypes.byref(_blob(entropy)), None, None, 0x1,
                                       ctypes.byref(out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))
