"""Show and resize the notes WebView through its owning WinForms control."""
import ctypes
from ctypes import wintypes


def present(window, activate: bool) -> None:
    from System import Action
    from System.Windows.Forms import FormWindowState
    from webview.platforms.winforms import BrowserView

    form = BrowserView.instances[window.uid]

    def show():
        user32 = ctypes.windll.user32
        get_style, set_style = user32.GetWindowLongW, user32.SetWindowLongW
        get_style.argtypes, get_style.restype = [wintypes.HWND, ctypes.c_int], ctypes.c_long
        set_style.argtypes, set_style.restype = [wintypes.HWND, ctypes.c_int, ctypes.c_long], ctypes.c_long
        hwnd = int(form.Handle.ToInt64())
        style = get_style(hwnd, -20)
        # ShowWindow alone leaves WinForms.Visible and its child WebView false.
        # Use managed Show, temporarily preventing activation for automatic opens.
        window.focus = activate
        set_style(hwnd, -20, (style & ~0x08000000) if activate else (style | 0x08000000))
        try:
            form.Show()
            if activate:
                form.WindowState = FormWindowState.Normal
                form.Activate()
        finally:
            window.focus = True
            set_style(hwnd, -20, style & ~0x08000000)

    form.Invoke(Action(show))


def resize(window, width: int, height: int) -> None:
    from System import Action
    from System.Drawing import Size, Point
    from System.Windows.Forms import Screen
    from webview.platforms.winforms import BrowserView

    form = BrowserView.instances[window.uid]

    def fit():
        # pywebview.resize uses SWP_SHOWWINDOW, which can reveal/activate a hidden
        # native frame without showing its managed children. Size has no such side effect.
        scale = form._scale
        area = Screen.FromControl(form).WorkingArea
        form.Size = Size(min(round(width * scale), area.Width), min(round(height * scale), area.Height))
        form.Location = Point(max(area.Left, min(form.Left, area.Right - form.Width)),
                              max(area.Top, min(form.Top, area.Bottom - form.Height)))

    form.Invoke(Action(fit))


def copy_text(window, text: str) -> None:
    """Clipboard writes must run on the window's STA thread in WebView2."""
    from System import Action
    from System.Windows.Forms import Clipboard, TextDataFormat
    from webview.platforms.winforms import BrowserView
    if window is None: raise RuntimeError("Meeting window unavailable")
    form = BrowserView.instances[window.uid]
    failure = []
    def copy():
        try: Clipboard.SetText(text, TextDataFormat.UnicodeText)
        except Exception: failure.append(True)
    form.Invoke(Action(copy))
    if failure: raise RuntimeError("Clipboard unavailable")
