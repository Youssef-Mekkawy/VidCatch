"""Design tokens. The palette is the original one; this module has no GUI imports."""

import sys

# Surfaces
BG = "#0F0F0F"
CARD = "#1A1A1A"
CARD2 = "#212121"
PANEL_BG = "#141414"
BORDER = "#2A2A2A"

# Accents
RED = "#FF0000"
RED_HOVER = "#CC0000"
GREEN = "#00C853"
BLUE = "#1565C0"
DANGER_HOVER = "#3D0000"

# Text
WHITE = "#FFFFFF"
GREY = "#AAAAAA"
GREY2 = "#555555"

# Geometry
WINDOW_SIZE = "900x740"
WINDOW_MIN_SIZE = (780, 660)
PANEL_WIDTH = 340
RADIUS = 10
THUMB_SIZE = (192, 108)
ROW_THUMB_SIZE = (128, 72)


def font_family() -> str:
    """Pick a UI font that exists on the current platform (Arabic-capable on all three)."""
    if sys.platform.startswith("win"):
        return "Segoe UI"
    if sys.platform == "darwin":
        return "Helvetica Neue"
    return "DejaVu Sans"
