"""可复用的轻量 Tkinter 控件。"""

from __future__ import annotations

from tkinter import Canvas, Frame
from tkinter import font as tkfont


class MacButton(Frame):
    """A small canvas button with macOS-like pill geometry and direct feedback."""

    PALETTE = {
        "primary": ("#007aff", "#006fe6", "#005ecb", "#ffffff"),
        "secondary": ("#e5e5ea", "#d1d1d6", "#c7c7cc", "#1d1d1f"),
        "danger": ("#fff0ef", "#ffe1df", "#ffd2cf", "#c9342b"),
    }

    def __init__(
        self, master, text="", command=None, kind="secondary", surface="#ffffff", width=None, height=32, **kwargs
    ):
        super().__init__(master, bg=surface, highlightthickness=0, bd=0)
        self.command = command
        self.kind = kind
        self.surface = surface
        self._disabled = False
        self._pressed = False
        self._hovered = False
        self._label = text
        self._draw_key = None
        self._font = tkfont.Font(family="Microsoft YaHei UI", size=9)
        requested_width = width if width is not None else self._font.measure(text) + 28
        if width is not None and width <= 8:
            requested_width = max(30, width * 12)
        self.canvas = Canvas(self, width=requested_width, height=height, bg=surface, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self._draw())
        self.canvas.bind("<Enter>", self._on_enter)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self._draw()

    def _colors(self):
        normal, hover, pressed, foreground = self.PALETTE[self.kind]
        if self._disabled:
            return "#e9e9ed", "#a1a1a6"
        if self._pressed:
            return pressed, foreground
        if self._hovered:
            return hover, foreground
        return normal, foreground

    def _draw(self):
        width = max(30, self.canvas.winfo_width() or int(self.canvas.cget("width")))
        height = max(26, self.canvas.winfo_height() or int(self.canvas.cget("height")))
        background, foreground = self._colors()
        radius = min(12, height // 2)
        draw_key = (width, height, background, foreground, self._label)
        if draw_key == self._draw_key:
            return
        self._draw_key = draw_key
        self.canvas.delete("all")
        self.canvas.create_rectangle(radius, 0, width - radius, height, fill=background, outline=background)
        self.canvas.create_rectangle(0, radius, width, height - radius, fill=background, outline=background)
        self.canvas.create_arc(0, 0, radius * 2, radius * 2, start=90, extent=90, fill=background, outline=background)
        self.canvas.create_arc(
            width - radius * 2, 0, width, radius * 2, start=0, extent=90, fill=background, outline=background
        )
        self.canvas.create_arc(
            0, height - radius * 2, radius * 2, height, start=180, extent=90, fill=background, outline=background
        )
        self.canvas.create_arc(
            width - radius * 2,
            height - radius * 2,
            width,
            height,
            start=270,
            extent=90,
            fill=background,
            outline=background,
        )
        self.canvas.create_text(width / 2, height / 2, text=self._label, fill=foreground, font=self._font)

    def _on_enter(self, _event):
        if not self._disabled:
            self._hovered = True
            self._draw()

    def _on_leave(self, _event):
        self._hovered = False
        self._pressed = False
        self._draw()

    def _on_press(self, _event):
        if not self._disabled:
            self._pressed = True
            self._draw()

    def _on_release(self, event):
        was_pressed = self._pressed
        self._pressed = False
        self._draw()
        if (
            was_pressed
            and not self._disabled
            and 0 <= event.x <= self.canvas.winfo_width()
            and 0 <= event.y <= self.canvas.winfo_height()
            and self.command
        ):
            self.command()

    def state(self, states=None):
        if states is None:
            return ("disabled",) if self._disabled else ()
        if "disabled" in states:
            self._disabled = True
        if "!disabled" in states:
            self._disabled = False
        self._draw()

    def set_text(self, text: str):
        if text == self._label:
            return
        self._label = text
        self._draw()
