"""Material Design 3 inspired light theme for the existing Tk interface."""

SURFACE = "#fef7ff"
SURFACE_CONTAINER_LOWEST = "#ffffff"
SURFACE_CONTAINER_LOW = "#f7f2fa"
SURFACE_CONTAINER = "#f3edf7"
SURFACE_CONTAINER_HIGH = "#ece6f0"
ON_SURFACE = "#1d1b20"
ON_SURFACE_VARIANT = "#49454f"
OUTLINE_VARIANT = "#cac4d0"
PRIMARY = "#6750a4"
ON_PRIMARY = "#ffffff"
SECONDARY_CONTAINER = "#e8def8"
ON_SECONDARY_CONTAINER = "#1d192b"
ERROR = "#b3261e"
ERROR_CONTAINER = "#f9dedc"


def apply_md3_styles(root, style):
    """Style ttk and defaults for newly created classic Tk editors."""
    for widget in ("Text", "Listbox"):
        for option, value in {
            "background": SURFACE_CONTAINER_LOWEST,
            "foreground": ON_SURFACE,
            "selectBackground": SECONDARY_CONTAINER,
            "selectForeground": ON_SECONDARY_CONTAINER,
            "highlightBackground": OUTLINE_VARIANT,
            "highlightColor": PRIMARY,
            "borderWidth": 0,
        }.items():
            root.option_add(f"*{widget}.{option}", value)
    root.option_add("*Text.insertBackground", PRIMARY)
    root.option_add("*Text.font", "{Microsoft YaHei UI} 10")
    style.configure("TNotebook", background=SURFACE, borderwidth=0, tabmargins=(4, 8, 4, 0))
    style.configure(
        "TNotebook.Tab",
        background=SURFACE_CONTAINER,
        foreground=ON_SURFACE_VARIANT,
        padding=(20, 10),
        font=("Microsoft YaHei UI", 10),
        borderwidth=0,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", SECONDARY_CONTAINER), ("active", SURFACE_CONTAINER_HIGH)],
        foreground=[("selected", PRIMARY)],
    )
    style.configure(
        "TEntry", padding=(10, 8), bordercolor=OUTLINE_VARIANT, lightcolor=OUTLINE_VARIANT, darkcolor=OUTLINE_VARIANT
    )
    style.map(
        "TEntry", bordercolor=[("focus", PRIMARY)], lightcolor=[("focus", PRIMARY)], darkcolor=[("focus", PRIMARY)]
    )
    style.configure(
        "TCombobox",
        padding=(8, 6),
        fieldbackground=SURFACE_CONTAINER_LOW,
        foreground=ON_SURFACE,
        arrowcolor=PRIMARY,
        bordercolor=OUTLINE_VARIANT,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", SURFACE_CONTAINER_LOW)],
        selectbackground=[("readonly", SECONDARY_CONTAINER)],
        selectforeground=[("readonly", ON_SURFACE)],
    )
    style.configure("TButton", background=SECONDARY_CONTAINER, foreground=ON_SECONDARY_CONTAINER, padding=(16, 8))
    style.map(
        "TButton",
        background=[("disabled", SURFACE_CONTAINER_HIGH), ("pressed", "#d0bcff"), ("active", "#ded3ef")],
        foreground=[("disabled", "#938f99")],
    )
    style.configure("Accent.TButton", background=PRIMARY, foreground=ON_PRIMARY)
    style.map(
        "Accent.TButton",
        background=[("disabled", SURFACE_CONTAINER_HIGH), ("pressed", "#4f378b"), ("active", "#7965af")],
        foreground=[("disabled", "#938f99")],
    )
    style.configure("Treeview", rowheight=34)
    style.map("Treeview.Heading", background=[("active", SECONDARY_CONTAINER)])
    style.configure("TSeparator", background=OUTLINE_VARIANT)
    style.configure(
        "Vertical.TScrollbar",
        background=OUTLINE_VARIANT,
        troughcolor=SURFACE_CONTAINER_LOW,
        borderwidth=0,
        arrowsize=13,
        arrowcolor=ON_SURFACE_VARIANT,
    )
    style.map("Vertical.TScrollbar", background=[("active", "#938f99"), ("pressed", PRIMARY)])
    style.configure(
        "Horizontal.TProgressbar",
        background=PRIMARY,
        troughcolor=SECONDARY_CONTAINER,
        borderwidth=0,
        lightcolor=PRIMARY,
        darkcolor=PRIMARY,
    )
    style.map("TCheckbutton", background=[("active", SURFACE_CONTAINER_LOW)], foreground=[("disabled", "#938f99")])
    style.configure("StatusBusy.TLabel", background=SECONDARY_CONTAINER, foreground=PRIMARY)
    style.configure("StatusError.TLabel", background=ERROR_CONTAINER, foreground=ERROR)
