from fastapi.testclient import TestClient

import app


_FAKE_HWND = 4242


class _FakeWindow:
    """Stands in for the pywebview window and counts every native call.

    Resize reaches across to the GUI thread and blocks until it responds, so
    the call counts matter as much as the final values. The Z-order change no
    longer touches this object at all: assigning pywebview's TopMost property
    from a worker thread is the blocking call that froze the app, so it goes
    through a posted SetWindowPos instead.
    """

    uid = "master"

    def __init__(self) -> None:
        self.shown = 0
        self.resizes: list[tuple[int, int]] = []

    def show(self) -> None:
        self.shown += 1

    def hide(self) -> None:
        pass

    def restore(self) -> None:
        pass

    def resize(self, width: int, height: int) -> None:
        self.resizes.append((width, height))


def _client(monkeypatch) -> tuple[TestClient, _FakeWindow, list]:
    window = _FakeWindow()
    topmost_calls: list[bool] = []
    monkeypatch.setattr(app.STATE, "window", window, raising=False)
    monkeypatch.setattr(app.STATE, "popup_on_top", True, raising=False)
    monkeypatch.setattr(app.STATE, "popup_visible", True, raising=False)
    monkeypatch.setattr(app.STATE, "popup_applied_size", None, raising=False)
    monkeypatch.setattr(app.STATE, "popup_hwnd", _FAKE_HWND, raising=False)
    monkeypatch.setattr(
        app, "_set_window_topmost",
        lambda hwnd, on_top: topmost_calls.append(on_top),
    )
    return TestClient(app.api), window, topmost_calls


def test_dialog_drops_always_on_top(monkeypatch):
    client, window, topmost = _client(monkeypatch)

    resp = client.post("/api/popup-window/on-top", json={"onTop": False})

    assert resp.json() == {"ok": True}
    assert topmost == [False]
    assert app.STATE.popup_on_top is False


def test_closing_dialog_restores_always_on_top(monkeypatch):
    client, window, topmost = _client(monkeypatch)

    client.post("/api/popup-window/on-top", json={"onTop": False})
    client.post("/api/popup-window/on-top", json={"onTop": True})

    assert topmost == [False, True]
    assert app.STATE.popup_on_top is True


def test_missing_flag_defaults_to_on_top(monkeypatch):
    client, window, topmost = _client(monkeypatch)
    client.post("/api/popup-window/on-top", json={"onTop": False})

    client.post("/api/popup-window/on-top", json={})

    assert topmost[-1] is True
    assert app.STATE.popup_on_top is True


def test_repeated_on_top_requests_touch_the_window_once(monkeypatch):
    """Repeat requests must not keep poking the native window."""
    client, window, topmost = _client(monkeypatch)

    for _ in range(5):
        client.post("/api/popup-window/on-top", json={"onTop": False})

    assert topmost == [False]
    assert app.STATE.popup_on_top is False


def test_reshowing_popup_keeps_dialog_stacking(monkeypatch):
    """Re-showing must not resurrect on-top while a dialog is open."""
    client, window, topmost = _client(monkeypatch)
    client.post("/api/popup-window/on-top", json={"onTop": False})

    app._hide_window()
    app._show_window()

    assert window.shown == 1
    assert topmost[-1] is False
    assert app.STATE.popup_on_top is False


def test_repeated_fit_at_same_height_resizes_once(monkeypatch):
    """A resize blocks the GUI thread, so identical fits must not re-resize."""
    client, window, topmost = _client(monkeypatch)

    for _ in range(10):
        client.post("/api/popup-window/fit", json={"height": 420})

    assert window.resizes == [(380, 500)]


def test_fit_heights_inside_the_clamp_resize_once(monkeypatch):
    """Heights that clamp to the same window size must collapse to one resize."""
    client, window, topmost = _client(monkeypatch)

    client.post("/api/popup-window/fit", json={"height": 900})
    client.post("/api/popup-window/fit", json={"height": 1200})

    assert window.resizes == [(380, 700)]


def test_alternating_fits_still_resize(monkeypatch):
    """Genuine height changes must still reach the window."""
    client, window, topmost = _client(monkeypatch)

    client.post("/api/popup-window/fit", json={"height": 300})
    client.post("/api/popup-window/fit", json={"height": 500})

    assert window.resizes == [(380, 380), (380, 580)]


def test_on_top_uses_the_async_z_order_flag():
    """SWP_ASYNCWINDOWPOS is what stops the call waiting on the GUI thread.

    Without it the request is *sent* to the owning thread and the caller
    blocks, which is what froze the app when a dialog opened while the
    auto-fit resize was in flight.
    """
    import ctypes

    seen = {}

    class _FakeUser32:
        def SetWindowPos(self, hwnd, after, x, y, cx, cy, flags):
            seen["hwnd"] = hwnd
            seen["after"] = after
            seen["flags"] = flags
            return 1

    class _FakeWinDll:
        user32 = _FakeUser32()

    original = ctypes.windll
    ctypes.windll = _FakeWinDll()
    try:
        app._set_window_topmost(99, False)
    finally:
        ctypes.windll = original

    assert seen["hwnd"] == 99
    assert seen["after"] == app._HWND_NOTOPMOST
    assert seen["flags"] & app._SWP_ASYNCWINDOWPOS
    # Z-order only: it must not move or resize the window.
    assert seen["flags"] & app._SWP_NOMOVE
    assert seen["flags"] & app._SWP_NOSIZE


def test_on_top_is_skipped_without_a_handle(monkeypatch):
    """No handle means no native call, rather than a crash on dialog open."""
    window = _FakeWindow()
    monkeypatch.setattr(app.STATE, "window", window, raising=False)
    monkeypatch.setattr(app.STATE, "popup_on_top", True, raising=False)
    monkeypatch.setattr(app.STATE, "popup_hwnd", 0, raising=False)
    monkeypatch.setattr(app, "_popup_hwnd", lambda: 0)
    called = []
    monkeypatch.setattr(app, "_set_window_topmost",
                        lambda *a: called.append(a))

    app._set_popup_on_top(False)

    assert called == []
    assert app.STATE.popup_on_top is False


def test_on_top_toggle_survives_missing_window(monkeypatch):
    monkeypatch.setattr(app.STATE, "window", None, raising=False)
    client = TestClient(app.api)

    resp = client.post("/api/popup-window/on-top", json={"onTop": False})

    assert resp.json() == {"ok": True}
    assert app.STATE.popup_on_top is False
