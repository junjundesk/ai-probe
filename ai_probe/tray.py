"""Optional Windows system-tray integration for the desktop UI.

The application remains usable when :mod:`pystray` (and Pillow) are not
installed.  In that case ``TrayController.start`` simply returns ``False``
and callers can keep the regular Tk window controls.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from contextlib import suppress

try:  # Keep tray support optional for headless/test environments.
    import pystray
    from PIL import Image, ImageDraw

    if sys.platform == "win32":
        # pystray selects the Windows backend through a dynamic import. Keep
        # the backend visible to static executable packagers such as Nuitka.
        import pystray._win32 as _pystray_win32  # noqa: F401
except Exception:  # pragma: no cover - exercised only without optional deps
    pystray = None
    Image = None
    ImageDraw = None
    _pystray_win32 = None


if pystray is not None:

    class _TrayIcon(pystray.Icon):
        """Restore the main window for Windows notification-area left clicks."""

        def __init__(self, *args, on_activate: Callable[[], None], **kwargs):
            self._on_activate = on_activate
            super().__init__(*args, **kwargs)

        def __call__(self):
            # The Win32 backend dispatches WM_LBUTTONUP through __call__.
            self._on_activate()

else:  # pragma: no cover - class is only used when pystray is available
    _TrayIcon = None


class TrayController:
    """Own a tray icon and marshal tray callbacks onto Tk's event loop."""

    def __init__(
        self,
        root,
        on_restore: Callable[[], None],
        on_lightweight: Callable[[bool], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ):
        self.root = root
        self.on_restore = on_restore
        self.on_lightweight = on_lightweight
        self.on_close = on_close
        self.lightweight_mode = False
        self.icon = None
        self._hidden = False
        self._closing = False
        self._start_error = None
        self._ready = threading.Event()

    @property
    def available(self) -> bool:
        """Whether the optional tray dependencies are importable."""

        return _TrayIcon is not None and Image is not None

    def start(self) -> bool:
        """Start the icon and wait until the Windows tray thread is ready."""

        if self.icon is not None:
            return self._ready.is_set() and self._start_error is None
        if not self.available or sys.platform != "win32":
            return False
        self._ready.clear()
        self._start_error = None
        try:
            image = self._create_image()
            menu = pystray.Menu(
                pystray.MenuItem("显示 AI Probe", self._restore_from_tray, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "轻量模式",
                    self._toggle_lightweight,
                    checked=lambda _item: self.lightweight_mode,
                    radio=True,
                ),
                pystray.MenuItem("退出", self._close_application),
            )
            self.icon = _TrayIcon(
                "ai-probe",
                image=image,
                title="AI Probe（单击显示窗口）",
                menu=menu,
                on_activate=self._restore_from_tray,
            )
            self.icon.run_detached(setup=self._on_started)
            if not self._ready.wait(timeout=2.0):
                raise RuntimeError("系统托盘启动超时")
            if self._start_error is not None:
                raise self._start_error
            return True
        except Exception:
            # Never hide the main window unless the tray icon is confirmed ready.
            self._start_error = self._start_error or RuntimeError("系统托盘启动失败")
            if self.icon is not None:
                with suppress(Exception):
                    self.icon.stop()
            self.icon = None
            return False

    def _on_started(self, icon) -> None:
        try:
            icon.visible = True
        except Exception as exc:
            self._start_error = exc
        finally:
            self._ready.set()

    @property
    def start_error(self):
        return self._start_error

    def stop(self) -> None:
        self._closing = True
        self._ready.clear()
        if self.icon is None:
            return
        with suppress(Exception):
            self.icon.stop()
        self.icon = None
        self._start_error = None
        self._closing = False

    def minimize(self) -> bool:
        """Hide the main window only after the tray icon is visible."""

        if not self.start():
            return False
        self._hidden = True
        try:
            self.root.withdraw()
        except Exception:
            self._hidden = False
            return False
        return True

    def handle_unmap(self, _event=None) -> None:
        """Move native Tk minimization into the tray when possible."""

        if self._closing or self._hidden:
            return
        try:
            if self.root.state() == "iconic":
                self.minimize()
        except Exception:
            pass

    def _restore_from_tray(self) -> None:
        with suppress(Exception):
            self.root.after(0, self._restore_on_tk_thread)

    def _restore_on_tk_thread(self) -> None:
        self._hidden = False
        try:
            self.root.deiconify()
            self.root.state("normal")
            self.root.lift()
            self.root.focus_force()
            self.on_restore()
        except Exception:
            pass

    def _toggle_lightweight(self, _icon=None, _item=None) -> None:
        enabled = not self.lightweight_mode

        def apply() -> None:
            self.lightweight_mode = enabled
            if self.on_lightweight is not None:
                self.on_lightweight(enabled)

        with suppress(Exception):
            self.root.after(0, apply)

    def _close_application(self, _icon=None, _item=None) -> None:
        """Run the owning application's normal close handler on Tk's thread."""

        with suppress(Exception):
            self.root.after(0, self.on_close or self.root.destroy)

    @staticmethod
    def _create_image():
        image = Image.new("RGBA", (64, 64), (0, 122, 255, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 8, 56, 56), radius=10, fill=(255, 255, 255, 255))
        draw.rectangle((20, 18, 44, 46), fill=(0, 122, 255, 255))
        draw.rectangle((25, 23, 39, 41), fill=(255, 255, 255, 255))
        return image
