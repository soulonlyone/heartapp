"""界面组件：液态玻璃面板 / Material 3 控件 / 心率表盘 / 图表。

所有控件都用 Canvas 手绘，不引入第三方 UI 库。

「液态玻璃」怎么在 Tkinter 里做出来
----------------------------------
Tkinter 既没有透明度也没有模糊，所以这里走的是**按扫描线混色**：

* 整窗背后是一道竖直彩色渐变（``Backdrop``）。
* 玻璃面板的填充色 = 该高度处背景色与白色的合成色 —— 视觉上等价于
  「白色磨砂玻璃透出底下的颜色」。
* 面板四个圆角外面也逐行画同样的渐变切片，所以和身后的背景严丝合缝。
* 面板上边缘一条浅色高光、下边缘一条极淡的分隔线，模拟玻璃的厚度。
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..metrics import ZONE_DEFINITIONS, format_duration, zone_for_bpm
from . import theme as T
from .theme import (  # noqa: F401  (re-export 给界面层用)
    BACKDROP_STOPS,
    GLASS_ALPHA,
    MUTED,
    ON_PRIMARY,
    ON_PRIMARY_CONTAINER,
    ON_SURFACE,
    ON_SURFACE_VARIANT,
    OUTLINE,
    PRIMARY,
    PRIMARY_CONTAINER,
    RADIUS,
    SECONDARY_CONTAINER,
    STATE_CONNECTED,
    STATE_CONNECTING,
    STATE_ERROR,
    STATE_IDLE,
    STATE_RECORDING,
    TEXT,
    TYPE_SCALE,
    alpha_over,
    backdrop,
    darken,
    lighten,
    mix,
)

# ---------------------------------------------------------------------------
# 兼容旧名字（界面层还在用）
# ---------------------------------------------------------------------------

BG = "#000000"                      # 仅作占位；实际背景是渐变
CARD = "#FFFFFF"
CARD_BORDER = OUTLINE
BORDER = OUTLINE
ACCENT = PRIMARY
ACCENT_DARK = darken(PRIMARY, 0.22)
ACCENT_SOFT = PRIMARY_CONTAINER
FAINT = T.FAINT
OK_GREEN = T.OK_GREEN
WARN_AMBER = T.WARN_AMBER
TRACK = "#E7E1E6"
HAIRLINE = "#EDE7EB"

#: 心率区间色：在浅色磨砂面板上要有足够对比度，所以取比纯色标更深的调子
ZONE_COLORS: Dict[str, str] = {
    "静息": "#8B8794",
    "Z1 恢复": "#2E93CE",
    "Z2 燃脂": "#1E9A55",
    "Z3 有氧": "#C28700",
    "Z4 无氧": "#DE6A1E",
    "Z5 极限": "#C2273F",
}

ZONE_BAND_COLORS: Dict[str, str] = {
    "静息": "#F1EFF3",
    "Z1 恢复": "#E9F3FA",
    "Z2 燃脂": "#E7F6ED",
    "Z3 有氧": "#FAF3DF",
    "Z4 无氧": "#FCEEE2",
    "Z5 极限": "#FBE9EC",
}

ZONE_ORDER: List[str] = ["静息"] + [name for name, _, _ in ZONE_DEFINITIONS]

FONT_CANDIDATES = ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI Variable Text",
                   "Segoe UI", "SimHei")
MONO_CANDIDATES = ("Cascadia Mono", "Consolas", "Courier New")

_family_cache: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# 字体 / 尺寸 / 颜色工具
# ---------------------------------------------------------------------------


def pick_family(widget: tk.Misc, candidates: Sequence[str], fallback: str) -> str:
    key = "|".join(candidates) + "::" + fallback
    if key in _family_cache:
        return _family_cache[key]
    try:
        available = set(tkfont.families(widget))
    except Exception:  # noqa: BLE001
        available = set()
    chosen = ""
    for name in candidates:
        if name in available:
            chosen = name
            break
    if not chosen:
        try:
            chosen = tkfont.nametofont(fallback).actual("family")
        except Exception:  # noqa: BLE001
            chosen = "TkDefaultFont"
    _family_cache[key] = chosen
    return chosen


def ui_font(widget: tk.Misc, size: int = 10, weight: str = "normal") -> Tuple[str, int, str]:
    return (pick_family(widget, FONT_CANDIDATES, "TkDefaultFont"), size, weight)


def mono_font(widget: tk.Misc, size: int = 10) -> Tuple[str, int]:
    return (pick_family(widget, MONO_CANDIDATES, "TkFixedFont"), size)


def type_font(widget: tk.Misc, role: str) -> Tuple[str, int, str]:
    """按 Material 3 字体尺度取字体。"""
    size, weight = TYPE_SCALE.get(role, (10, "normal"))
    return ui_font(widget, size, weight)


def measure(widget: tk.Misc, text: str, font_spec: Tuple) -> int:
    try:
        return tkfont.Font(
            family=font_spec[0], size=font_spec[1],
            weight=font_spec[2] if len(font_spec) > 2 else "normal",
        ).measure(text)
    except Exception:  # noqa: BLE001
        return len(text) * (font_spec[1] if len(font_spec) > 1 else 10)


def color_for_bpm(bpm: Optional[float], max_hr: int) -> str:
    if not bpm or bpm <= 0:
        return T.FAINT
    return ZONE_COLORS.get(zone_for_bpm(bpm, max_hr), ON_SURFACE_VARIANT)


def zone_label_for_bpm(bpm: Optional[float], max_hr: int) -> str:
    if not bpm or bpm <= 0:
        return "无读数"
    return zone_for_bpm(bpm, max_hr)


def round_rect_points(x1: float, y1: float, x2: float, y2: float, r: float) -> List[float]:
    """圆角矩形的多边形顶点（配合 ``smooth=True``）。"""
    r = max(0.0, min(float(r), (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


def _nice_step(span: float, target_lines: int = 5) -> float:
    if span <= 0:
        return 10.0
    raw = span / max(1, target_lines)
    for step in (5, 10, 15, 20, 25, 30, 40, 50, 100):
        if raw <= step:
            return float(step)
    return 100.0


# ---------------------------------------------------------------------------
# 背景与「透出底色」的计算
# ---------------------------------------------------------------------------


def y_ratio(widget: tk.Misc) -> float:
    """控件在整窗中的纵向位置（0=顶，1=底）。"""
    try:
        top = widget.winfo_toplevel()
        y = widget.winfo_rooty() - top.winfo_rooty()
        h = max(1, top.winfo_height())
        return max(0.0, min(1.0, y / float(h)))
    except Exception:  # noqa: BLE001
        return 0.5


def global_y(widget: tk.Misc) -> float:
    try:
        return float(widget.winfo_rooty() - widget.winfo_toplevel().winfo_rooty())
    except Exception:  # noqa: BLE001
        return 0.0


def surface_color(widget: tk.Misc) -> str:
    """控件「身下」应该是什么颜色。

    优先跟随祖先里的玻璃面板；如果一路到顶窗都没有玻璃面板（例如弹窗），
    就用窗口自己的背景色 —— 否则自绘控件会去取背景渐变的颜色，在弹窗上很突兀。
    """
    w = getattr(widget, "master", None)
    depth = 0
    while w is not None and depth < 12:
        # GlassCard 和 TonalCard 都有 fill_color；漏掉 TonalCard 的话，
        # 卡片里的控件会取到外层的玻璃色，在卡片上留下一块色差方块。
        if isinstance(w, (GlassCard, TonalCard)):
            return w.fill_color
        if isinstance(w, (tk.Tk, tk.Toplevel)):
            try:
                return str(w.cget("bg"))
            except Exception:  # noqa: BLE001
                break
        w = getattr(w, "master", None)
        depth += 1
    return backdrop.color_at(y_ratio(widget))


class BackdropCanvas(tk.Canvas):
    """铺满窗口的渐变背景（含两团柔和色晕）。"""

    def __init__(self, master: tk.Misc, **kwargs) -> None:
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bd", 0)
        super().__init__(master, **kwargs)
        self._size: Tuple[int, int] = (0, 0)
        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, event: "tk.Event") -> None:
        size = (int(event.width), int(event.height))
        if size == self._size or size[0] < 2 or size[1] < 2:
            return
        self._size = size
        self.redraw()

    def redraw(self) -> None:
        w, h = self._size
        if w < 2 or h < 2:
            return
        self.delete("all")
        backdrop.draw(self, w, h, step=1, tag="bg")
        # 两团色晕：左上偏冷、右下偏暖，让渐变不那么「直」
        backdrop.draw_glow(self, w * 0.18, h * 0.14, max(w, h) * 0.42,
                           "#7FE7FF", strength=0.30)
        backdrop.draw_glow(self, w * 0.88, h * 0.82, max(w, h) * 0.40,
                           "#FFE08A", strength=0.28)
        self.tag_lower("bg")


# ---------------------------------------------------------------------------
# 液态玻璃面板
# ---------------------------------------------------------------------------


class GlassCard(tk.Canvas):
    """半透明磨砂面板。内容放进 ``self.body``。"""

    def __init__(
        self,
        master: tk.Misc,
        *,
        radius: int = RADIUS["card"],
        padding: int = 16,
        glass: float = GLASS_ALPHA,
        **kwargs,
    ) -> None:
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bd", 0)
        super().__init__(master, bg=backdrop.color_at(0.4), **kwargs)
        self._radius = radius
        self._padding = padding
        self._glass = glass
        self.fill_color: str = backdrop.glass_at(0.4, glass)
        self._size: Tuple[int, int] = (0, 0)
        self.body = tk.Frame(self, bg=self.fill_color)
        self._window = self.create_window(0, 0, window=self.body, anchor="nw")
        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, event: "tk.Event") -> None:
        w, h = int(event.width), int(event.height)
        if w < 6 or h < 6:
            return
        self._paint(w, h)
        if (w, h) == self._size:
            return
        self._size = (w, h)
        p = self._padding
        self.coords(self._window, p, p)
        self.itemconfigure(self._window, width=max(1, w - 2 * p), height=max(1, h - 2 * p))

    def _paint(self, w: int, h: int) -> None:
        # 只删自己画的东西；绝对不能 delete("all")，那会把内嵌的 body 一起删掉
        self.delete("glass")

        top = self.winfo_toplevel()
        win_h = max(1, top.winfo_height())
        y0 = global_y(self)
        t_mid = max(0.0, min(1.0, (y0 + h / 2.0) / win_h))

        # 1) 身后背景的切片：只为了让四个圆角与背景无缝衔接
        for y in range(0, h, 2):
            t = max(0.0, min(1.0, (y0 + y) / win_h))
            self.create_line(0, y, w, y, fill=backdrop.color_at(t), tags="glass")

        # 2) 投影（三层，越外越淡）
        for level in (0, 1, 2):
            grow = (7, 4, 2)[level]
            self.create_polygon(
                round_rect_points(1 + grow * 0.3, 3 + grow, w - 1 - grow * 0.3, h - 1 + grow,
                                  self._radius + grow * 0.5),
                smooth=True, splinesteps=18,
                fill=backdrop.shadow_at(t_mid, level), outline="", tags="glass",
            )

        # 3) 磨砂面板
        new_fill = backdrop.glass_at(t_mid, self._glass)
        self.fill_color = new_fill
        self.create_polygon(
            round_rect_points(1, 1, w - 1, h - 2, self._radius),
            smooth=True, splinesteps=20,
            fill=new_fill, outline=backdrop.edge_dark_at(t_mid), width=1, tags="glass",
        )

        # 4) 上边缘高光：玻璃的「厚度」
        self.create_line(
            1 + self._radius * 0.8, 2.0, w - 1 - self._radius * 0.8, 2.0,
            fill=backdrop.edge_light_at(t_mid), width=2, tags="glass",
        )

        self.tag_lower("glass")
        try:
            self.body.configure(bg=new_fill)
        except tk.TclError:
            pass


class Divider(tk.Frame):
    """玻璃面板里的一条极淡分隔线。"""

    def __init__(self, master: tk.Misc, **kwargs) -> None:
        kwargs.setdefault("height", 1)
        super().__init__(master, bg=alpha_over(OUTLINE, "#FFFFFF", 0.45), **kwargs)


# ---------------------------------------------------------------------------
# Material 3 按钮 / 芯片
# ---------------------------------------------------------------------------

BUTTON_VARIANTS = ("filled", "tonal", "outlined", "text", "danger", "ghost")

#: 兼容旧的 ttk 风格名
VARIANT_ALIASES = {
    "Primary.TButton": "filled",
    "Stop.TButton": "danger",
    "Accent.TButton": "filled",
}


class M3Button(tk.Canvas):
    """胶囊按钮。``variant`` 决定配色：filled / tonal / outlined / text / danger。"""

    def __init__(
        self,
        master: tk.Misc,
        text: str = "",
        command: Optional[Callable[[], None]] = None,
        *,
        variant: str = "filled",
        height: int = 38,
        padx: int = 18,
        font_role: str = "labelLarge",
        min_width: int = 0,
        **kwargs,
    ) -> None:
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bd", 0)
        super().__init__(master, bg=surface_color(master), **kwargs)
        self._text = text
        self._command = command
        self._variant = VARIANT_ALIASES.get(variant, variant)
        self._height = height
        self._padx = padx
        self._role = font_role
        self._min_width = min_width
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._cw = 0
        self._ch = 0
        self._surface = surface_color(self)

        self.bind("<Configure>", self._on_configure)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._redraw()

    # -- 对外接口 ---------------------------------------------------------

    def invoke(self) -> None:
        if self._enabled and self._command is not None:
            self._command()

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self._redraw()

    def set_variant(self, variant: str) -> None:
        variant = VARIANT_ALIASES.get(variant, variant)
        if variant != self._variant:
            self._variant = variant
            self._redraw()

    def set_enabled(self, enabled: bool) -> None:
        if enabled != self._enabled:
            self._enabled = enabled
            self._redraw()

    # 兼容 ``button.configure(text=..., style=...)`` 这种老写法
    def configure(self, cnf=None, **kw):  # type: ignore[override]
        text = kw.pop("text", None)
        variant = kw.pop("variant", None)
        kw.pop("style", None)
        if text is not None and text != self._text:
            self._text = text
        if variant is not None:
            variant = VARIANT_ALIASES.get(variant, variant)
            if variant != self._variant:
                self._variant = variant
        if (text is not None or variant is not None) and cnf is None and not kw:
            self._redraw()
            return None
        if cnf is not None:
            return super().configure(cnf, **kw)
        if kw:
            return super().configure(**kw)
        return super().configure()

    config = configure

    # -- 绘制 -------------------------------------------------------------

    def _palette(self) -> Tuple[str, str, Optional[str]]:
        surface = self._surface
        glass = alpha_over("#FFFFFF", surface, 0.72)
        if self._variant == "filled":
            base, fg, border = PRIMARY, ON_PRIMARY, None
        elif self._variant == "tonal":
            base, fg, border = PRIMARY_CONTAINER, ON_PRIMARY_CONTAINER, None
        elif self._variant == "outlined":
            base, fg, border = glass, PRIMARY, alpha_over(OUTLINE, surface, 0.95)
        elif self._variant == "text":
            base, fg, border = glass, PRIMARY, None
        elif self._variant == "danger":
            base, fg, border = "#3A2E36", "#FFFFFF", None
        else:  # ghost
            base, fg, border = glass, ON_SURFACE_VARIANT, None

        if not self._enabled:
            base = alpha_over(base, surface, 0.38)
            fg = alpha_over(fg, surface, 0.45)
        elif self._pressed:
            base = darken(base, 0.18)
        elif self._hover:
            base = darken(base, 0.07)
        return base, fg, border

    def _redraw(self) -> None:
        self.delete("all")
        font = type_font(self, self._role)
        tw = measure(self, self._text, font)
        w = max(self._min_width, tw + self._padx * 2)
        h = self._height
        if (w, h) != (self._cw, self._ch):
            self._cw, self._ch = w, h
            self.configure(width=w, height=h)

        fill, fg, border = self._palette()
        self.create_polygon(
            round_rect_points(0, 0, w - 1, h - 1, h / 2.0),
            smooth=True, splinesteps=20,
            fill=fill, outline=border if border else fill,
            width=1 if border else 0,
        )
        self.create_text(w / 2.0, h / 2.0, text=self._text, fill=fg, font=font)

    # -- 事件 -------------------------------------------------------------

    def _on_configure(self, _event: "tk.Event") -> None:
        surface = surface_color(self)
        if surface != self._surface:
            self._surface = surface
            self.configure(bg=surface)
            self._redraw()

    def _on_enter(self, _event: "tk.Event") -> None:
        self._hover = True
        if self._enabled:
            self._redraw()

    def _on_leave(self, _event: "tk.Event") -> None:
        self._hover = False
        self._pressed = False
        if self._enabled:
            self._redraw()

    def _on_press(self, _event: "tk.Event") -> None:
        if self._enabled:
            self._pressed = True
            self._redraw()

    def _on_release(self, event: "tk.Event") -> None:
        was = self._pressed
        self._pressed = False
        if self._enabled:
            self._redraw()
            # 只有松手时还在按钮范围内才算点击
            if was and 0 <= event.x <= self._cw and 0 <= event.y <= self._ch:
                self.invoke()


class M3Chip(tk.Canvas):
    """Material 3 筛选芯片（带勾选状态的胶囊）。"""

    def __init__(
        self,
        master: tk.Misc,
        text: str = "",
        *,
        variable: Optional[tk.BooleanVar] = None,
        command: Optional[Callable[[], None]] = None,
        height: int = 34,
        **kwargs,
    ) -> None:
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bd", 0)
        super().__init__(master, bg=surface_color(master), **kwargs)
        self._text = text
        self._var = variable
        self._command = command
        self._height = height
        self._hover = False
        self._cw = 0
        self._ch = 0
        self._surface = surface_color(self)
        self._selected = False
        self.bind("<Configure>", self._on_configure)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Button-1>", self._on_click)
        self._sync_from_var()
        self._redraw()

    def _sync_from_var(self) -> None:
        if self._var is not None:
            self._selected = bool(self._var.get())

    def refresh(self) -> None:
        self._sync_from_var()
        self._redraw()

    def _set_hover(self, value: bool) -> None:
        self._hover = value
        self._redraw()

    def _on_click(self, _event: "tk.Event") -> None:
        self._selected = not self._selected
        if self._var is not None:
            self._var.set(self._selected)
        self._redraw()
        if self._command is not None:
            self._command()

    def _on_configure(self, _event: "tk.Event") -> None:
        surface = surface_color(self)
        if surface != self._surface:
            self._surface = surface
            self.configure(bg=surface)
            self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        surface = self._surface
        glass = alpha_over("#FFFFFF", surface, 0.72)
        if self._selected:
            fill = SECONDARY_CONTAINER
            fg = ON_PRIMARY_CONTAINER
            border = None
            label = f"✓  {self._text}"
        else:
            fill = glass
            fg = ON_SURFACE_VARIANT
            border = alpha_over(OUTLINE, surface, 0.7)
            label = self._text
        if self._hover:
            fill = darken(fill, 0.05)

        font = type_font(self, "labelLarge")
        tw = measure(self, label, font)
        w = tw + 32
        h = self._height
        if (w, h) != (self._cw, self._ch):
            self._cw, self._ch = w, h
            self.configure(width=w, height=h)
        self.create_polygon(
            round_rect_points(0, 0, w - 1, h - 1, h / 2.0),
            smooth=True, splinesteps=20,
            fill=fill, outline=border if border else fill, width=1 if border else 0,
        )
        self.create_text(w / 2.0, h / 2.0, text=label, fill=fg, font=font)


# ---------------------------------------------------------------------------
# Material 3 选项卡
# ---------------------------------------------------------------------------


class _TabStrip(tk.Canvas):
    """选项卡条：选中项背后一块胶囊指示器。"""

    def __init__(self, master: tk.Misc, on_select: Callable[[int], None], **kwargs) -> None:
        kwargs.setdefault("height", 42)
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bd", 0)
        super().__init__(master, bg=surface_color(master), **kwargs)
        self._on_select = on_select
        self._labels: List[str] = []
        self._index = 0
        self._hover = -1
        self._hits: List[Tuple[float, float, int]] = []
        self._cw = 0
        self._surface = surface_color(self)
        self.bind("<Configure>", self._on_configure)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _e: self._set_hover(-1))

    def set_labels(self, labels: Sequence[str]) -> None:
        self._labels = list(labels)
        self._redraw()

    def set_index(self, index: int) -> None:
        self._index = max(0, min(len(self._labels) - 1, index))
        self._redraw()

    def _on_configure(self, _event: "tk.Event") -> None:
        surface = surface_color(self)
        if surface != self._surface:
            self._surface = surface
            self.configure(bg=surface)
        self._redraw()

    def _on_motion(self, event: "tk.Event") -> None:
        for x0, x1, idx in self._hits:
            if x0 <= event.x <= x1:
                self._set_hover(idx)
                return
        self._set_hover(-1)

    def _set_hover(self, index: int) -> None:
        if index != self._hover:
            self._hover = index
            self._redraw()

    def _on_click(self, event: "tk.Event") -> None:
        for x0, x1, idx in self._hits:
            if x0 <= event.x <= x1:
                self._on_select(idx)
                return

    def _redraw(self) -> None:
        self.delete("all")
        h = max(self.winfo_height(), 40)
        font = type_font(self, "titleMedium")
        pad = 14
        x = 2.0
        self._hits = []
        for idx, label in enumerate(self._labels):
            tw = measure(self, label, font)
            w = tw + pad * 2
            active = idx == self._index
            if active:
                self.create_polygon(
                    round_rect_points(x, 5, x + w, h - 5, (h - 10) / 2.0),
                    smooth=True, splinesteps=20,
                    fill=PRIMARY_CONTAINER, outline="",
                )
            elif idx == self._hover:
                self.create_polygon(
                    round_rect_points(x, 5, x + w, h - 5, (h - 10) / 2.0),
                    smooth=True, splinesteps=20,
                    fill=alpha_over("#FFFFFF", self._surface, 0.35), outline="",
                )
            self.create_text(
                x + w / 2.0, h / 2.0, text=label, font=font,
                fill=ON_PRIMARY_CONTAINER if active else ON_SURFACE_VARIANT,
            )
            self._hits.append((x, x + w, idx))
            x += w + 6


class M3Tabs(tk.Frame):
    """Material 3 选项卡容器。``add(page, text)`` 加页，``select(i)`` 切换。"""

    def __init__(self, master: tk.Misc, *, on_change: Optional[Callable[[int], None]] = None,
                 **kwargs) -> None:
        super().__init__(master, bg=surface_color(master), **kwargs)
        self._on_change = on_change
        self._pages: List[tk.Frame] = []
        self._labels: List[str] = []
        self._index = 0

        self.strip = _TabStrip(self, self._select_from_ui)
        self.strip.pack(fill="x", padx=4, pady=(2, 6))

        self._stack = tk.Frame(self, bg=surface_color(self))
        self._stack.pack(fill="both", expand=True)
        self._stack.rowconfigure(0, weight=1)
        self._stack.columnconfigure(0, weight=1)

    def new_page(self) -> tk.Frame:
        """建一个内容页。

        页面必须挂在内容区 ``_stack`` 上，不能挂在选项卡本身 —— 选项卡用 pack
        管理自己的子控件，再往里 grid 会直接报 geometry manager 冲突。
        """
        return SurfaceFrame(self._stack)

    def add(self, page: tk.Frame, text: str) -> None:
        page.grid(row=0, column=0, sticky="nsew")
        self._pages.append(page)
        self._labels.append(text)
        self.strip.set_labels(self._labels)
        # 新页是最后创建的，默认叠在最上层。必须重新应用一次当前选择把它压回去，
        # 否则界面显示的会是最后加的那一页（踩过这个坑）。
        self.select(0 if len(self._pages) == 1 else self._index)

    def index(self) -> int:
        return self._index

    def select(self, index: Optional[int] = None):  # type: ignore[override]
        """``select(i)`` 切换；``select()`` 返回当前下标。"""
        if index is None:
            return self._index
        index = max(0, min(len(self._pages) - 1, int(index)))
        self._index = index
        self.strip.set_index(index)
        for i, page in enumerate(self._pages):
            if i == index:
                page.tkraise()
        if self._on_change is not None:
            self._on_change(index)
        return index

    def _select_from_ui(self, index: int) -> None:
        self.select(index)


# ---------------------------------------------------------------------------
# 状态胶囊
# ---------------------------------------------------------------------------


class Pill(tk.Canvas):
    """状态胶囊。"""

    def __init__(
        self,
        master: tk.Misc,
        *,
        text: str = "",
        fg: str = "#FFFFFF",
        bg: str = T.STATE_IDLE,
        font: Optional[Tuple] = None,
        padx: int = 14,
        pady: int = 7,
        dot: bool = True,
        **kwargs,
    ) -> None:
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bd", 0)
        super().__init__(master, bg=surface_color(master), **kwargs)
        self._fg, self._bg = fg, bg
        self._padx, self._pady = padx, pady
        self._text, self._dot = text, dot
        self._font = font or type_font(self, "labelLarge")
        self._surface = surface_color(self)
        self.bind("<Configure>", self._on_configure)
        self._redraw()

    def set(self, text: Optional[str] = None, *, fg: Optional[str] = None,
            bg: Optional[str] = None) -> None:
        changed = False
        if text is not None and text != self._text:
            self._text, changed = text, True
        if fg is not None and fg != self._fg:
            self._fg, changed = fg, True
        if bg is not None and bg != self._bg:
            self._bg, changed = bg, True
        if changed:
            self._redraw()

    def _on_configure(self, _event: "tk.Event") -> None:
        surface = surface_color(self)
        if surface != self._surface:
            self._surface = surface
            self.configure(bg=surface)
            self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        tw = measure(self, self._text, self._font)
        try:
            th = tkfont.Font(
                family=self._font[0], size=self._font[1],
                weight=self._font[2] if len(self._font) > 2 else "normal",
            ).metrics("linespace")
        except Exception:  # noqa: BLE001
            th = 14
        dot_w = 13 if self._dot else 0
        w = int(tw + dot_w + self._padx * 2)
        h = int(th + self._pady * 2)
        self.configure(width=w, height=h)
        self.create_polygon(
            round_rect_points(0, 0, w - 1, h - 1, h / 2.0),
            smooth=True, splinesteps=20, fill=self._bg, outline="",
        )
        cy = h / 2.0
        if self._dot:
            self.create_oval(self._padx, cy - 3.5, self._padx + 7, cy + 3.5,
                             fill=self._fg, outline="")
        self.create_text(self._padx + dot_w, cy, text=self._text, fill=self._fg,
                         font=self._font, anchor="w")


# ---------------------------------------------------------------------------
# 心率表盘
# ---------------------------------------------------------------------------


class BpmGauge(tk.Canvas):
    """环形仪表：外圈心率刻度环，中间当前心率大字。缺口在正下方。"""

    def __init__(self, master: tk.Misc, **kwargs) -> None:
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bd", 0)
        super().__init__(master, bg=surface_color(master), **kwargs)
        self.bpm: Optional[int] = None
        self.max_hr: int = 190
        self.contact: Optional[bool] = None
        self.subtitle: str = ""
        self.device_text: str = ""
        self._size: Tuple[int, int] = (0, 0)
        self._surface = surface_color(self)
        self.bind("<Configure>", self._on_configure)

    def set_state(self, *, bpm: Optional[int], max_hr: int,
                  contact: Optional[bool] = None, subtitle: str = "",
                  device_text: str = "") -> None:
        self.bpm = bpm
        self.max_hr = max_hr or 190
        self.contact = contact
        self.subtitle = subtitle
        self.device_text = device_text
        self.redraw()

    def _on_configure(self, event: "tk.Event") -> None:
        size = (int(event.width), int(event.height))
        surface = surface_color(self)
        changed = surface != self._surface
        if changed:
            self._surface = surface
            self.configure(bg=surface)
        if size == self._size and not changed:
            return
        self._size = size
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        w = max(self.winfo_width(), 240)
        h = max(self.winfo_height(), 220)
        surface = self._surface

        margin = max(18, min(w, h) * 0.07)
        size = max(130.0, min(w, h) - margin * 2)
        thickness = max(12.0, size * 0.082)
        cx, cy = w / 2.0, h / 2.0
        radius = size / 2.0 - thickness / 2.0
        box = (cx - radius, cy - radius, cx + radius, cy + radius)
        start, sweep = 225.0, -270.0

        # 轨道：玻璃面板上的浅色轨道
        track = alpha_over(ON_SURFACE, surface, 0.10)
        self.create_arc(*box, start=start, extent=sweep, style="arc",
                        outline=track, width=thickness)

        shown = self.bpm if (self.bpm and self.bpm > 0) else None
        color = color_for_bpm(shown, self.max_hr)
        lo, hi = 40.0, max(80.0, self.max_hr * 1.05)
        ratio = 0.0 if shown is None else max(0.0, min(1.0, (shown - lo) / (hi - lo)))

        if ratio > 0.002:
            self.create_arc(*box, start=start, extent=sweep * ratio, style="arc",
                            outline=color, width=thickness)
            for frac in (0.0, ratio):  # 两端圆帽
                ang = math.radians(start + sweep * frac)
                px = cx + radius * math.cos(ang)
                py = cy - radius * math.sin(ang)
                half = thickness / 2.0
                self.create_oval(px - half, py - half, px + half, py + half,
                                 fill=color, outline="")

        # 区间分界刻度
        for _name, low, _high in ZONE_DEFINITIONS:
            frac = (self.max_hr * low - lo) / (hi - lo)
            if not 0.03 < frac < 0.97:
                continue
            ang = math.radians(start + sweep * frac)
            inner = radius + thickness / 2.0 + 2
            outer = inner + 7
            self.create_line(
                cx + inner * math.cos(ang), cy - inner * math.sin(ang),
                cx + outer * math.cos(ang), cy - outer * math.sin(ang),
                fill=alpha_over(ON_SURFACE, surface, 0.22), width=2, capstyle="round",
            )

        number_size = max(24, int(size * 0.27))
        self.create_text(
            cx, cy - size * 0.03, text=str(shown) if shown is not None else "--",
            fill=color if shown is not None else T.FAINT,
            font=ui_font(self, number_size, "bold"),
        )
        self.create_text(cx, cy + size * 0.175, text="bpm", fill=MUTED,
                         font=type_font(self, "bodyMedium"))
        self.create_text(cx, cy + size * 0.30,
                         text=zone_label_for_bpm(shown, self.max_hr),
                         fill=color, font=type_font(self, "titleMedium"))

        # 不显示设备名：程序面向任意 BLE 心率广播设备，表盘上写型号没意义

        if self.contact is not None:
            txt = "已贴合" if self.contact else "未贴合"
            tint = T.STATE_CONNECTED if self.contact else T.STATE_CONNECTING
            font = type_font(self, "labelMedium")
            tw = measure(self, txt, font)
            y = h - 15
            self.create_text(cx + 6, y, text=txt, fill=tint, font=font, anchor="center")
            self.create_oval(cx - tw / 2.0 - 9, y - 3.5, cx - tw / 2.0 - 2, y + 3.5,
                             fill=tint, outline="")


BpmPanel = BpmGauge


# ---------------------------------------------------------------------------
# 趋势图
# ---------------------------------------------------------------------------


class TrendChart(tk.Canvas):
    """心率折线图。

    ``axis="live"``：x 是「多少秒之前」的负数，右端固定为 0。
    ``axis="elapsed"``：x 是从 0 开始的秒数。
    """

    def __init__(self, master: tk.Misc, *, height: int = 240, title: str = "", **kwargs) -> None:
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bd", 0)
        super().__init__(master, height=height, bg=surface_color(master), **kwargs)
        self.points: List[Tuple[float, float]] = []
        self.window: Optional[float] = None
        self.axis = "live"
        self.max_hr: Optional[int] = None
        self.title = title
        self.empty_text = "暂无数据"
        self._y_range: Optional[Tuple[float, float]] = None
        self._size: Tuple[int, int] = (0, 0)
        self._surface = surface_color(self)
        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, event: "tk.Event") -> None:
        size = (int(event.width), int(event.height))
        surface = surface_color(self)
        changed = surface != self._surface
        if changed:
            self._surface = surface
            self.configure(bg=surface)
        if size == self._size and not changed:
            return
        self._size = size
        self.redraw()

    def set_points(self, points: Sequence[Tuple[float, float]], *, window: Optional[float] = None,
                   axis: str = "live", max_hr: Optional[int] = None,
                   y_range: Optional[Tuple[float, float]] = None,
                   title: Optional[str] = None, empty_text: Optional[str] = None) -> None:
        self.points = list(points)
        self.window = window
        self.axis = axis
        self.max_hr = max_hr
        self._y_range = y_range
        if title is not None:
            self.title = title
        if empty_text is not None:
            self.empty_text = empty_text
        self.redraw()

    def clear(self) -> None:
        self.points = []
        self.redraw()

    def _x_range(self) -> Tuple[float, float]:
        if not self.points:
            if self.axis == "live":
                return (-(self.window or 300.0), 0.0)
            return (0.0, 60.0)
        xs = [p[0] for p in self.points]
        if self.axis == "live":
            return (0.0 - (self.window or max(60.0, -min(xs))), 0.0)
        left = min(0.0, min(xs))
        right = max(xs)
        if right - left < 10.0:
            right = left + 10.0
        return (left, right)

    def _y_range_calc(self) -> Tuple[float, float]:
        if self._y_range is not None:
            return self._y_range
        if not self.points:
            return (50.0, 150.0)
        ys = [p[1] for p in self.points]
        lo, hi = min(ys), max(ys)
        pad = max(10.0, (hi - lo) * 0.18)
        lo, hi = lo - pad, hi + pad
        if hi - lo < 45.0:
            mid = (lo + hi) / 2.0
            lo, hi = mid - 22.0, mid + 22.0
        step = _nice_step(hi - lo)
        lo = max(30.0, int(lo / step) * step)
        hi = (int(hi / step) + 1) * step
        return (float(lo), float(hi))

    def redraw(self) -> None:
        self.delete("all")
        surface = self._surface
        w = max(self.winfo_width(), 200)
        h = max(self.winfo_height(), 120)
        pad_l, pad_r, pad_t, pad_b = 46, 18, 32, 26
        plot_w = w - pad_l - pad_r
        plot_h = h - pad_t - pad_b
        if plot_w <= 10 or plot_h <= 10:
            return

        x_lo, x_hi = self._x_range()
        y_lo, y_hi = self._y_range_calc()
        if x_hi <= x_lo:
            x_hi = x_lo + 1.0
        if y_hi <= y_lo:
            y_hi = y_lo + 1.0

        def px(x: float) -> float:
            return pad_l + (x - x_lo) / (x_hi - x_lo) * plot_w

        def py(y: float) -> float:
            return pad_t + (y_hi - y) / (y_hi - y_lo) * plot_h

        grid = alpha_over(ON_SURFACE, surface, 0.09)

        if self.max_hr:
            bands = [("静息", 0.0, 0.5)] + [
                (name, low, high) for name, low, high in ZONE_DEFINITIONS
            ]
            for name, low, high in bands:
                b_lo = max(y_lo, self.max_hr * low)
                b_hi = min(y_hi, self.max_hr * high)
                if b_hi <= b_lo:
                    continue
                self.create_rectangle(
                    pad_l, py(b_hi), pad_l + plot_w, py(b_lo),
                    fill=alpha_over(ZONE_COLORS.get(name, "#888888"), surface, 0.10),
                    outline="",
                )

        step = _nice_step(y_hi - y_lo, 5)
        value = int(y_lo / step) * step
        while value <= y_hi + step * 0.5:
            if value >= y_lo:
                yy = py(value)
                self.create_line(pad_l, yy, pad_l + plot_w, yy, fill=grid, dash=(2, 3))
                self.create_text(pad_l - 8, yy, text=str(int(value)), fill=T.FAINT,
                                 font=mono_font(self, 8), anchor="e")
            value += step

        span = x_hi - x_lo
        tick = span / 6.0
        for candidate in (15, 30, 60, 120, 300, 600, 900):
            if span / candidate <= 8:
                tick = float(candidate)
                break
        t = int(x_lo / tick) * tick
        while t <= x_hi + 1e-9:
            if t >= x_lo - 1e-9:
                xx = px(t)
                self.create_line(xx, pad_t, xx, pad_t + plot_h, fill=grid, dash=(2, 3))
                label = format_duration(abs(t))
                if self.axis == "live" and t < 0:
                    label = "-" + label
                self.create_text(xx, pad_t + plot_h + 12, text=label, fill=T.FAINT,
                                 font=mono_font(self, 8), anchor="center")
            t += tick

        if self.title:
            self.create_text(pad_l, 12, text=self.title, fill=TEXT,
                             font=type_font(self, "titleMedium"), anchor="w")

        if not self.points:
            self.create_text(pad_l + plot_w / 2.0, pad_t + plot_h / 2.0,
                             text=self.empty_text, fill=T.FAINT,
                             font=type_font(self, "bodyMedium"), anchor="center")
            return

        pts: List[float] = []
        for x, y in self.points:
            pts.extend((px(x), py(max(y_lo, min(y_hi, y)))))
        if len(pts) >= 4:
            self.create_polygon(
                [pts[0], pad_t + plot_h] + pts + [pts[-2], pad_t + plot_h],
                fill=alpha_over(PRIMARY, surface, 0.12), outline="",
            )
            self.create_line(*pts, fill=PRIMARY, width=2,
                             joinstyle="round", capstyle="round")
        elif len(pts) == 2:
            cx, cy = pts
            self.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=PRIMARY, outline="")

        if len(self.points) >= 5:
            avg = sum(p[1] for p in self.points) / len(self.points)
            if y_lo <= avg <= y_hi:
                self.create_line(pad_l, py(avg), pad_l + plot_w, py(avg),
                                 fill=alpha_over(ON_SURFACE, surface, 0.28), dash=(5, 4))
                self.create_text(pad_l + plot_w - 4, py(avg) - 9, text=f"均值 {avg:.0f}",
                                 fill=MUTED, font=type_font(self, "labelSmall"), anchor="e")

        lx, ly = self.points[-1]
        cx, cy = px(lx), py(max(y_lo, min(y_hi, ly)))
        self.create_oval(cx - 4.5, cy - 4.5, cx + 4.5, cy + 4.5,
                         fill=surface, outline=PRIMARY, width=2)
        label_y = max(pad_t + 9, cy - 13)
        if cx > pad_l + plot_w - 58:
            self.create_text(cx - 11, label_y, text=f"{ly:.0f}", fill=PRIMARY,
                             font=type_font(self, "labelLarge"), anchor="e")
        else:
            self.create_text(cx + 11, label_y, text=f"{ly:.0f}", fill=PRIMARY,
                             font=type_font(self, "labelLarge"), anchor="w")


# ---------------------------------------------------------------------------
# 心率区间条
# ---------------------------------------------------------------------------


class ZoneBar(tk.Canvas):
    """各心率区间累计时长的堆叠条 + 两列图例。"""

    ROW_H = 21

    def __init__(self, master: tk.Misc, **kwargs) -> None:
        kwargs.setdefault("height", 124)
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bd", 0)
        super().__init__(master, bg=surface_color(master), **kwargs)
        self.zone_seconds: Dict[str, float] = {}
        self.max_hr: int = 190
        self.title = "心率区间时长"
        self._size: Tuple[int, int] = (0, 0)
        self._surface = surface_color(self)
        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, event: "tk.Event") -> None:
        size = (int(event.width), int(event.height))
        surface = surface_color(self)
        changed = surface != self._surface
        if changed:
            self._surface = surface
            self.configure(bg=surface)
        if size == self._size and not changed:
            return
        self._size = size
        self.redraw()

    def set_zones(self, zone_seconds: Dict[str, float], *, max_hr: int = 190,
                  title: Optional[str] = None) -> None:
        self.zone_seconds = dict(zone_seconds or {})
        self.max_hr = max_hr or 190
        if title is not None:
            self.title = title
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        surface = self._surface
        w = max(self.winfo_width(), 220)
        h = max(self.winfo_height(), 110)
        x0, x1 = 2, w - 2
        y = 2
        total = sum(self.zone_seconds.values())

        if self.title:
            self.create_text(x0, y, text=self.title, fill=TEXT,
                             font=type_font(self, "titleMedium"), anchor="nw")
            if total > 0:
                self.create_text(x1, y + 2, text=f"共 {format_duration(total)}",
                                 fill=MUTED, font=type_font(self, "labelMedium"), anchor="ne")
            y += 24

        bar_h = 18
        self.create_polygon(
            round_rect_points(x0, y, x1, y + bar_h, bar_h / 2.0),
            smooth=True, splinesteps=18,
            fill=alpha_over(ON_SURFACE, surface, 0.08), outline="",
        )

        if total <= 0:
            self.create_text((x0 + x1) / 2.0, y + bar_h / 2.0, text="暂无数据",
                             fill=T.FAINT, font=type_font(self, "labelMedium"),
                             anchor="center")
            return

        cursor = x0
        for name in ZONE_ORDER:
            seconds = self.zone_seconds.get(name, 0.0)
            if seconds <= 0:
                continue
            seg = (seconds / total) * (x1 - x0)
            self.create_rectangle(cursor, y, cursor + seg, y + bar_h,
                                  fill=ZONE_COLORS.get(name, MUTED), outline="")
            cursor += seg

        y += bar_h + 12
        # 图例排成 3 列 × 2 行：右侧卡片高度有限，2 列 × 3 行会放不下被裁掉
        cols = 3
        gap = 18.0
        col_w = max(84.0, (x1 - x0 - gap * (cols - 1)) / cols)
        for idx, name in enumerate(ZONE_ORDER):
            col, row = idx % cols, idx // cols
            bx = x0 + col * (col_w + gap)
            row_y = y + row * self.ROW_H + self.ROW_H / 2.0
            right = bx + col_w - 2
            if row_y > h - 5:
                break
            seconds = self.zone_seconds.get(name, 0.0)
            pct = (seconds / total * 100.0) if total else 0.0
            self.create_polygon(
                round_rect_points(bx, row_y - 5, bx + 11, row_y + 5, 3.5),
                smooth=True, splinesteps=12,
                fill=ZONE_COLORS.get(name, MUTED), outline="",
            )
            self.create_text(bx + 17, row_y, text=name,
                             fill=TEXT if seconds > 0 else MUTED,
                             font=type_font(self, "labelMedium"), anchor="w")
            self.create_text(right, row_y, text=f"{pct:.0f}%",
                             fill=MUTED if seconds > 0 else T.FAINT,
                             font=mono_font(self, 9), anchor="e")
            self.create_text(right - 32, row_y, text=format_duration(seconds),
                             fill=TEXT if seconds > 0 else T.FAINT,
                             font=mono_font(self, 9), anchor="e")


class TonalCard(tk.Canvas):
    """玻璃面板内部的次级卡片。

    Material 3 用「surface container」层级表达嵌套：外层是液态玻璃，
    内层是比它更亮一档的纯色圆角表面。颜色从所在玻璃面板派生，
    所以不会有接缝。
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        radius: int = 20,
        padding: int = 14,
        tone: float = 0.48,
        **kwargs,
    ) -> None:
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bd", 0)
        super().__init__(master, bg=surface_color(master), **kwargs)
        self._radius = radius
        self._padding = padding
        self._tone = tone
        self.fill_color: str = alpha_over("#FFFFFF", surface_color(master), tone)
        self._size: Tuple[int, int] = (0, 0)
        self.body = tk.Frame(self, bg=self.fill_color)
        self._window = self.create_window(0, 0, window=self.body, anchor="nw")
        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, event: "tk.Event") -> None:
        w, h = int(event.width), int(event.height)
        if w < 6 or h < 6:
            return
        self._paint(w, h)
        if (w, h) == self._size:
            return
        self._size = (w, h)
        p = self._padding
        self.coords(self._window, p, p)
        self.itemconfigure(self._window, width=max(1, w - 2 * p), height=max(1, h - 2 * p))

    def _paint(self, w: int, h: int) -> None:
        self.delete("card")
        base = surface_color(self)
        fill = alpha_over("#FFFFFF", base, self._tone)
        self.fill_color = fill
        try:
            self.configure(bg=base)
            self.body.configure(bg=fill)
        except tk.TclError:
            pass
        self.create_polygon(
            round_rect_points(1, 1, w - 1, h - 1, self._radius),
            smooth=True, splinesteps=18,
            fill=fill, outline=alpha_over(OUTLINE, fill, 0.42), width=1, tags="card",
        )
        self.tag_lower("card")


