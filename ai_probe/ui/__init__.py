"""Tkinter 用户界面."""

__all__ = ["ProbeApp"]


def __getattr__(name):
    if name == "ProbeApp":
        from .probe_app import ProbeApp

        return ProbeApp
    raise AttributeError(name)
