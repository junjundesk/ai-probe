"""Compatibility export for the PySide6 desktop UI."""

__all__ = ["ProbeApp"]


def __getattr__(name):
    if name == "ProbeApp":
        from ..qt_app import QtMainWindow

        return QtMainWindow
    raise AttributeError(name)
