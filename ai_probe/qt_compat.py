"""Shared light theme and screen-aware layout helpers for the Qt UI."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QFrame, QScrollArea, QStyleFactory, QWidget

# Keep a little breathing room for the title bar and window borders, which are
# outside QScreen.availableGeometry() but still need to be visible.
SCREEN_MARGIN = 24

_THEME_MARKER = "_ai_probe_light_theme"

_ACTIVE_COLORS = {
    QPalette.ColorRole.Window: "#f7f9fc",
    QPalette.ColorRole.WindowText: "#1f2937",
    QPalette.ColorRole.Base: "#ffffff",
    QPalette.ColorRole.AlternateBase: "#f1f5f9",
    QPalette.ColorRole.ToolTipBase: "#fffdf5",
    QPalette.ColorRole.ToolTipText: "#1f2937",
    QPalette.ColorRole.Text: "#1f2937",
    QPalette.ColorRole.Button: "#ffffff",
    QPalette.ColorRole.ButtonText: "#1f2937",
    QPalette.ColorRole.BrightText: "#ffffff",
    QPalette.ColorRole.Link: "#0b5cab",
    QPalette.ColorRole.LinkVisited: "#7c3aed",
    QPalette.ColorRole.Highlight: "#1565c0",
    QPalette.ColorRole.HighlightedText: "#ffffff",
    QPalette.ColorRole.PlaceholderText: "#6b7280",
    QPalette.ColorRole.Light: "#ffffff",
    QPalette.ColorRole.Midlight: "#e5ebf3",
    QPalette.ColorRole.Dark: "#a7b4c8",
    QPalette.ColorRole.Mid: "#c4cfdd",
    QPalette.ColorRole.Shadow: "#7b8798",
}

# Unfocused selections stay visible without pretending the window is active.
_INACTIVE_COLORS = {
    **_ACTIVE_COLORS,
    QPalette.ColorRole.Highlight: "#cdddf1",
    QPalette.ColorRole.HighlightedText: "#1f2937",
}

_DISABLED_COLORS = {
    QPalette.ColorRole.Window: "#f3f5f8",
    QPalette.ColorRole.WindowText: "#7c8798",
    QPalette.ColorRole.Base: "#f7f8fa",
    QPalette.ColorRole.AlternateBase: "#eef1f5",
    QPalette.ColorRole.ToolTipBase: "#f3f5f8",
    QPalette.ColorRole.ToolTipText: "#7c8798",
    QPalette.ColorRole.Text: "#7c8798",
    QPalette.ColorRole.Button: "#eef1f5",
    QPalette.ColorRole.ButtonText: "#7c8798",
    QPalette.ColorRole.BrightText: "#7c8798",
    QPalette.ColorRole.Link: "#7c8798",
    QPalette.ColorRole.LinkVisited: "#7c8798",
    QPalette.ColorRole.Highlight: "#d9e0ea",
    QPalette.ColorRole.HighlightedText: "#7c8798",
    QPalette.ColorRole.PlaceholderText: "#a1aab8",
    QPalette.ColorRole.Light: "#ffffff",
    QPalette.ColorRole.Midlight: "#e2e7ee",
    QPalette.ColorRole.Dark: "#aab5c4",
    QPalette.ColorRole.Mid: "#c9d2de",
    QPalette.ColorRole.Shadow: "#8e9aab",
}

# This stylesheet intentionally contains no colors: Fusion plus the application
# palette paints controls, including their selected and disabled states.
_WINDOW_STYLESHEET = """
QGroupBox { font-weight: 600; margin-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QToolBar { spacing: 6px; padding: 5px; }
"""


def build_light_palette() -> QPalette:
    """Build the full palette used by every AI Probe window."""
    palette = QPalette()
    for group, colors in (
        (QPalette.ColorGroup.Active, _ACTIVE_COLORS),
        (QPalette.ColorGroup.Inactive, _INACTIVE_COLORS),
        (QPalette.ColorGroup.Disabled, _DISABLED_COLORS),
    ):
        for role, color in colors.items():
            palette.setColor(group, role, QColor(color))

    accent_role = getattr(QPalette.ColorRole, "Accent", None)
    if accent_role is not None:
        for group, color in (
            (QPalette.ColorGroup.Active, _ACTIVE_COLORS[QPalette.ColorRole.Highlight]),
            (QPalette.ColorGroup.Inactive, _INACTIVE_COLORS[QPalette.ColorRole.Highlight]),
            (QPalette.ColorGroup.Disabled, _DISABLED_COLORS[QPalette.ColorRole.Highlight]),
        ):
            palette.setColor(group, accent_role, QColor(color))
    return palette


def apply_light_theme(application: QApplication | None = None) -> QApplication:
    """Apply AI Probe's light theme once per QApplication.

    Fusion is deliberate rather than decorative: native platform styles may
    paint controls from the operating-system theme instead of QPalette, so a
    dark Windows theme can make a light palette unreadable. Fusion consistently
    honors the application palette for the standard QWidgets used here.

    The marker makes direct construction of windows safe without repeatedly
    resetting a theme (and other palettes) whenever a dialog is opened.
    """
    application = application or QApplication.instance()
    if not isinstance(application, QApplication):
        raise RuntimeError("创建窗口前必须存在 QApplication")
    if getattr(application, _THEME_MARKER, False):
        return application

    fusion = QStyleFactory.create("Fusion")
    if fusion is not None:
        application.setStyle(fusion)
    application.setPalette(build_light_palette())
    setattr(application, _THEME_MARKER, True)
    return application


def apply_window_style(window: QWidget) -> None:
    """Apply layout-only chrome and the current palette to one window.

    The explicit window palette makes direct construction safe even before the
    window is shown: Qt otherwise keeps a stale palette on a freshly created
    top-level widget in some offscreen/high-DPI environments. Colors still come
    from the application palette, so no global theme is reset here.

    Keeping this out of QApplication.setStyleSheet() leaves the platform style
    unwrapped, so the Fusion style and the application palette stay observable
    and consistent for message boxes that have no window-level stylesheet.
    """
    application = QApplication.instance()
    if isinstance(application, QApplication):
        window.setPalette(application.palette())
    window.setStyleSheet(_WINDOW_STYLESHEET)


def fit_window_to_screen(
    window: QWidget,
    preferred_size: tuple[int, int],
    minimum_size: tuple[int, int],
    *,
    screen=None,
    margin: int = SCREEN_MARGIN,
) -> None:
    """Size a window from logical screen pixels, clamping both sizes.

    availableGeometry() is already expressed in logical pixels, so this works
    with fractional/high-DPI scaling without multiplying by devicePixelRatio.
    """
    screen = screen or window.screen() or QApplication.primaryScreen()
    if screen is None:
        window.setMinimumSize(*minimum_size)
        window.resize(*preferred_size)
        return

    available = screen.availableGeometry()
    max_width = max(1, available.width() - margin * 2)
    max_height = max(1, available.height() - margin * 2)
    min_width = min(max(1, int(minimum_size[0])), max_width)
    min_height = min(max(1, int(minimum_size[1])), max_height)
    width = max(min_width, min(int(preferred_size[0]), max_width))
    height = max(min_height, min(int(preferred_size[1]), max_height))

    window.setMinimumSize(min_width, min_height)
    window.resize(width, height)


def scrollable(widget: QWidget, object_name: str = "") -> QScrollArea:
    """Put a widget in a frameless, resize-aware scroll area."""
    scroll = QScrollArea()
    if object_name:
        scroll.setObjectName(object_name)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setWidget(widget)
    return scroll
