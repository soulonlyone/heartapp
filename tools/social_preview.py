"""生成 GitHub 社交预览图（1280×640）。

输出 docs/images/social-preview.png，上传到
GitHub → Settings → Social preview。

只画品牌元素和一张已有截图，不读个人数据。
注意：不要设 QT_QPA_PLATFORM=offscreen，离屏模式没有字体，文字会消失。

用法：.venv\\Scripts\\python.exe tools\\social_preview.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QFont,
    QFontDatabase,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

from heartapp.ui.qt.editorial import INK, MUTED, PAPER, RED, RULE  # noqa: E402

OUT = ROOT / "docs" / "images" / "social-preview.png"
SHOT = ROOT / "docs" / "images" / "screenshot-multiplayer.png"
W, H = 1280, 640


def pick_font(candidates: list, fallback: str) -> str:
    available = set(QFontDatabase.families())
    for name in candidates:
        if name in available:
            return name
    return fallback


def draw_curve(painter: QPainter, rect: QRectF) -> None:
    """一条示意的心率曲线，和界面里同一个观感。"""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # 细网格
    painter.setPen(QPen(QColor(RULE), 1))
    for i in range(1, 4):
        y = rect.top() + rect.height() * i / 4
        painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

    # 曲线：低平 + 一次上冲 + 回落
    pts = [
        (0.00, 0.62), (0.06, 0.58), (0.12, 0.64), (0.18, 0.55), (0.24, 0.60),
        (0.30, 0.52), (0.36, 0.57), (0.42, 0.46), (0.48, 0.33), (0.53, 0.18),
        (0.58, 0.30), (0.64, 0.45), (0.70, 0.52), (0.76, 0.44), (0.82, 0.55),
        (0.88, 0.49), (0.94, 0.56), (1.00, 0.51),
    ]
    path = QPainterPath()
    for index, (fx, fy) in enumerate(pts):
        x = rect.left() + rect.width() * fx
        y = rect.top() + rect.height() * fy
        if index == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)

    # 曲线下方淡红填充
    fill = QPainterPath(path)
    fill.lineTo(rect.right(), rect.bottom())
    fill.lineTo(rect.left(), rect.bottom())
    fill.closeSubpath()
    grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
    grad.setColorAt(0.0, QColor(241, 61, 43, 38))
    grad.setColorAt(1.0, QColor(241, 61, 43, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(grad)
    painter.drawPath(fill)

    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(RED), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.drawPath(path)
    painter.restore()


def draw_pulse_mark(painter: QPainter, x: float, y: float, size: float) -> float:
    """红色圆点 + 心电图折线（与界面顶部一致）。"""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(RED))
    painter.drawEllipse(QPointF(x + size * 0.16, y + size * 0.5), size * 0.16, size * 0.16)

    path = QPainterPath()
    base = x + size * 0.42
    span = size * 1.5
    path.moveTo(base, y + size * 0.5)
    path.lineTo(base + span * 0.18, y + size * 0.5)
    path.lineTo(base + span * 0.28, y + size * 0.18)
    path.lineTo(base + span * 0.40, y + size * 0.86)
    path.lineTo(base + span * 0.52, y + size * 0.36)
    path.lineTo(base + span * 0.66, y + size * 0.5)
    path.lineTo(base + span * 0.92, y + size * 0.5)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(RED), size * 0.09, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.drawPath(path)
    painter.restore()
    return base + span


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    del app

    canvas = QImage(W, H, QImage.Format.Format_ARGB32)
    canvas.fill(QColor(PAPER))

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    latin_serif = pick_font(["Georgia", "Cambria", "Times New Roman"], "Georgia")
    han = pick_font(["Microsoft YaHei", "Microsoft YaHei UI", "PingFang SC", "SimHei"], "Microsoft YaHei")

    # 顶部品牌行
    mark_end = draw_pulse_mark(painter, 96, 74, 26)
    brand = QFont(latin_serif)
    brand.setFamily(latin_serif)
    brand.setPixelSize(58)
    brand.setWeight(QFont.Weight.DemiBold)
    painter.setFont(brand)
    painter.setPen(QColor(INK))
    painter.drawText(QPointF(mark_end + 18, 112), "HeartApp")

    # 主标题
    title = QFont(han)
    title.setPixelSize(58)
    title.setWeight(QFont.Weight.DemiBold)
    painter.setFont(title)
    painter.setPen(QColor(INK))
    painter.drawText(QPointF(96, 236), "游戏时的心率，")

    painter.setPen(QColor(RED))
    painter.drawText(QPointF(96, 312), "看得见，也留得住。")

    # 副标题
    sub = QFont(han)
    sub.setPixelSize(25)
    painter.setFont(sub)
    painter.setPen(QColor(MUTED))
    painter.drawText(QPointF(96, 372), "BLE 手表 · 置顶小窗 · 本地记录 · 多人对比")

    latin = QFont(han)
    latin.setPixelSize(22)
    painter.setFont(latin)
    painter.setPen(QColor(MUTED))
    painter.drawText(QPointF(96, 410), "Local-first heart-rate overlay for gaming")

    # 底部标签条
    tags = ["不注册", "不上云", "免费导出", "可自建服务端"]
    chip_font = QFont(han)
    chip_font.setPixelSize(21)
    painter.setFont(chip_font)
    x = 96.0
    for text in tags:
        width = painter.fontMetrics().horizontalAdvance(text) + 40
        rect = QRectF(x, 492, width, 46)
        painter.setPen(QPen(QColor(RULE), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 23, 23)
        painter.setPen(QColor(INK))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        x += width + 12

    # 左下：曲线
    draw_curve(painter, QRectF(96, 566, 470, 46))

    # 右侧：应用截图
    if SHOT.exists():
        shot = QImage(str(SHOT))
        if not shot.isNull():
            target = QRectF(700, 168, 500, 350)
            scaled = shot.scaled(
                int(target.width()), int(target.height()),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            crop = scaled.copy(
                max(0, (scaled.width() - int(target.width())) // 2),
                0,
                int(target.width()), int(target.height()),
            )
            # 边框
            painter.setPen(QPen(QColor(RULE), 2))
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawRoundedRect(target.adjusted(-14, -14, 14, 14), 18, 18)
            clip = QPainterPath()
            clip.addRoundedRect(target, 12, 12)
            painter.save()
            painter.setClipPath(clip)
            painter.drawImage(target.topLeft(), crop)
            painter.restore()
    else:
        painter.setPen(QColor(MUTED))
        painter.drawText(QRectF(664, 150, 770, 420), Qt.AlignmentFlag.AlignCenter, "（缺少截图）")

    painter.end()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(OUT))
    print(f"  OK {OUT.relative_to(ROOT)}  {OUT.stat().st_size / 1024:.0f} KB  {W}x{H}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
