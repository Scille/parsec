# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from PyQt5.QtCore import QPoint, QRect, QSize, Qt
from PyQt5.QtWidgets import QLayout, QSizePolicy, QStyle


class FlowLayout(QLayout):
    def __init__(self, spacing=10, parent=None):
        super().__init__(parent)
        self.spacing = spacing
        self.items = []

    def addItem(self, item):
        self.items.append(item)

    def horizontalSpacing(self):
        if self.spacing >= 0:
            return self.spacing
        return self._smart_spacing(QStyle.PM_LayoutHorizontalSpacing)

    def verticalSpacing(self):
        if self.spacing >= 0:
            return self.spacing
        return self._smart_spacing(QStyle.PM_LayoutVerticalSpacing)

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        if index >= 0 and index < len(self.items):
            return self.items[index]
        return None

    def takeAt(self, index):
        if index >= 0 and index < len(self.items):
            return self.items.pop(index)
        return None

    def clear(self):
        while self.count() != 0:
            item = self.takeAt(0)
            if item:
                w = item.widget()
                self.removeWidget(w)
                w.hide()
                w.setParent(None)

    def expandingDirections(self):
        return Qt.Horizontal

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        height = self._do_layout(QRect(0, 0, width, 0), True)
        return height

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _do_layout(self, rect, test_only):
        x = rect.x()
        y = rect.y()
        line_height = 0

        for item in self.items:
            w = item.widget()
            space_x = self.horizontalSpacing()
            if space_x == -1:
                space_x = w.style().layoutSpacing(
                    QSizePolicy.PushButton, QSizePolicy.PushButton, Qt.Horizontal
                )
            space_y = self.verticalSpacing()
            if space_y == -1:
                space_y = w.style().layoutSpacing(
                    QSizePolicy.QPushButton, QSizePolicy.QPushButton, Qt.Vertical
                )
            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())
        return y + line_height - rect.x()

    def _smart_spacing(self, pm):
        parent = self.parent()
        if not parent:
            return -1
        elif parent.isWidgetType():
            parent.style().pixelMetric(pm, 0, parent)
        else:
            parent.spacing()
