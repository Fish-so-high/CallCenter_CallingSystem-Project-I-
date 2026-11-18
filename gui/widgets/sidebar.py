from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QPushButton, QStyle, QVBoxLayout, QWidget

from gui.theme import TAG_COLORS
from services import DbStatus

TAGS = ("Success", "Miss", "Break", "Rejected", "Abandoned")


class Sidebar(QFrame):
    run_clicked = Signal()
    generator_toggled = Signal(bool)
    export_clicked = Signal()
    employees_clicked = Signal()
    data_clicked = Signal()
    settings_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(236)
        st = self.style()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 16, 14, 14)
        lay.setSpacing(6)

        brand = QLabel("CallCenter"); brand.setObjectName("brand")
        sub = QLabel("Customer Service Calling System"); sub.setObjectName("brandSub")
        sub.setWordWrap(True)
        lay.addWidget(brand); lay.addWidget(sub)

        self.run_button = QPushButton(" Khởi chạy"); self.run_button.setObjectName("runButton")
        self.run_button.setProperty("running", False)
        self.run_button.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.run_button.clicked.connect(self.run_clicked)
        lay.addWidget(self.run_button)
        lay.addSpacing(4)

        self.generator_button = self._nav("Sinh cuộc gọi giả lập", QStyle.StandardPixmap.SP_ComputerIcon)
        self.generator_button.setCheckable(True)
        self.generator_button.setChecked(True)
        self.generator_button.setToolTip("Bật/tắt bộ sinh cuộc gọi. Tắt: engine vẫn chạy nhưng không có cuộc gọi mới.")
        self.generator_button.toggled.connect(self.generator_toggled)
        self.export_button = self._nav("Trích xuất CSV", QStyle.StandardPixmap.SP_DialogSaveButton, self.export_clicked)
        self.employees_button = self._nav("Danh sách nhân viên", QStyle.StandardPixmap.SP_FileDialogDetailedView,
                                          self.employees_clicked)
        self.data_button = self._nav("Truy cập dữ liệu", QStyle.StandardPixmap.SP_DirOpenIcon, self.data_clicked)
        self.settings_button = self._nav("Cài đặt", QStyle.StandardPixmap.SP_FileDialogContentsView,
                                         self.settings_clicked)
        for b in (self.generator_button, self.export_button, self.employees_button, self.data_button,
                  self.settings_button):
            lay.addWidget(b)
        lay.addStretch(1)

        cap = QLabel("THỐNG KÊ HÔM NAY"); cap.setObjectName("groupCaption"); lay.addWidget(cap)
        grid = QGridLayout(); grid.setHorizontalSpacing(8); grid.setVerticalSpacing(2)
        self._stat_labels = {}
        for i, tag in enumerate(TAGS):
            name = QLabel(tag); name.setStyleSheet(f"color: {TAG_COLORS[tag]}; background: transparent;")
            val = QLabel("0"); val.setAlignment(Qt.AlignmentFlag.AlignRight); val.setStyleSheet("background: transparent;")
            grid.addWidget(name, i, 0); grid.addWidget(val, i, 1)
            self._stat_labels[tag] = val
        lay.addLayout(grid)
        lay.addSpacing(8)
        self.state_label = QLabel("Trạng thái: chưa chạy"); self.state_label.setObjectName("muted")
        lay.addWidget(self.state_label)
        self.schedule_label = QLabel(""); self.schedule_label.setObjectName("muted"); self.schedule_label.setWordWrap(True)
        lay.addWidget(self.schedule_label)
        self.db_chip = QLabel("PostgreSQL: đang kết nối..."); self.db_chip.setObjectName("dbChip")
        self.db_chip.setWordWrap(True)
        self.db_chip.setProperty("ok", False)
        lay.addWidget(self.db_chip)

    def _nav(self, text: str, icon: QStyle.StandardPixmap, signal=None) -> QPushButton:
        b = QPushButton(" " + text)
        b.setObjectName("navButton")
        b.setIcon(self.style().standardIcon(icon))
        b.setIconSize(QSize(18, 18))
        if signal is not None:
            b.clicked.connect(signal)
        return b

    # ---------------------------------------- Update GUI
    def set_state(self, running: bool, generator_enabled: bool) -> None:
        st = self.style()
        self.run_button.setText(" Tạm dừng" if running else " Khởi chạy")
        self.run_button.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_MediaPause if running
                                                else QStyle.StandardPixmap.SP_MediaPlay))
        self.run_button.setProperty("running", running)
        self.run_button.style().unpolish(self.run_button); self.run_button.style().polish(self.run_button)
        self.generator_button.blockSignals(True)
        self.generator_button.setChecked(generator_enabled)
        self.generator_button.blockSignals(False)
        self.state_label.setText("Trạng thái: " + ("đang chạy" if running else "tạm dừng"))

    def set_schedule(self, summary: str, details: str = "") -> None:
        self.schedule_label.setText(summary)
        self.schedule_label.setToolTip(details)

    def set_stats(self, stats) -> None:
        for tag, label in self._stat_labels.items():
            label.setText(f"{stats.get(tag, 0):,}")

    def set_db_status(self, status: DbStatus) -> None:
        text = "PostgreSQL: " + ("kết nối" if status.connected else "mất kết nối")
        if status.pending:
            text += f" · chờ ghi {status.pending:,}"
        if status.dropped:
            text += f" · bỏ {status.dropped:,}"
        self.db_chip.setText(text)
        self.db_chip.setToolTip(status.message)
        self.db_chip.setProperty("ok", status.connected)
        self.db_chip.style().unpolish(self.db_chip); self.db_chip.style().polish(self.db_chip)
