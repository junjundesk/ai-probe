"""Shared mouse context menu for editable Tk widgets."""

from __future__ import annotations

from tkinter import END, Menu, TclError

_EDITABLE_CLASSES = ("Entry", "TEntry", "Text", "Spinbox", "TSpinbox", "TCombobox")


def install_edit_context_menu(root):
    """Install cut/copy/paste/select-all menus for every editable widget."""
    if getattr(root, "_edit_context_menu_installed", False):
        return

    menu = Menu(root, tearoff=False)
    root._edit_context_menu = menu
    root._edit_context_menu_widget = None

    def selection_present(widget):
        try:
            if widget.winfo_class() == "Text":
                return bool(widget.tag_ranges("sel"))
            return bool(widget.selection_present())
        except (TclError, AttributeError):
            return False

    def widget_state(widget):
        try:
            return str(widget.cget("state"))
        except (TclError, AttributeError):
            return "normal"

    def action(sequence):
        widget = root._edit_context_menu_widget
        if widget is None or not widget.winfo_exists():
            return
        widget.focus_set()
        try:
            widget.event_generate(sequence)
        except TclError:
            return

    def select_all():
        widget = root._edit_context_menu_widget
        if widget is None or not widget.winfo_exists():
            return
        widget.focus_set()
        try:
            if widget.winfo_class() == "Text":
                widget.tag_add("sel", "1.0", "end-1c")
                widget.mark_set("insert", "end-1c")
            else:
                widget.selection_range(0, END)
                widget.icursor(END)
        except (TclError, AttributeError):
            return

    menu.add_command(label="剪切", command=lambda: action("<<Cut>>"))
    menu.add_command(label="复制", command=lambda: action("<<Copy>>"))
    menu.add_command(label="粘贴", command=lambda: action("<<Paste>>"))
    menu.add_separator()
    menu.add_command(label="全选", command=select_all)

    def show_menu(event):
        widget = event.widget
        if widget.winfo_class() not in _EDITABLE_CLASSES:
            return

        try:
            index = (
                widget.index(f"@{event.x},{event.y}") if widget.winfo_class() == "Text" else widget.index(f"@{event.x}")
            )
            selected = selection_present(widget)
            if not selected:
                if widget.winfo_class() == "Text":
                    widget.mark_set("insert", index)
                else:
                    widget.icursor(index)
        except (TclError, AttributeError):
            selected = False

        root._edit_context_menu_widget = widget
        readonly = widget_state(widget) in {"readonly", "disabled"}
        menu.entryconfigure("剪切", state="normal" if selected and not readonly else "disabled")
        menu.entryconfigure("复制", state="normal" if selected else "disabled")
        menu.entryconfigure("粘贴", state="normal" if not readonly else "disabled")
        menu.entryconfigure("全选", state="normal")
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    for widget_class in _EDITABLE_CLASSES:
        root.bind_class(widget_class, "<Button-3>", show_menu, add="+")
    root._edit_context_menu_installed = True
