# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pendulum
from PyQt5.QtCore import QDate, Qt, QTime, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QApplication, QDialog, QWidget
from structlog import get_logger

from parsec.core.gui.custom_dialogs import GreyedDialog, show_error
from parsec.core.gui.lang import get_qlocale
from parsec.core.gui.lang import translate as _
from parsec.core.gui.trio_thread import ThreadSafeQtSignal
from parsec.core.gui.ui.timestamped_workspace_widget import Ui_TimestampedWorkspaceWidget

logger = get_logger()


async def _do_workspace_get_creation_timestamp(workspace_fs):
    # Add 1 second as we want a timestamp safe to ask for a manifest to the backend from the GUI
    return (await workspace_fs.get_earliest_timestamp()).add(seconds=1)


class TimestampedWorkspaceWidget(QWidget, Ui_TimestampedWorkspaceWidget):
    get_creation_timestamp_success = pyqtSignal()
    get_creation_timestamp_error = pyqtSignal()

    def __init__(self, workspace_fs, jobs_ctx):
        super().__init__()
        self.setupUi(self)
        self.dialog = None
        self.workspace_fs = workspace_fs
        self.jobs_ctx = jobs_ctx
        self.calendar_widget.setLocale(get_qlocale())
        for d in (Qt.Saturday, Qt.Sunday):
            fmt = self.calendar_widget.weekdayTextFormat(d)
            fmt.setForeground(QColor(0, 0, 0))
            self.calendar_widget.setWeekdayTextFormat(d, fmt)
        self.get_creation_timestamp_success.connect(self.enable_with_timestamp)
        self.get_creation_timestamp_error.connect(self.on_error)
        self.limits_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "get_creation_timestamp_success"),
            ThreadSafeQtSignal(self, "get_creation_timestamp_error"),
            _do_workspace_get_creation_timestamp,
            workspace_fs=workspace_fs,
        )
        self.button_show.clicked.connect(self._on_show_clicked)

    @property
    def date(self):
        return self.calendar_widget.selectedDate()

    @property
    def time(self):
        return self.time_edit.time()

    def _on_show_clicked(self):
        if self.dialog:
            self.dialog.accept()
        elif QApplication.activeModalWidget():
            QApplication.activeModalWidget().accept()
        else:
            logger.warning("Cannot close dialog when displaying info")

    def cancel(self):
        if self.limits_job:
            self.limits_job.cancel_and_join()

    def set_time_limits(self):
        selected_date = self.calendar_widget.selectedDate()
        if selected_date == QDate(*self.creation_date):
            self.time_edit.setMinimumTime(QTime(*self.creation_time))
        else:
            self.time_edit.clearMinimumTime()
        if selected_date == QDate(*self.now_date):
            self.time_edit.setMaximumTime(QTime(*self.now_time))
        else:
            self.time_edit.clearMaximumTime()

    def on_error(self):
        if self.limits_job and self.limits_job.status != "cancelled":
            show_error(
                self,
                _("TEXT_WORKSPACE_TIMESTAMPED_VERSION_RETRIEVAL_FAILED"),
                exception=self.limits_job.exc,
            )
        self.limits_job = None
        self.reject()

    def enable_with_timestamp(self):
        creation = self.limits_job.ret.in_timezone("local")
        self.limits_job = None
        self.creation_date = (creation.year, creation.month, creation.day)
        self.creation_time = (creation.hour, creation.minute, creation.second)
        now = pendulum.now().in_timezone("local")
        self.now_date = (now.year, now.month, now.day)
        self.now_time = (now.hour, now.minute, now.second)
        self.calendar_widget.setMinimumDate(QDate(*self.creation_date))
        self.calendar_widget.setMaximumDate(QDate(*self.now_date))
        self.calendar_widget.selectionChanged.connect(self.set_time_limits)
        self.set_time_limits()
        self.time_edit.setDisplayFormat("h:mm:ss")

    def on_close(self):
        self.cancel()

    @classmethod
    def exec_modal(cls, workspace_fs, jobs_ctx, parent):
        w = cls(workspace_fs=workspace_fs, jobs_ctx=jobs_ctx)
        d = GreyedDialog(
            center_widget=w, title=_("TEXT_WORKSPACE_TIMESTAMPED_TITLE"), parent=parent
        )
        w.dialog = d
        r = d.exec_()
        if r == QDialog.Rejected:
            return None, None
        return w.date, w.time
