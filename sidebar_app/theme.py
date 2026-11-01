"""Colours, stylesheet and drawn icons.

The default dark theme follows Notiq's look: deep navy surfaces, a
yellow primary accent, pill buttons, 12px rounded cards and a thin blue
edge strip.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

# Card colours, in menu order.
CARD_COLORS: dict[str, tuple[str, str]] = {
    "yellow": ("Yellow", "#fcc803"),
    "orange": ("Orange", "#ff9f43"),
    "red": ("Red", "#ff6b6b"),
    "pink": ("Pink", "#f78fb3"),
    "purple": ("Purple", "#a78bfa"),
    "blue": ("Blue", "#4d8dff"),
    "teal": ("Teal", "#2dd4bf"),
    "green": ("Green", "#4ade80"),
    "grey": ("Grey", "#9aa3bf"),
}

PRIORITY_COLORS = {1: "#4d8dff", 2: "#ff9f43", 3: "#ff6b6b"}


@dataclass(frozen=True)
class Palette:
    name: str
    panel: str
    panel_border: str
    surface: str        # header controls, inputs
    card_base: str
    card_tint: float    # how strongly the card colour tints the card
    text: str
    muted: str
    hover: str
    selection: str
    accent: str         # primary (yellow)
    accent_text: str
    strip: str          # edge strip (blue)
    strip_hover: str
    danger: str
    info: str


DARK = Palette(
    name="dark",
    panel="#0e1120",
    panel_border="#262a38",
    surface="#141723",
    card_base="#171b2a",
    card_tint=0.10,
    text="#eef1fb",
    muted="#9aa3bf",
    hover="#232838",
    selection="#2f4a8a",
    accent="#fcc803",
    accent_text="#1a1400",
    strip="#3d7dff",
    strip_hover="#6a9bff",
    danger="#ff6b6b",
    info="#6a9bff",
)

LIGHT = Palette(
    name="light",
    panel="#f5f6fa",
    panel_border="#dde0ea",
    surface="#ffffff",
    card_base="#ffffff",
    card_tint=0.16,
    text="#151826",
    muted="#5f6782",
    hover="#e8ebf3",
    selection="#b9cdf7",
    accent="#fcc803",
    accent_text="#1a1400",
    strip="#3d7dff",
    strip_hover="#1f5fe0",
    danger="#d93838",
    info="#2f6fe0",
)


def palette(name: str) -> Palette:
    return LIGHT if name == "light" else DARK


def blend(base: str, accent: str, amount: float) -> str:
    b, a = QColor(base), QColor(accent)
    mix = lambda x, y: round(x + (y - x) * amount)  # noqa: E731
    return QColor(mix(b.red(), a.red()), mix(b.green(), a.green()), mix(b.blue(), a.blue())).name()


def card_accent(color: str) -> str:
    return CARD_COLORS.get(color, CARD_COLORS["yellow"])[1]


def card_background(p: Palette, color: str) -> str:
    return blend(p.card_base, card_accent(color), p.card_tint)


def stylesheet(p: Palette) -> str:
    return f"""
    QWidget {{ color: {p.text}; font-size: 10pt; }}
    #panel {{
        background: {p.panel};
        border: 1px solid {p.panel_border};
        border-radius: 14px;
    }}
    #panelTitle {{ font-size: 13pt; font-weight: 700; }}
    QLabel[muted="true"] {{ color: {p.muted}; font-size: 9pt; }}
    QLabel[section="true"] {{
        color: {p.muted}; font-size: 8.5pt; font-weight: 700;
        letter-spacing: 0.5px; padding: 6px 2px 0 2px;
    }}
    QLabel[badge="true"] {{
        border-radius: 8px; padding: 1px 7px; font-size: 8.5pt; font-weight: 600;
    }}
    QLineEdit, QTextEdit, QPlainTextEdit {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 6px;
        padding: 2px 4px;
        selection-background-color: {p.selection};
    }}
    QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover {{ border: 1px solid {p.panel_border}; }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {p.strip}; }}
    #search {{
        background: {p.surface};
        border: 1px solid {p.panel_border};
        border-radius: 17px;
        padding: 6px 12px;
    }}
    #search:focus {{ border: 1px solid {p.strip}; }}
    #cardTitle {{ font-weight: 600; font-size: 10.5pt; }}
    #code {{
        background: {blend(p.card_base, '#000000', 0.25 if p.name == 'dark' else 0.04)};
        border-radius: 8px; font-family: "Cascadia Code", Consolas, "Courier New", monospace;
        font-size: 9.5pt;
    }}
    QToolButton {{
        background: transparent; border: none; border-radius: 6px; padding: 3px;
    }}
    QToolButton:hover {{ background: {p.hover}; }}
    QToolButton:checked {{ background: {p.hover}; }}
    QToolButton::menu-indicator {{ image: none; width: 0; }}
    QToolButton[view="true"] {{
        color: {p.muted}; border-radius: 13px; padding: 4px 10px; font-weight: 600;
    }}
    QToolButton[view="true"]:checked {{ background: {p.surface}; color: {p.text}; }}
    QPushButton {{
        background: {p.surface}; color: {p.text};
        border: 1px solid {p.panel_border}; border-radius: 15px; padding: 5px 14px;
    }}
    QPushButton:hover {{ background: {p.hover}; }}
    QPushButton[primary="true"] {{
        background: {p.accent}; color: {p.accent_text}; border: none; font-weight: 700;
    }}
    QPushButton[primary="true"]:hover {{ background: {blend(p.accent, '#ffffff', 0.2)}; }}
    QPushButton[flat="true"] {{ background: transparent; border: none; color: {p.muted}; }}
    QPushButton[flat="true"]:hover {{ color: {p.text}; background: {p.hover}; }}
    #banner {{
        background: {p.surface}; border: 1px solid {p.panel_border}; border-radius: 12px;
    }}
    #banner[kind="reminder"] {{ border: 1px solid {p.accent}; }}
    #banner[kind="link"] {{ border: 1px solid {p.strip}; }}
    QTabBar {{ qproperty-drawBase: 0; }}
    QTabBar::tab {{
        background: transparent; color: {p.muted}; border: none;
        border-radius: 12px; padding: 4px 11px; margin-right: 2px;
    }}
    QTabBar::tab:hover {{ background: {p.hover}; color: {p.text}; }}
    QTabBar::tab:selected {{ background: {p.surface}; color: {p.text}; font-weight: 600; }}
    QTabBar QToolButton {{ background: {p.panel}; }}
    QScrollArea, #cardList {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {p.panel_border}; border-radius: 3px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {p.muted}; }}
    QScrollBar::add-line, QScrollBar::sub-line,
    QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
    QCheckBox {{ spacing: 6px; }}
    QMenu {{
        background: {p.surface}; border: 1px solid {p.panel_border};
        border-radius: 8px; padding: 4px;
    }}
    QMenu::item {{ padding: 5px 22px 5px 10px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {p.hover}; }}
    QMenu::item:disabled {{ color: {p.muted}; }}
    QMenu::separator {{ height: 1px; background: {p.panel_border}; margin: 4px 6px; }}
    QDialog {{ background: {p.panel}; }}
    QDialog QComboBox, QDialog QSpinBox, QDialog QLineEdit, QDialog QDateTimeEdit {{
        background: {p.surface}; border: 1px solid {p.panel_border};
        border-radius: 6px; padding: 4px 6px;
    }}
    QCalendarWidget QWidget {{ background: {p.surface}; alternate-background-color: {p.surface}; }}
    QCalendarWidget QAbstractItemView {{ selection-background-color: {p.strip}; }}
    """


# ------------------------------------------------------------------- icons
@lru_cache(maxsize=512)
def make_icon(name: str, color: str, size: int = 16) -> QIcon:
    """Line icons drawn with QPainter so they look identical everywhere.
    Cached: the same few icons and colours are used by every card."""
    icon = QIcon()
    for scale in (1, 2):
        icon.addPixmap(_draw(name, QColor(color), size, scale))
    return icon


def _poly(p: QPainter, *pts: tuple[float, float], close: bool = False) -> None:
    path = QPainterPath(QPointF(*pts[0]))
    for pt in pts[1:]:
        path.lineTo(QPointF(*pt))
    if close:
        path.closeSubpath()
    p.drawPath(path)


def _draw(name: str, color: QColor, size: int, scale: int) -> QPixmap:
    pm = QPixmap(size * scale, size * scale)
    pm.setDevicePixelRatio(scale)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    s = float(size)
    c = s / 2
    S = lambda *v: tuple(x * s for x in v)  # noqa: E731

    if name == "plus":
        _poly(p, S(0.5, 0.2), S(0.5, 0.8))
        _poly(p, S(0.2, 0.5), S(0.8, 0.5))
    elif name == "close":
        _poly(p, S(0.28, 0.28), S(0.72, 0.72))
        _poly(p, S(0.72, 0.28), S(0.28, 0.72))
    elif name == "check":
        _poly(p, S(0.22, 0.52), S(0.42, 0.72), S(0.78, 0.3))
    elif name == "more":
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        for x in (0.25, 0.5, 0.75):
            p.drawEllipse(QPointF(s * x, c), 1.5, 1.5)
    elif name == "grip":
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        for x in (0.38, 0.62):
            for y in (0.25, 0.5, 0.75):
                p.drawEllipse(QPointF(s * x, s * y), 1.3, 1.3)
    elif name == "chevron_right":
        _poly(p, S(0.4, 0.25), S(0.65, 0.5), S(0.4, 0.75))
    elif name == "chevron_down":
        _poly(p, S(0.25, 0.4), S(0.5, 0.65), S(0.75, 0.4))
    elif name == "open":
        _poly(p, S(0.4, 0.6), S(0.78, 0.22))
        _poly(p, S(0.5, 0.22), S(0.78, 0.22), S(0.78, 0.5))
        _poly(p, S(0.6, 0.8), S(0.2, 0.8), S(0.2, 0.4))
    elif name in ("pin", "pinned"):
        p.translate(c, c)
        p.rotate(45)
        p.translate(-c, -c)
        if name == "pinned":
            p.setBrush(color)
        p.drawRoundedRect(QRectF(*S(0.36, 0.12, 0.28, 0.36)), 1.5, 1.5)
        _poly(p, S(0.24, 0.5), S(0.76, 0.5))
        _poly(p, S(0.5, 0.5), S(0.5, 0.9))
    elif name == "settings":
        p.drawEllipse(QPointF(c, c), s * 0.13, s * 0.13)
        p.drawEllipse(QPointF(c, c), s * 0.27, s * 0.27)
        for i in range(8):
            p.save()
            p.translate(c, c)
            p.rotate(i * 45)
            p.drawLine(QPointF(0, -s * 0.27), QPointF(0, -s * 0.4))
            p.restore()
    elif name == "dot":
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(c, c), s * 0.32, s * 0.32)
    elif name == "bell":
        path = QPainterPath(QPointF(*S(0.22, 0.7)))
        path.lineTo(QPointF(*S(0.28, 0.62)))
        path.cubicTo(QPointF(*S(0.28, 0.2)), QPointF(*S(0.72, 0.2)), QPointF(*S(0.72, 0.62)))
        path.lineTo(QPointF(*S(0.78, 0.7)))
        path.closeSubpath()
        p.drawPath(path)
        _poly(p, S(0.43, 0.82), S(0.57, 0.82))
    elif name == "archive":
        p.drawRoundedRect(QRectF(*S(0.16, 0.2, 0.68, 0.18)), 1.5, 1.5)
        _poly(p, S(0.22, 0.38), S(0.22, 0.8), S(0.78, 0.8), S(0.78, 0.38))
        _poly(p, S(0.42, 0.52), S(0.58, 0.52))
    elif name == "today":
        p.drawRoundedRect(QRectF(*S(0.16, 0.22, 0.68, 0.6)), 2, 2)
        _poly(p, S(0.16, 0.4), S(0.84, 0.4))
        _poly(p, S(0.34, 0.14), S(0.34, 0.28))
        _poly(p, S(0.66, 0.14), S(0.66, 0.28))
        p.setBrush(color)
        p.drawRect(QRectF(*S(0.32, 0.52, 0.14, 0.12)))
    elif name == "cards":
        p.drawRoundedRect(QRectF(*S(0.18, 0.18, 0.64, 0.26)), 2, 2)
        p.drawRoundedRect(QRectF(*S(0.18, 0.56, 0.64, 0.26)), 2, 2)
    elif name == "link":
        p.drawRoundedRect(QRectF(*S(0.12, 0.38, 0.42, 0.24)), s * 0.12, s * 0.12)
        p.drawRoundedRect(QRectF(*S(0.46, 0.38, 0.42, 0.24)), s * 0.12, s * 0.12)
    elif name == "copy":
        p.drawRoundedRect(QRectF(*S(0.32, 0.32, 0.48, 0.5)), 2, 2)
        _poly(p, S(0.2, 0.66), S(0.2, 0.18), S(0.62, 0.18))
    elif name == "lock":
        p.drawRoundedRect(QRectF(*S(0.24, 0.46, 0.52, 0.38)), 2, 2)
        path = QPainterPath(QPointF(*S(0.34, 0.46)))
        path.lineTo(QPointF(*S(0.34, 0.34)))
        path.cubicTo(QPointF(*S(0.34, 0.12)), QPointF(*S(0.66, 0.12)), QPointF(*S(0.66, 0.34)))
        path.lineTo(QPointF(*S(0.66, 0.46)))
        p.drawPath(path)
    elif name == "focus":
        p.drawEllipse(QPointF(c, c), s * 0.32, s * 0.32)
        _poly(p, S(0.5, 0.3), S(0.5, 0.5), S(0.64, 0.6))
    elif name == "headphones":
        path = QPainterPath(QPointF(*S(0.2, 0.62)))
        path.lineTo(QPointF(*S(0.2, 0.5)))
        path.cubicTo(QPointF(*S(0.2, 0.1)), QPointF(*S(0.8, 0.1)), QPointF(*S(0.8, 0.5)))
        path.lineTo(QPointF(*S(0.8, 0.62)))
        p.drawPath(path)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(*S(0.14, 0.56, 0.16, 0.26)), 2, 2)
        p.drawRoundedRect(QRectF(*S(0.7, 0.56, 0.16, 0.26)), 2, 2)
    elif name == "note":
        _poly(p, S(0.24, 0.16), S(0.62, 0.16), S(0.78, 0.32), S(0.78, 0.84), S(0.24, 0.84), close=True)
        _poly(p, S(0.36, 0.5), S(0.64, 0.5))
        _poly(p, S(0.36, 0.66), S(0.56, 0.66))
    elif name == "checklist":
        p.drawRoundedRect(QRectF(*S(0.16, 0.2, 0.2, 0.2)), 1.5, 1.5)
        p.drawRoundedRect(QRectF(*S(0.16, 0.6, 0.2, 0.2)), 1.5, 1.5)
        _poly(p, S(0.46, 0.3), S(0.84, 0.3))
        _poly(p, S(0.46, 0.7), S(0.84, 0.7))
    elif name == "code":
        _poly(p, S(0.34, 0.28), S(0.14, 0.5), S(0.34, 0.72))
        _poly(p, S(0.66, 0.28), S(0.86, 0.5), S(0.66, 0.72))
        _poly(p, S(0.56, 0.2), S(0.44, 0.8))
    elif name == "image":
        p.drawRoundedRect(QRectF(*S(0.14, 0.2, 0.72, 0.6)), 2, 2)
        _poly(p, S(0.2, 0.74), S(0.42, 0.5), S(0.58, 0.64), S(0.68, 0.56), S(0.82, 0.7))
        p.drawEllipse(QPointF(*S(0.64, 0.36)), s * 0.06, s * 0.06)
    elif name == "file":
        _poly(p, S(0.26, 0.14), S(0.58, 0.14), S(0.76, 0.32), S(0.76, 0.86), S(0.26, 0.86), close=True)
        _poly(p, S(0.58, 0.14), S(0.58, 0.32), S(0.76, 0.32))
    elif name == "links":
        _poly(p, S(0.42, 0.58), S(0.58, 0.42))
        p.drawRoundedRect(QRectF(*S(0.14, 0.5, 0.36, 0.22)), s * 0.1, s * 0.1)
        p.drawRoundedRect(QRectF(*S(0.5, 0.28, 0.36, 0.22)), s * 0.1, s * 0.1)
    elif name == "play":
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        path = QPainterPath(QPointF(*S(0.32, 0.2)))
        path.lineTo(QPointF(*S(0.8, 0.5)))
        path.lineTo(QPointF(*S(0.32, 0.8)))
        path.closeSubpath()
        p.drawPath(path)
    elif name == "stop":
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(*S(0.26, 0.26, 0.48, 0.48)), 2, 2)
    elif name == "power":
        path = QPainterPath()
        ring = QRectF(*S(0.18, 0.2, 0.64, 0.64))
        path.arcMoveTo(ring, 120)
        path.arcTo(ring, 120, 300)
        p.drawPath(path)
        _poly(p, S(0.5, 0.12), S(0.5, 0.46))
    elif name == "secret":
        return _draw("lock", color, size, scale)
    p.end()
    return pm


def app_icon() -> QIcon:
    """Tray / window icon: a dark tile with stacked cards and a blue edge."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        s = float(size)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(DARK.surface))
        p.drawRoundedRect(QRectF(0, 0, s, s), s * 0.22, s * 0.22)
        p.setBrush(QColor(DARK.strip))
        p.drawRoundedRect(QRectF(s * 0.8, s * 0.18, s * 0.08, s * 0.64), s * 0.04, s * 0.04)
        for i, col in enumerate((DARK.accent, "#eef1fb", "#9aa3bf")):
            p.setBrush(QColor(col))
            p.drawRoundedRect(QRectF(s * 0.14, s * (0.18 + i * 0.24), s * 0.56, s * 0.16),
                              s * 0.05, s * 0.05)
        p.end()
        icon.addPixmap(pm)
    return icon
