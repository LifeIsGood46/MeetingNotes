"""Native app window via WebView2 (Edge runtime), COM-only — no .NET.

Uses the official WebView2 SDK type library (``native/WebView2.tlb``) plus
the loader DLL (``native/WebView2Loader.dll``) to host the WebView2 control
inside a plain Win32 window: standard title bar, taskbar entry, logo icon.

The Edge runtime ships with Windows 10/11 (Edge updates), so this is a real
desktop window on ~every machine. If the runtime is missing or COM wiring
fails, the caller falls back to the chromeless Edge app window, then the
system browser.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
from pathlib import Path

WND_CLASS = "MeetingNotesWnd"
WM_DESTROY = 0x0002
WM_SIZE = 0x0005

LRESULT = ctypes.c_ssize_t
UINT = ctypes.c_uint
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t

WS_OVERLAPPEDWINDOW = 0x00CF0000
SW_SHOW = 5
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000004

_NATIVE = Path(__file__).resolve().parent / "native"


def _loader():
    """Load WebView2Loader.dll from the package (or beside a frozen exe)."""
    candidates = [
        _NATIVE / "WebView2Loader.dll",
        Path(sys.executable).resolve().parent / "WebView2Loader.dll",
        Path(sys.executable).resolve().parent / "_internal" / "WebView2Loader.dll",
    ]
    for p in candidates:
        if p.is_file():
            try:
                lib = ctypes.WinDLL(str(p))
                lib.CreateCoreWebView2EnvironmentWithOptions.restype = ctypes.c_int
                lib.CreateCoreWebView2EnvironmentWithOptions.argtypes = [
                    ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p,
                    ctypes.c_void_p]
                return lib
            except OSError:
                continue
    return None


def _tlb_path() -> Path | None:
    p = _NATIVE / "WebView2.tlb"
    return p if p.is_file() else None


def _user_data_dir() -> str:
    if getattr(sys, "frozen", False):
        d = Path(sys.executable).parent / "data" / "webview2"
    else:
        d = Path.home() / ".meetingnotes" / "webview2"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def webview2_available() -> bool:
    """Cheap capability check: loader + typelib present and the runtime installed."""
    if not sys.platform.startswith("win"):
        return False
    lib = _loader()
    if lib is None or _tlb_path() is None:
        return False
    try:
        f = lib.GetAvailableCoreWebView2BrowserVersionString
        f.restype = ctypes.c_int
        f.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_wchar_p)]
        ver = ctypes.c_wchar_p()
        return f(None, ctypes.byref(ver)) == 0 and bool(ver.value)
    except OSError:
        return False


def open_webview2_window(url: str, title: str, width: int, height: int,
                         icon_ico: Path | None = None) -> bool:
    """Create a Win32 window hosting the WebView2 control. Blocks until the
    window closes. Returns False fast when WebView2 is unavailable.

    All COM work happens on this thread's STA with a message pump, so the
    WebView2 callbacks stay apartment-safe.
    """
    if not sys.platform.startswith("win"):
        return False

    import comtypes
    import comtypes.client

    loader = _loader()
    tlb = _tlb_path()
    if loader is None or tlb is None:
        return False

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # Windows "native occlusion" detection pauses rendering when another
    # window overlaps ours. On VMs/RDP (and behind the VMware toolbar) it
    # misjudges, leaving stale frames: dialogs "don't appear" until some
    # input forces a repaint, and clicks hit the last painted frame. Turning
    # the feature off keeps the frame and hit-test tree in sync.
    args = "--disable-features=CalculateNativeWinOcclusion"
    existing = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
    if "CalculateNativeWinOcclusion" not in existing:
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
            f"{existing} {args}".strip()
        )

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", UINT), ("style", UINT),
            ("lpfnWndProc", ctypes.WINFUNCTYPE(LRESULT, wt.HWND, UINT, WPARAM, LPARAM)),
            ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
            ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
            ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
            ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR),
            ("hIconSm", wt.HICON),
        ]

    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, UINT, WPARAM, LPARAM)

    user32.DefWindowProcW.restype = LRESULT
    user32.DefWindowProcW.argtypes = [wt.HWND, UINT, WPARAM, LPARAM]
    user32.CreateWindowExW.restype = wt.HWND
    user32.CreateWindowExW.argtypes = [UINT, wt.LPCWSTR, wt.LPCWSTR, UINT,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
    user32.PeekMessageW.restype = ctypes.c_int
    user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, UINT, UINT, UINT]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.GetClientRect.restype = ctypes.c_int
    user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    user32.LoadImageW.restype = wt.HANDLE
    user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, UINT, ctypes.c_int,
                                  ctypes.c_int, UINT]
    kernel32.GetModuleHandleW.restype = wt.HINSTANCE
    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]

    closed = threading.Event()
    state: dict = {}

    def wnd_proc(hwnd, msg, wparam, lparam):
        if msg == WM_DESTROY:
            closed.set()
            user32.PostQuitMessage(0)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    wnd_proc_c = WNDPROC(wnd_proc)

    def pump(until: threading.Event | None) -> None:
        """Dispatch messages until `until` is set or the window closes."""
        msg = wt.MSG()
        while not closed.is_set() and (until is None or not until.is_set()):
            if not user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.WaitMessage()
                continue
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
            if msg.message == WM_SIZE and state.get("ctrl") is not None:
                r = wt.RECT()
                user32.GetClientRect(msg.hwnd, ctypes.byref(r))
                try:
                    state["ctrl"].Bounds = r
                except Exception:
                    pass

    hinstance = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = wnd_proc_c
    wc.hInstance = hinstance
    wc.lpszClassName = WND_CLASS
    if icon_ico and Path(icon_ico).is_file():
        wc.hIcon = user32.LoadImageW(None, str(icon_ico), IMAGE_ICON,
                                     0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
        wc.hIconSm = wc.hIcon

    if not user32.RegisterClassExW(ctypes.byref(wc)):
        return False

    scr_w = user32.GetSystemMetrics(0)
    scr_h = user32.GetSystemMetrics(1)
    x = max(0, (scr_w - width) // 2)
    y = max(0, (scr_h - height) // 2)
    hwnd = user32.CreateWindowExW(
        0, WND_CLASS, title, WS_OVERLAPPEDWINDOW,
        x, y, width, height, None, None, hinstance, None)
    if not hwnd:
        user32.UnregisterClassW(WND_CLASS, hinstance)
        return False

    def cleanup():
        try:
            user32.DestroyWindow(hwnd)
        except Exception:
            pass
        try:
            user32.UnregisterClassW(WND_CLASS, hinstance)
        except Exception:
            pass

    try:
        comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
        if getattr(sys, "frozen", False):
            # frozen packages are read-only; generate wrappers in memory
            comtypes.client.gen_dir = None
        comtypes.client.GetModule(str(tlb))
        import comtypes.gen.WebView2 as W

        env_ready = threading.Event()
        ctrl_ready = threading.Event()

        from comtypes import COMObject

        class EnvCb(COMObject):
            _com_interfaces_ = [W.ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler]

            def Invoke(self, errorCode, createdEnvironment):
                state["env"] = createdEnvironment
                env_ready.set()

        class CtrlCb(COMObject):
            _com_interfaces_ = [W.ICoreWebView2CreateCoreWebView2ControllerCompletedHandler]

            def Invoke(self, errorCode, createdController):
                state["ctrl"] = createdController
                ctrl_ready.set()

        env_cb = EnvCb()
        env_punk = env_cb.QueryInterface(comtypes.IUnknown)
        env_ptr = ctypes.cast(env_punk, ctypes.c_void_p).value
        hr = loader.CreateCoreWebView2EnvironmentWithOptions(
            None, _user_data_dir(), None, ctypes.c_void_p(env_ptr))
        if hr != 0:
            cleanup()
            return False

        pump(env_ready)
        env = state.get("env")
        if env is None:
            cleanup()
            return False

        ctrl_cb = CtrlCb()
        env.CreateCoreWebView2Controller(hwnd, ctrl_cb)
        pump(ctrl_ready)
        controller = state.get("ctrl")
        if controller is None:
            cleanup()
            return False

        webview = controller.CoreWebView2
        r = wt.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(r))
        controller.Bounds = r
        # The controller starts INVISIBLE — without this, WebView2 never
        # paints and the window shows a blank white page.
        controller.IsVisible = True
        webview.Navigate(url)
        user32.ShowWindow(hwnd, SW_SHOW)
        user32.UpdateWindow(hwnd)

        pump(None)  # run until the window closes
        return True
    except Exception:
        import traceback

        traceback.print_exc(file=sys.stderr)
        return False
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass
        cleanup()