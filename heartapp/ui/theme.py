"""Material 3 Expressive / Android 17「液态玻璃」风格的主题常量。

设计依据
--------
Android 17 的视觉方向是 **液态玻璃**：内容浮在带色彩的动态背景之上，面板是
半透明磨砂的、能透出底下的颜色；控件是大圆角乃至胶囊形。底层体系仍是
**Material 3 Expressive**（动态取色、醒目排版、大形状）。

来源：
* 9to5Google — Google adding blur to Android 17's system UI on Pixel
* Android Central — Android 17 might be getting a glassy, iOS-style look
* m3.material.io — Material 3 Expressive

Tkinter 的两个硬限制
--------------------
1. **没有透明度**：`#RRGGBB` 不能带 alpha。
2. **没有高斯模糊**。

这里的做法是「按扫描线混色」：背景是一道竖直彩色渐变，玻璃面板的颜色 =
该高度处背景色与白色的混合。面板的四个圆角外面也按同样的渐变逐行填色，
所以看不出接缝，视觉上等价于「磨砂玻璃透出背景」。
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

# ---------------------------------------------------------------------------
# 颜色工具
# ---------------------------------------------------------------------------

Color = str
Stop = Tuple[float, Color]


def hex_to_rgb(value: Color) -> Tuple[int, int, int]:
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def rgb_to_hex(rgb: Sequence[float]) -> Color:
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(c)))) for c in rgb[:3])


def mix(a: Color, b: Color, t: float) -> Color:
    """在 a 与 b 之间线性插值。``t=0`` 得到 a，``t=1`` 得到 b。"""
    t = max(0.0, min(1.0, t))
    ra, ga, ba = hex_to_rgb(a)
    rb, gb, bb = hex_to_rgb(b)
    return rgb_to_hex((ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t))


def lighten(color: Color, amount: float) -> Color:
    return mix(color, "#FFFFFF", amount)


def darken(color: Color, amount: float) -> Color:
    return mix(color, "#000000", amount)


def alpha_over(fg: Color, bg: Color, alpha: float) -> Color:
    """把 ``fg`` 以 ``alpha`` 的不透明度叠加在 ``bg`` 上（等价于真 alpha 合成）。"""
    return mix(bg, fg, alpha)


# ---------------------------------------------------------------------------
# Material 3 色彩角色（浅色方案，种子色 = 玫红，贴合「心率」语义）
# ---------------------------------------------------------------------------

PRIMARY = "#B4245F"
ON_PRIMARY = "#FFFFFF"
PRIMARY_CONTAINER = "#FFD8E6"
ON_PRIMARY_CONTAINER = "#3E0021"

SECONDARY = "#73546A"
SECONDARY_CONTAINER = "#F7D8EE"
ON_SECONDARY_CONTAINER = "#2A1223"

TERTIARY = "#7C5635"
TERTIARY_CONTAINER = "#FFDCC2"

ERROR = "#BA1A1A"
ERROR_CONTAINER = "#FFDAD6"

ON_SURFACE = "#1B1B1F"
ON_SURFACE_VARIANT = "#52434B"
OUTLINE = "#857078"
OUTLINE_VARIANT = "#D8C2CA"

#: 玻璃面板里的文字色（面板是浅色磨砂，所以用深色文字）
TEXT = ON_SURFACE
MUTED = ON_SURFACE_VARIANT
FAINT = mix(ON_SURFACE_VARIANT, "#FFFFFF", 0.42)

OK_GREEN = "#1B873F"
WARN_AMBER = "#B26A00"

#: 状态色（用在玻璃面板上，所以取深一点的调子保证对比度）
STATE_IDLE = "#6B6470"
STATE_CONNECTING = WARN_AMBER
STATE_CONNECTED = OK_GREEN
STATE_RECORDING = PRIMARY
STATE_ERROR = ERROR

# ---------------------------------------------------------------------------
# 背景渐变（模拟 Android 17 的动态壁纸）
# ---------------------------------------------------------------------------

#: 竖直多段渐变。整体偏亮、彩度适中：既要「液态玻璃」的通透感，
#: 又要保证磨砂面板上的深色文字有足够对比度。
BACKDROP_STOPS: List[Stop] = [
    (0.00, "#7B5CF0"),
    (0.16, "#9A55DF"),
    (0.34, "#C756BE"),
    (0.52, "#EE5E90"),
    (0.70, "#FB8368"),
    (0.86, "#FFAE72"),
    (1.00, "#FFD9A0"),
]

#: 面板磨砂强度：白色占比越高越不透明（0.83 ≈ 83% 白）。
#: 太低会让背景的彩度压过文字；太高就看不出「透出底色」了。
GLASS_ALPHA = 0.83
#: 顶部高光边、底部分隔线的强度
GLASS_EDGE_LIGHT = 0.55
GLASS_EDGE_DARK = 0.10


class Backdrop:
    """背景渐变。负责画背景，也负责给玻璃面板算「透出来的颜色」。"""

    def __init__(self, stops: Sequence[Stop] = tuple(BACKDROP_STOPS)) -> None:
        self.stops: List[Stop] = sorted(stops, key=lambda s: s[0]) or [
            (0.0, "#FFFFFF"),
            (1.0, "#EEEEEE"),
        ]

    def color_at(self, t: float) -> Color:
        t = max(0.0, min(1.0, float(t)))
        prev = self.stops[0]
        for stop in self.stops:
            if t <= stop[0]:
                if stop[0] <= prev[0]:
                    return stop[1]
                local = (t - prev[0]) / (stop[0] - prev[0])
                return mix(prev[1], stop[1], local)
            prev = stop
        return self.stops[-1][1]

    def glass_at(self, t: float, alpha: float = GLASS_ALPHA) -> Color:
        """该高度处「磨砂玻璃」的颜色：背景色与白色的混合。"""
        return alpha_over("#FFFFFF", self.color_at(t), alpha)

    def edge_light_at(self, t: float) -> Color:
        """玻璃上边缘的高光。"""
        return alpha_over("#FFFFFF", self.glass_at(t), GLASS_EDGE_LIGHT)

    def edge_dark_at(self, t: float) -> Color:
        """玻璃下边缘的分隔线。"""
        return alpha_over("#000000", self.glass_at(t), GLASS_EDGE_DARK)

    def shadow_at(self, t: float, level: int) -> Color:
        """投影的第 ``level`` 层（0 = 最外层最淡）。"""
        strength = (0.030, 0.045, 0.060)[max(0, min(2, level))]
        return alpha_over("#2A1B33", self.color_at(t), strength)

    def draw(self, canvas, width: int, height: int, step: int = 1, tag: str = "backdrop") -> None:
        """把渐变画到 Canvas 上（逐行填色）。"""
        step = max(1, int(step))
        for y in range(0, height, step):
            color = self.color_at(y / max(1, height))
            canvas.create_line(0, y, width, y, fill=color, tags=tag)

    def draw_glow(self, canvas, cx: float, cy: float, radius: float,
                  color: Color, strength: float = 0.30, rings: int = 26) -> None:
        """画一团柔和的色晕，让背景不那么单调（模拟壁纸的光斑）。

        纯 Tkinter 没有径向渐变，这里用一圈圈同心圆近似；每圈的
        颜色都已经和背景混好，所以是「画上去就对了」，不依赖透明度。
        """
        if radius <= 2:
            return
        for i in range(rings, 0, -1):
            frac = i / rings
            r = radius * frac
            base_t = max(0.0, min(1.0, cy / max(1.0, canvas.winfo_height() or 800)))
            base = self.color_at(base_t)
            blended = alpha_over(color, base, strength * (1.0 - frac) ** 1.6)
            canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                fill=blended, outline="", tags="glow",
            )


#: 全局背景实例。界面各处都从这里取色，保证整窗渐变连续。
backdrop = Backdrop()


# ---------------------------------------------------------------------------
# Material 3 字体尺度
# ---------------------------------------------------------------------------

#: (名称, 字号, 字重) —— 已按桌面端视觉缩放，不是手机 dp 原值
TYPE_SCALE = {
    "displaySmall": (30, "bold"),
    "headlineSmall": (19, "bold"),
    "titleLarge": (14, "bold"),
    "titleMedium": (12, "bold"),
    "bodyLarge": (11, "normal"),
    "bodyMedium": (10, "normal"),
    "labelLarge": (10, "bold"),
    "labelMedium": (9, "normal"),
    "labelSmall": (8, "normal"),
}

#: Material 3 的圆角尺度
RADIUS = {
    "card": 26,
    "panel": 20,
    "small": 12,
    "pill": 999,
}
