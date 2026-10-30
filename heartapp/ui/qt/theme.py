"""设计令牌：克制、精密的浅色主题（Apple Health / Linear 那一类）。

设计原则
--------
上一版用的是高饱和多色渐变 + 重磨砂玻璃，那是"炫"，不是"高级"。真正的高级感来自
**克制**：

* **近中性底色**：页面是一层几乎看不出色相的冷灰渐变，不用彩虹色。
* **纯白卡片**：白卡浮在浅灰页面上，靠 1px 发丝描边 + 极轻投影表达层级。
* **唯一强调色**：深玫红只出现在「当前心率 / 主按钮 / 选中态 / 曲线」，其余全是灰阶。
* **排版层级**：字号/字重/颜色三级分明，数字用等宽数字避免跳动。
* **留白**：统一按 4 的倍数取间距，宁松勿紧。

这里不放任何模糊/透明度魔法 —— Qt 清晰的矢量绘制配上克制的配色，
比"半透明磨砂"更显质感。
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)

Color = str
Stop = Tuple[float, Color]

# ---------------------------------------------------------------------------
# 色板（near-monochrome + 一个强调色）
# ---------------------------------------------------------------------------

#: 页面底色（竖直渐变，几乎无色相）
PAGE_TOP = "#FAFAFB"
PAGE_MID = "#F4F4F7"
PAGE_BOTTOM = "#EEEFF3"

#: 卡片
SURFACE = "#FFFFFF"
SURFACE_SUNK = "#F7F7F9"

BORDER = "#E7E7EC"
BORDER_STRONG = "#D9D9E0"
DIVIDER = "#EFEFF3"

#: 文字（四级灰阶）
INK = "#15161A"
INK_2 = "#5A5D66"
INK_3 = "#8B8E98"
INK_4 = "#B5B8C1"

#: 强调色：深玫红。只用于「心率数值 / 主按钮 / 选中态 / 曲线」
ACCENT = "#C9184A"
ACCENT_PRESSED = "#A5113B"
ACCENT_SOFT = "#FBEAEF"

#: 近黑：主按钮用，比纯黑柔和，是看起来最"高级"的按钮色
INK_SOLID = "#1A1B20"
INK_SOLID_HOVER = "#2B2D34"

#: 语义色（低饱和）
SUCCESS = "#2E8B62"
WARNING = "#A9741C"
DANGER = "#B3312E"

#: 兼容旧名字
PRIMARY = ACCENT
ON_PRIMARY = "#FFFFFF"
PRIMARY_CONTAINER = ACCENT_SOFT
ON_PRIMARY_CONTAINER = "#5C0A22"
SECONDARY = INK_2
SECONDARY_CONTAINER = "#EFEFF3"
ON_SECONDARY_CONTAINER = INK
ON_SURFACE = INK
ON_SURFACE_VARIANT = INK_2
OUTLINE = BORDER_STRONG
OUTLINE_VARIANT = BORDER
TEXT = INK
MUTED = INK_2
FAINT = INK_3
ERROR = DANGER

STATE_IDLE = INK_3
STATE_CONNECTING = WARNING
STATE_CONNECTED = SUCCESS
STATE_RECORDING = ACCENT
STATE_ERROR = DANGER

#: 心率区间色：刻意降低饱和度。六个颜色要在同一"灰度水平"上，
#: 只要有一个特别跳，整体就花了。
ZONE_COLORS = {
    "静息": "#9AA1AE",
    "Z1 恢复": "#5B8FB9",
    "Z2 燃脂": "#4C9A78",
    "Z3 有氧": "#B08A3C",
    "Z4 无氧": "#BE6B44",
    "Z5 极限": "#B04A55",
}

#: 区间浅底（小标签用）
ZONE_SOFT = {
    "静息": "#F1F2F5",
    "Z1 恢复": "#EDF3F8",
    "Z2 燃脂": "#EDF5F1",
    "Z3 有氧": "#F7F3E9",
    "Z4 无氧": "#F9F0EB",
    "Z5 极限": "#F8ECEE",
}

#: 背景渐变
BACKDROP_STOPS: List[Stop] = [
    (0.00, PAGE_TOP),
    (0.55, PAGE_MID),
    (1.00, PAGE_BOTTOM),
]

#: 兼容旧名字
GLASS_ALPHA = 0.97
BLUR_STRENGTH = 16

# ---------------------------------------------------------------------------
# 圆角 / 间距 / 字体
# ---------------------------------------------------------------------------

RADIUS_CARD = 18
RADIUS_PANEL = 14
RADIUS_SMALL = 10

#: 统一间距标尺（4 的倍数）
SPACE = {
    "xs": 4,
    "sm": 8,
    "md": 12,
    "lg": 16,
    "xl": 20,
    "xxl": 24,
    "xxxl": 32,
}

TYPE_SCALE = {
    "appTitle": (17, QFont.Weight.DemiBold),
    "title": (14, QFont.Weight.DemiBold),
    "section": (11, QFont.Weight.DemiBold),
    "body": (10, QFont.Weight.Normal),
    "bodyStrong": (10, QFont.Weight.DemiBold),
    "caption": (9, QFont.Weight.Normal),
    "label": (9, QFont.Weight.DemiBold),
    "micro": (8, QFont.Weight.Normal),
    "metric": (22, QFont.Weight.DemiBold),
    # 以下为旧角色名，保留以免散落在别处的调用失效
    "displaySmall": (24, QFont.Weight.DemiBold),
    "headlineSmall": (17, QFont.Weight.DemiBold),
    "titleLarge": (14, QFont.Weight.DemiBold),
    "titleMedium": (12, QFont.Weight.DemiBold),
    "bodyLarge": (11, QFont.Weight.Normal),
    "bodyMedium": (10, QFont.Weight.Normal),
    "labelLarge": (10, QFont.Weight.DemiBold),
    "labelMedium": (9, QFont.Weight.Normal),
    "labelSmall": (8, QFont.Weight.Normal),
}

_PREFERRED_FAMILIES = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Segoe UI Variable Text",
    "Segoe UI",
    "SimHei",
)

_family_cache: List[str] = []


def ui_family() -> str:
    if _family_cache:
        return _family_cache[0]
    available = set(QFontDatabase.families())
    chosen = ""
    for name in _PREFERRED_FAMILIES:
        if name in available:
            chosen = name
            break
    if not chosen:
        chosen = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
    _family_cache.append(chosen)
    return chosen


def font(role: str = "body") -> QFont:
    size, weight = TYPE_SCALE.get(role, (10, QFont.Weight.Normal))
    f = QFont(ui_family())
    f.setPointSize(max(1, size))
    f.setWeight(weight)
    return f


def font_px(px: int, weight: QFont.Weight = QFont.Weight.DemiBold) -> QFont:
    f = QFont(ui_family())
    f.setPixelSize(max(1, int(px)))
    f.setWeight(weight)
    return f


def spaced_font(role: str, spacing: float) -> QFont:
    """带字距的字体。标题用负字距会显得更紧致。"""
    f = font(role)
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


def mono_family() -> str:
    available = set(QFontDatabase.families())
    for name in ("Cascadia Mono", "Consolas", "JetBrains Mono", "Courier New"):
        if name in available:
            return name
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()


def mono_font(size: int = 9) -> QFont:
    f = QFont(mono_family())
    f.setPointSize(size)
    return f


# ---------------------------------------------------------------------------
# 颜色工具
# ---------------------------------------------------------------------------


def color(value: Color, alpha: float = 1.0) -> QColor:
    c = QColor(value)
    if alpha < 1.0:
        c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def mix(a: Color, b: Color, t: float) -> QColor:
    ca, cb = QColor(a), QColor(b)
    t = max(0.0, min(1.0, t))
    return QColor(
        int(ca.red() + (cb.red() - ca.red()) * t),
        int(ca.green() + (cb.green() - ca.green()) * t),
        int(ca.blue() + (cb.blue() - ca.blue()) * t),
    )


def darken(value: Color, amount: float) -> QColor:
    return mix(value, "#000000", amount)


def lighten(value: Color, amount: float) -> QColor:
    return mix(value, "#FFFFFF", amount)


def tint_surface(base: QColor, amount: float) -> QColor:
    """把底色往白色拉，用于卡片内部再分层。"""
    return mix(base.name(), "#FFFFFF", max(0.0, min(1.0, amount)))


# ---------------------------------------------------------------------------
# 背景
# ---------------------------------------------------------------------------


def build_backdrop(width: int, height: int) -> QPixmap:
    """页面底色：几乎看不出色相的冷灰渐变 + 两团极淡光晕。

    目的是让纯白卡片有"浮起来"的余地，而不是让背景本身抢戏。
    """
    width = max(2, int(width))
    height = max(2, int(height))
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    gradient = QLinearGradient(0, 0, 0, height)
    for position, value in BACKDROP_STOPS:
        gradient.setColorAt(position, QColor(value))
    painter.fillRect(QRect(0, 0, width, height), QBrush(gradient))

    # 极淡的强调色光晕（右上）与冷色光晕（左下），只为了不"死板"
    _soft_glow(painter, width * 0.86, -height * 0.05,
               max(width, height) * 0.55, QColor(ACCENT), 0.055)
    _soft_glow(painter, width * 0.05, height * 1.02,
               max(width, height) * 0.50, QColor("#5B6BD6"), 0.045)
    painter.end()
    return pixmap


def _soft_glow(painter: QPainter, cx: float, cy: float, radius: float,
               value: QColor, strength: float) -> None:
    gradient = QRadialGradient(QPointF(cx, cy), max(1.0, radius))
    inner = QColor(value)
    inner.setAlphaF(strength)
    middle = QColor(value)
    middle.setAlphaF(strength * 0.35)
    outer = QColor(value)
    outer.setAlphaF(0.0)
    gradient.setColorAt(0.0, inner)
    gradient.setColorAt(0.45, middle)
    gradient.setColorAt(1.0, outer)
    painter.setBrush(QBrush(gradient))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QPointF(cx, cy), radius, radius)


def blur_region(source: QPixmap, rect: QRect, strength: int = BLUR_STRENGTH) -> QPixmap:
    """保留接口（旧代码在用）。新主题里卡片是纯白，不再需要真的模糊。"""
    if source.isNull() or rect.width() <= 0 or rect.height() <= 0:
        return QPixmap()
    return source.copy(rect)


# ---------------------------------------------------------------------------
# 绘制助手
# ---------------------------------------------------------------------------


def rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def paint_shadow(painter: QPainter, rect: QRectF, radius: float,
                 spread: float = 12.0, strength: float = 1.0) -> None:
    """极轻的多层投影。高级感的投影永远"几乎看不见"。"""
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    layers = 4
    for i in range(layers, 0, -1):
        grow = spread * (i / layers)
        alpha = 0.016 * strength * (1.0 - (i / (layers + 1.0))) ** 1.4
        painter.setBrush(color("#1A1B20", alpha))
        painter.drawRoundedRect(
            rect.adjusted(-grow * 0.45, grow * 0.28, grow * 0.45, grow * 0.75),
            radius + grow * 0.4, radius + grow * 0.4,
        )
    painter.restore()


def paint_card(painter: QPainter, rect: QRectF, radius: float,
               fill: Color = SURFACE, border: Color = BORDER,
               border_alpha: float = 1.0) -> QPainterPath:
    """白卡：底色 + 1px 发丝描边。"""
    path = rounded_path(rect, radius)
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color(fill))
    painter.drawPath(path)
    painter.restore()

    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(color(border, border_alpha), 1.0))
    painter.drawPath(rounded_path(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius))
    painter.restore()
    return path


def paint_soft_shadow(painter: QPainter, rect: QRectF, radius: float,
                      layers: int = 5, spread: float = 10.0,
                      strength: float = 0.16) -> None:
    """兼容旧接口。"""
    paint_shadow(painter, rect, radius, spread=spread, strength=max(0.01, strength / 0.16))


def frost(painter: QPainter, rect: QRectF, radius: float, blurred: QPixmap,
          alpha: float = 1.0) -> QPainterPath:
    """兼容旧接口：现在就是画一张白卡。"""
    return paint_card(painter, rect, radius)


def glass_edge(painter: QPainter, path: QPainterPath, rect: QRectF,
               radius: float) -> None:
    """兼容旧接口：描边已经在 paint_card 里画了。"""
    return None
