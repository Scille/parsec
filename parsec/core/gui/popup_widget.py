from PyQt5.QtCore import Qt, QRect, QTimer, pyqtProperty, QPropertyAnimation, QEvent
from PyQt5.QtGui import QPainter, QColor, QBrush
from PyQt5.QtWidgets import QWidget, QApplication, QLabel, QVBoxLayout


class PopupWidget(QWidget):
    def _set_opacity(self, o):
        self._opacity = o
        self.setWindowOpacity(o)

    def _get_opacity(self):
        return self._opacity

    opacity = pyqtProperty(float, fset=_set_opacity, fget=_get_opacity)

    def __init__(self, parent):
        super().__init__()
        self._parent = parent
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setObjectName("PopupWidget")
        self._opacity = 0.0
        self.animation = QPropertyAnimation(self, b"opacity")
        self.animation.finished.connect(self.hide)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.label.setWordWrap(True)
        self.label.setStyleSheet("color: #FFFFFF; margin: 10px;")
        self.setLayout(QVBoxLayout())
        self.layout().addWidget(self.label)
        self.timer = QTimer()
        self.timer.timeout.connect(self.hide_animation)
        for win in QApplication.topLevelWidgets():
            if win.objectName() == "MainWindow":
                win.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Move or event.type() == QEvent.Resize:
            self.move_popup()
        return False

    def set_message(self, message):
        self.label.setText(message)
        self.adjustSize()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRect()
        rect.setX(self.rect().x() + 5)
        rect.setY(self.rect().y() + 5)
        rect.setWidth(self.rect().width() - 10)
        rect.setHeight(self.rect().height() - 10)
        painter.setBrush(QBrush(QColor(51, 51, 51, 204)))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect, 10, 10)

    def move_popup(self):
        pos = self._parent.mapToGlobal(self._parent.pos())
        self.setGeometry(
            pos.x() + self._parent.size().width() - self.width() - 20,
            pos.y() + self._parent.size().height() - self.height() - 20,
            self.width(),
            self.height(),
        )

    def show(self):
        self.setWindowOpacity(0.0)
        self.animation.setDuration(300)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)

        self.move_popup()

        super().show()
        self.animation.start()
        self.timer.start(3000)

    def hide_animation(self):
        self.timer.stop()
        self.animation.setDuration(500)
        self.animation.setStartValue(1.0)
        self.animation.setEndValue(0.0)
        self.animation.start()

    def hide(self):
        if self.opacity == 0.0:
            super().hide()
