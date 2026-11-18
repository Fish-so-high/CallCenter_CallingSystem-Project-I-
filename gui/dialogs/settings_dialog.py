from __future__ import annotations

import dataclasses
from typing import Optional

from PySide6.QtCore import QTime

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QTabWidget, QTimeEdit, QVBoxLayout, QWidget,
)

from config.runtime import RuntimeConfig
from core.engine import ReleasePolicy

POLICY_LABELS = {ReleasePolicy.REQUEUE.value: "Về cuối hàng đợi rảnh (mặc định)",
                 ReleasePolicy.TO_BREAK.value: "Chuyển sang tạm nghỉ"}


class SettingsDialog(QDialog):
    def __init__(self, config: RuntimeConfig, employee_count: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._base = config
        self.setWindowTitle("Cài đặt")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        tabs = QTabWidget(); lay.addWidget(tabs)

        # ---- Queue ----
        t1 = QWidget(); f1 = QFormLayout(t1)
        f1.addRow("Số nhân viên:", self._muted(f"{employee_count} (quản lý ở mục “Danh sách nhân viên”)"))
        self.max_queue = self._spin(1, 100_000, config.max_queue_size)
        f1.addRow("Sức chứa Calling_queue:", self.max_queue)
        self.miss_policy = self._policy(config.miss_release_policy)
        self.break_policy = self._policy(config.break_release_policy)
        f1.addRow("Nhân viên sau cuộc gọi Miss:", self.miss_policy)
        f1.addRow("Nhân viên sau cuộc gọi Break:", self.break_policy)
        tabs.addTab(t1, "Hàng đợi")

        # ---- Generator ----
        t2 = QWidget(); f2 = QFormLayout(t2)
        self.max_call = self._spin(1, 100_000, config.max_call)
        self.threshold = self._spin(0, 100_000, config.heavy_threshold)
        self.min_iv = self._dspin(config.min_interval); self.max_iv = self._dspin(config.max_interval)
        f2.addRow("max_call (cuộc gọi tối đa mỗi batch):", self.max_call)
        f2.addRow("Ngưỡng queue (vượt thì max_call giảm một nửa):", self.threshold)
        f2.addRow("Khoảng nghỉ giữa batch - tối thiểu (giây):", self.min_iv)
        f2.addRow("Khoảng nghỉ giữa batch - tối đa (giây):", self.max_iv)
        w = config.event_weights
        self.w_success = self._dspin(w.get("Success", 0), 1000); self.w_miss = self._dspin(w.get("Miss", 0), 1000)
        self.w_break = self._dspin(w.get("Break", 0), 1000)
        f2.addRow("Trọng số Success:", self.w_success); f2.addRow("Trọng số Miss:", self.w_miss)
        f2.addRow("Trọng số Break:", self.w_break)
        warn = QLabel("⚠ Phân phối event là MẶC ĐỊNH TẠM (70/10/20), chưa được xác nhận. Tỉ lệ = trọng số / tổng.")
        warn.setWordWrap(True); warn.setStyleSheet("color: #d99a2b;")
        f2.addRow(warn)
        self.use_seed = QCheckBox("Cố định seed để tái tạo kịch bản")
        self.use_seed.setChecked(config.seed is not None)
        self.seed = self._spin(0, 2_147_483_647, config.seed or 0); self.seed.setEnabled(config.seed is not None)
        self.use_seed.toggled.connect(self.seed.setEnabled)
        f2.addRow(self.use_seed, self.seed)
        self.mode = QComboBox(); self.mode.addItem("Sinh ngẫu nhiên", "random"); self.mode.addItem("Phát lại từ file", "replay")
        self.mode.setCurrentIndex(0 if config.source_mode == "random" else 1)
        f2.addRow("Nguồn cuộc gọi:", self.mode)
        self.replay = QLineEdit(config.replay_file)
        f2.addRow("File phát lại (phone|event|time_call):", self._with_browse(self.replay, file=True))
        self.record = QCheckBox("Ghi lại cuộc gọi đã sinh vào data/input_testcase.txt (để phát lại sau)")
        self.record.setChecked(config.record_generated)
        f2.addRow(self.record)
        tabs.addTab(t2, "Bộ sinh cuộc gọi")

        # ---- Export ----
        t3 = QWidget(); f3 = QFormLayout(t3)
        self.export_dir = QLineEdit(config.export_dir)
        f3.addRow("Thư mục xuất CSV:", self._with_browse(self.export_dir, file=False))
        tabs.addTab(t3, "Xuất dữ liệu")

        # ---- Schedule ----
        t4 = QWidget(); f4 = QFormLayout(t4)
        self.auto_enabled = QCheckBox("Bật lịch tự động (xuất CSV và phân kỳ dữ liệu)")
        self.auto_enabled.setChecked(config.auto_schedule_enabled)
        f4.addRow(self.auto_enabled)
        self.export_time = QTimeEdit(QTime.fromString(config.auto_export_time, "HH:mm") if QTime.fromString(config.auto_export_time, "HH:mm").isValid() else QTime(19, 0))
        self.export_time.setDisplayFormat("HH:mm")
        f4.addRow("Giờ xuất CSV mỗi ngày:", self.export_time)
        f4.addRow("Phân kỳ dữ liệu:", self._muted("luôn lúc 00:00 (theo ngày lịch, giờ Asia/Ho_Chi_Minh)"))
        note = QLabel("Lịch chỉ chạy khi ứng dụng đang mở. Nếu ứng dụng bị đóng lúc đến giờ, lần mở sau sẽ chạy bù. "
                      "Muốn xuất cả khi ứng dụng đóng, dùng Windows Task Scheduler (xem README).")
        note.setWordWrap(True); note.setObjectName("muted")
        f4.addRow(note)
        tabs.addTab(t4, "Lịch tự động")

        self.error = QLabel(""); self.error.setWordWrap(True); self.error.setStyleSheet("color: #ff8d92;")
        lay.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Lưu")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Hủy")
        buttons.accepted.connect(self._accept); buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.validator = RuntimeConfig.validate          
        self.apply_callback = None                       

    # --------------------- Service
    @staticmethod
    def _muted(text: str) -> QLabel:
        l = QLabel(text); l.setObjectName("muted"); return l

    @staticmethod
    def _spin(lo: int, hi: int, value: int) -> QSpinBox:
        s = QSpinBox(); s.setRange(lo, hi); s.setValue(int(value)); return s

    @staticmethod
    def _dspin(value: float, hi: float = 3600.0) -> QDoubleSpinBox:
        s = QDoubleSpinBox(); s.setRange(0.0, hi); s.setDecimals(2); s.setSingleStep(0.5); s.setValue(float(value)); return s

    @staticmethod
    def _policy(current: str) -> QComboBox:
        c = QComboBox()
        for value, label in POLICY_LABELS.items():
            c.addItem(label, value)
        c.setCurrentIndex(max(0, c.findData(current)))
        return c

    def _with_browse(self, edit: QLineEdit, file: bool) -> QWidget:
        host = QWidget(); row = QHBoxLayout(host); row.setContentsMargins(0, 0, 0, 0)
        btn = QPushButton("Chọn...")
        def pick():
            path = (QFileDialog.getOpenFileName(self, "Chọn file", edit.text(), "Văn bản (*.txt);;Tất cả (*)")[0]
                    if file else QFileDialog.getExistingDirectory(self, "Chọn thư mục", edit.text()))
            if path:
                edit.setText(path)
        btn.clicked.connect(pick)
        row.addWidget(edit, 1); row.addWidget(btn)
        return host

    # ------------------------------- Result
    def result_config(self) -> RuntimeConfig:
        return dataclasses.replace(
            self._base,
            max_queue_size=self.max_queue.value(),
            miss_release_policy=self.miss_policy.currentData(),
            break_release_policy=self.break_policy.currentData(),
            max_call=self.max_call.value(), heavy_threshold=self.threshold.value(),
            min_interval=self.min_iv.value(), max_interval=self.max_iv.value(),
            event_weights={"Success": self.w_success.value(), "Miss": self.w_miss.value(), "Break": self.w_break.value()},
            seed=self.seed.value() if self.use_seed.isChecked() else None,
            source_mode=self.mode.currentData(), replay_file=self.replay.text().strip(),
            record_generated=self.record.isChecked(), export_dir=self.export_dir.text().strip(),
            auto_schedule_enabled=self.auto_enabled.isChecked(),
            auto_export_time=self.export_time.time().toString("HH:mm"),
        )

    def _accept(self) -> None:
        cfg = self.result_config()
        if self.apply_callback is not None:
            res = self.apply_callback(cfg)
            if not res.ok:
                self.error.setText(res.message)
                return
            self.message = res.message
        else:
            errors = cfg.validate()
            if errors:
                self.error.setText("; ".join(errors))
                return
            self.message = "Đã lưu cài đặt"
        self.accept()
