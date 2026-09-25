"""Regression guard for the WebView2 blank-window bug.

The controller starts INVISIBLE; if the code never sets ``IsVisible = True``
the window shows a white page (this shipped once). These tests are static
checks on the source — a live WebView2 render test lives in the release
verification script (scripts + cleancheck).
"""

from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "meetingnotes" / "webview2win.py"


def test_controller_is_made_visible():
    text = SRC.read_text(encoding="utf-8")
    assert "IsVisible = True" in text, (
        "webview2win must set controller.IsVisible = True — without it the "
        "window renders blank white (regression)."
    )


def test_visibility_set_before_navigate():
    text = SRC.read_text(encoding="utf-8")
    vis = text.index("IsVisible = True")
    nav = text.index("webview.Navigate(url)")
    assert vis < nav, "make the controller visible before navigating"


def test_no_pythonnet_dependency():
    """The window must not rely on pythonnet/clr (broke on clean machines)."""
    text = SRC.read_text(encoding="utf-8")
    assert "import clr" not in text
    assert "pythonnet" not in text.lower()
    assert "import webview" not in text and "import clr_loader" not in text


def test_bundled_native_assets_exist():
    native = SRC.parent / "native"
    assert (native / "WebView2.tlb").is_file()
    assert (native / "WebView2Loader.dll").is_file()
