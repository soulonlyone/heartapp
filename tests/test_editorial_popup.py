"""暗色调色板下检查弹窗的实际像素及重复打开后的轮廓。"""
from __future__ import annotations

import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from heartapp.ui.qt.editorial import CapsuleCombo, PAPER, ensure_light_palette


class TestCapsulePopup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        ensure_light_palette(cls.app)

    def setUp(self):
        self.palette = self.app.palette()
        dark = QPalette(self.palette)
        for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button):
            dark.setColor(role, QColor("#101010"))
        for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText):
            dark.setColor(role, QColor("white"))
        self.app.setPalette(dark)
        self.host = QWidget()
        self.host.setStyleSheet(f"QComboBox QAbstractItemView {{ background: {PAPER}; color: black; }}")
        self.combo = CapsuleCombo()
        self.combo.setParent(self.host)
        self.combo.addItems(["最近 5 分钟", "最近 30 分钟", "本次全程"])
        self.host.resize(400, 200)
        self.host.show()

    def tearDown(self):
        self.combo.hidePopup()
        self.host.close()
        self.host.deleteLater()
        self.app.setPalette(self.palette)
        self.app.processEvents()

    def assert_popup_pixels(self):
        self.combo.showPopup()
        self.app.processEvents()
        popup = self.combo.view().window()
        image = popup.grab().toImage()
        for x in (0, image.width() - 1):
            for y in (0, image.height() - 1):
                self.assertEqual(image.pixelColor(x, y).alpha(), 0, "圆角外应透明，不能露出黑底")
        view = self.combo.view()
        point = view.mapTo(popup, QPoint(view.width() // 2, view.height() - 3))
        ratio = image.devicePixelRatio()
        color = image.pixelColor(round(point.x() * ratio), round(point.y() * ratio))
        self.assertEqual(color.name(), PAPER.lower(), "菜单内侧应保持暖白背景")
        self.assertFalse(popup.mask().contains(QPoint(0, 0)))
        self.assertTrue(popup.mask().contains(popup.rect().center()))

    def test_dark_palette_has_transparent_corners_and_paper_interior(self):
        self.assert_popup_pixels()

    def test_reopen_resize_and_keyboard_selection(self):
        self.assert_popup_pixels()
        self.combo.hidePopup()
        self.combo.resize(280, 36)
        self.assert_popup_pixels()
        QTest.keyClick(self.combo.view(), Qt.Key.Key_Down)
        QTest.keyClick(self.combo.view(), Qt.Key.Key_Return)
        self.assertEqual(self.combo.currentIndex(), 1)
        self.assertFalse(self.combo.view().window().isVisible())
