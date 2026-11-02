"""与暖白画布融合的窗口控制，移动和缩放交给系统处理。"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from .editorial import INK, RED, RULE


class WindowButton(QPushButton):
    def __init__(self, action: str) -> None:
        super().__init__()
        self.action = action
        self.setFixedSize(36, 36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName({"minimize": "最小化", "maximize": "最大化", "close": "关闭"}[action])
        self.setToolTip(self.accessibleName())

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered = self.underMouse() or self.hasFocus()
        closing = hovered and self.action == "close"
        painter.setPen(QPen(QColor(RED if closing else RULE), 1))
        painter.setBrush(QColor(RED if closing else "#F0ECE4") if hovered else Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(.5, .5, 35, 35), 18, 18)
        painter.setPen(QPen(QColor("white" if closing else INK), 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.action == "minimize":
            painter.drawLine(13, 18, 23, 18)
        elif self.action == "close":
            painter.drawLine(13, 13, 23, 23)
            painter.drawLine(23, 13, 13, 23)
        elif self.window().isMaximized():
            painter.drawRect(QRectF(15, 12, 9, 9))
            painter.fillRect(QRectF(12, 15, 9, 9), self.window().palette().window())
            painter.drawRect(QRectF(12, 15, 9, 9))
        else:
            painter.drawRect(QRectF(13, 13, 10, 10))


class TitleBar(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setToolTip("拖动移动窗口 · 双击最大化 / 还原")
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(10)
        self.minimize_button = WindowButton("minimize")
        self.maximize_button = WindowButton("maximize")
        self.close_button = WindowButton("close")
        self.minimize_button.clicked.connect(lambda: self.window().showMinimized())
        self.maximize_button.clicked.connect(self.toggle_maximized)
        self.close_button.clicked.connect(lambda: self.window().close())

    def add_controls(self) -> None:
        self.row.addSpacing(12)
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            self.row.addWidget(button)

    def toggle_maximized(self) -> None:
        window = self.window()
        window.showNormal() if window.isMaximized() else window.showMaximized()

    def sync_state(self) -> None:
        text = "还原" if self.window().isMaximized() else "最大化"
        self.maximize_button.setAccessibleName(text)
        self.maximize_button.setToolTip(text)
        self.maximize_button.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.window().windowHandle():
            if self.window().windowHandle().startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class FramelessWindow(QWidget):
    """保留系统移动、贴边和八方向缩放能力的无边框主窗口。"""
    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setMouseTracking(True)

    def _resize_edges(self, point: QPoint):
        edges = Qt.Edge(0)
        if self.isMaximized() or self.isFullScreen():
            return edges
        if point.x() < 6:
            edges |= Qt.Edge.LeftEdge
        elif point.x() >= self.width() - 6:
            edges |= Qt.Edge.RightEdge
        if point.y() < 6:
            edges |= Qt.Edge.TopEdge
        elif point.y() >= self.height() - 6:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mouseMoveEvent(self, event) -> None:
        edges = self._resize_edges(event.position().toPoint())
        cursors = {
            Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
            Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
            Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
            Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
            Qt.Edge.TopEdge | Qt.Edge.LeftEdge: Qt.CursorShape.SizeFDiagCursor,
            Qt.Edge.BottomEdge | Qt.Edge.RightEdge: Qt.CursorShape.SizeFDiagCursor,
            Qt.Edge.TopEdge | Qt.Edge.RightEdge: Qt.CursorShape.SizeBDiagCursor,
            Qt.Edge.BottomEdge | Qt.Edge.LeftEdge: Qt.CursorShape.SizeBDiagCursor,
        }
        self.setCursor(cursors.get(edges, Qt.CursorShape.ArrowCursor))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self.unsetCursor()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        edges = self._resize_edges(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and edges and self.windowHandle():
            if self.windowHandle().startSystemResize(edges):
                event.accept()
                return
        super().mousePressEvent(event)

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "_titlebar"):
            self._titlebar.sync_state()
        super().changeEvent(event)