# ---------------------------------------------------------------------------
# 跟随底色的基础控件
#
# Tkinter 的 Frame / Label 是不透明的。玻璃面板的磨砂色会随窗口大小变化，
# 所以这些控件要能重新同步自己的背景色，否则会在面板上留下色块。
# ---------------------------------------------------------------------------


class SurfaceFrame(tk.Frame):
    def __init__(self, master: tk.Misc, **kwargs) -> None:
        kwargs.setdefault("bg", surface_color(master))
        super().__init__(master, **kwargs)
        self.bind("<Configure>", lambda _e: self.sync_surface())

    def sync_surface(self) -> None:
        color = surface_color(self)
        try:
            if str(self.cget("bg")).upper() != color.upper():
                self.configure(bg=color)
        except tk.TclError:
            pass


class SurfaceLabel(tk.Label):
    def __init__(self, master: tk.Misc, **kwargs) -> None:
        kwargs.setdefault("bg", surface_color(master))
        super().__init__(master, **kwargs)
        self.bind("<Configure>", lambda _e: self.sync_surface())

    def sync_surface(self) -> None:
        color = surface_color(self)
        try:
            if str(self.cget("bg")).upper() != color.upper():
                self.configure(bg=color)
        except tk.TclError:
            pass


def refresh_surfaces(root: tk.Misc) -> None:
    """递归让所有 Surface* 控件重新取色。窗口尺寸变化后调用。"""
    stack = [root]
    seen = 0
    while stack and seen < 4000:
        widget = stack.pop()
        seen += 1
        sync = getattr(widget, "sync_surface", None)
        if callable(sync):
            try:
                sync()
            except Exception:  # noqa: BLE001
                pass
        try:
            stack.extend(widget.winfo_children())
        except Exception:  # noqa: BLE001
            pass


# 兼容旧名字
RoundedCard = GlassCard
