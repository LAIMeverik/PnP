import sys
import os
import json
import pandas as pd
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QFileDialog,
                             QSpinBox, QGroupBox, QTableWidget,
                             QTabWidget, QComboBox, QScrollArea)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QShortcut, QKeySequence

from core.pnp_logic import PnPLogicMixin
from core.warehouse_manager import WarehouseMixin
from ui.visual_map import VisualMapMixin
from ui.progress_panel import ProgressPanelMixin


class SMTNavigator(QMainWindow, VisualMapMixin, ProgressPanelMixin, WarehouseMixin, PnPLogicMixin):
    """50 физических позиций чипшутера: L5–L29 (25), затем R5–R29 (25)."""
    CS_BANK_SLOTS = [f"L{i}" for i in range(5, 30)] + [f"R{i}" for i in range(5, 30)]
    DEFAULT_TAPE_WIDTH = 8

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SMT Navigator: Пошаговая Перезаправка (Динамические очереди)")
        self.resize(1300, 900)

        self.all_data = None
        self.feeder_map = {}
        self.tape_sizes = [8, 12, 16, 24, 32, 44]
        self.spins = {}
        self.progress_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "smt_progress.json",
        )
        self.warehouse_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "warehouse.json",
        )

        progress_data = self.load_progress()
        self.setup_completed = progress_data.get('setup', {})
        self.instr_completed = progress_data.get('instr', {})
        self.global_feeder_map = progress_data.get('global_feeder_map') or {}
        self.batch_stock_deducted = progress_data.get('batch_stock_deducted', {})
        self.warehouse_data = self.load_warehouse_data(progress_data.get('warehouse', []))
        self.batch_comps = {}

        # Горячие клавиши (Enter)
        self.shortcut_enter = QShortcut(QKeySequence("Return"), self)
        self.shortcut_enter.activated.connect(self.toggle_completed)
        self.shortcut_enter_num = QShortcut(QKeySequence("Enter"), self)
        self.shortcut_enter_num.activated.connect(self.toggle_completed)

        self.setStyleSheet("""
            QMainWindow { background-color: #1a1a1a; }
            QLabel { color: #ffffff; font-size: 13px; }
            QGroupBox { color: #007AFF; font-weight: bold; border: 1px solid #333; margin-top: 10px; }
            QPushButton { border-radius: 6px; padding: 10px; font-weight: bold; }
            QTableWidget { background-color: #222; color: #ddd; gridline-color: #333; }
            QHeaderView::section { background-color: #333; color: white; }
            QComboBox { background-color: #007AFF; color: white; font-weight: bold; padding: 5px; }
        """)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # ПАНЕЛЬ УПРАВЛЕНИЯ
        top_group = QGroupBox("ПАНЕЛЬ УПРАВЛЕНИЯ И КОНФИГУРАЦИЯ")
        top_layout = QVBoxLayout(top_group)

        file_run_lay = QHBoxLayout()
        self.btn_load = QPushButton("📂 ЗАГРУЗИТЬ ПРОЕКТ")
        self.btn_load.setFixedHeight(35)
        self.btn_load.clicked.connect(self.load_file)

        self.status_label = QLabel("СТАТУС: Ожидание загрузки файла...")
        self.status_label.setStyleSheet("color: #FFC107; font-weight: bold; font-size: 13px;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_run = QPushButton("⚡ РАССЧИТАТЬ БАЗУ")
        self.btn_run.setFixedHeight(35)
        self.btn_run.setStyleSheet("background-color: #2E7D32; color: white;")
        self.btn_run.clicked.connect(self.calculate_global)

        self.btn_next_batch = QPushButton("➡️ СЛЕДУЮЩИЙ ЗАХОД")
        self.btn_next_batch.setFixedHeight(35)
        self.btn_next_batch.setStyleSheet("background-color: #1976D2; color: white;")
        self.btn_next_batch.clicked.connect(self.next_batch)

        file_run_lay.addWidget(self.btn_load, stretch=1)
        file_run_lay.addWidget(self.status_label, stretch=3)
        file_run_lay.addWidget(self.btn_run, stretch=1)
        file_run_lay.addWidget(self.btn_next_batch, stretch=1)
        top_layout.addLayout(file_run_lay)

        # Конфигурация фидеров (ленты)
        cfg_lay = QHBoxLayout()
        cfg_lay.addWidget(QLabel("<b>Доступные слоты:</b>"))
        for s in self.tape_sizes:
            sp = QSpinBox()
            sp.setRange(0, 500)
            # В павуке нет 8мм, поэтому по умолчанию ставим 0
            sp.setValue(0 if s == 8 else 10)
            sp.setFixedWidth(60)
            sp.setFixedHeight(30)
            lbl = QLabel(f"{s}мм:")
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            cfg_lay.addWidget(lbl)
            cfg_lay.addWidget(sp)
            self.spins[s] = sp
        tape_limits = progress_data.get('tape_limits', {})
        for s in self.tape_sizes:
            key = str(s)
            if key in tape_limits:
                try:
                    self.spins[s].setValue(int(tape_limits[key]))
                except (TypeError, ValueError):
                    pass
        cfg_lay.addStretch()
        top_layout.addLayout(cfg_lay)

        # Выбор платы
        bs_layout = QHBoxLayout()
        bs_layout.addWidget(QLabel("<b>ТEКУЩАЯ СБОРКА:</b>"))
        self.board_sel = QComboBox()
        self.board_sel.setFixedHeight(30)
        self.board_sel.currentIndexChanged.connect(self._on_board_changed)
        bs_layout.addWidget(self.board_sel, stretch=1)
        self.recharge_warning = QLabel("")
        self.recharge_warning.setWordWrap(True)
        bs_layout.addWidget(self.recharge_warning, stretch=2)
        top_layout.addLayout(bs_layout)

        layout.addWidget(top_group)

        # ПАНЕЛЬ ПРОГРЕССА
        self.progress_box = QGroupBox("ПРОГРЕСС УСТАНОВКИ")
        self.progress_box.setMaximumHeight(140)
        self.progress_layout = QVBoxLayout()
        scroll_prog = QScrollArea()
        scroll_prog.setWidgetResizable(True)
        self.prog_container = QWidget()
        self.prog_container.setLayout(self.progress_layout)
        scroll_prog.setWidget(self.prog_container)
        box_lay = QVBoxLayout()
        box_lay.addWidget(scroll_prog)
        self.progress_box.setLayout(box_lay)
        layout.addWidget(self.progress_box)

        # СВОДКА ПРОЕКТА
        self.summary_box = QGroupBox("ОБЩАЯ ИНФОРМАЦИЯ ПО ПЛАТЕ")
        slayout = QHBoxLayout()
        self.lbl_total_parts = QLabel("Всего компонентов: 0")
        self.lbl_unique_parts = QLabel("Уникальных: 0")
        self.lbl_feeders_used = QLabel("Занято фидеров: 0")
        self.lbl_next_board = QLabel("Рекомендуемая следующая: -")
        for lbl in (self.lbl_total_parts, self.lbl_unique_parts, self.lbl_feeders_used, self.lbl_next_board):
            lbl.setStyleSheet(
                "font-size: 14px; color: #E0E0E0; background-color: #333; padding: 5px; border-radius: 5px;")
            slayout.addWidget(lbl)
        self.summary_box.setLayout(slayout)
        layout.addWidget(self.summary_box)

        # ТАБЛИЦЫ И ВКЛАДКИ
        self.tabs = QTabWidget()

        self.tab_visual = QWidget()
        self.tabs.addTab(self.tab_visual, " ВИЗУАЛЬНАЯ КАРТА СТАНКОВ")
        self.setup_visual_tab()

        self.tab_warehouse = QWidget()
        self.tabs.addTab(self.tab_warehouse, "📦 СКЛАД")
        self.setup_warehouse_tab()

        self.table_setup = QTableWidget()
        self.table_setup.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.table_setup.itemSelectionChanged.connect(self._on_setup_selection_changed)
        self.tabs.addTab(self.table_setup, "🛠 КАРТА ЗАПРАВКИ (Что поставить в станок)")

        self.table_instr = QTableWidget()
        self.table_instr.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tabs.addTab(self.table_instr, "📋 СПИСОК УСТАНОВКИ (Куда ставить на плате)")

        layout.addWidget(self.tabs)

    def closeEvent(self, event):
        self.save_progress()
        super().closeEvent(event)

    def load_file(self):
        fn, _ = QFileDialog.getOpenFileName(self, "BOM", "", "Excel (*.xlsx *.xls)")
        if fn:
            self.status_label.setText("СТАТУС: Файл загружен. Готов к расчету.")
            self.status_label.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 14px;")
            xls = pd.ExcelFile(fn)
            self.all_data = pd.concat([pd.read_excel(xls, s).assign(Sheet=s) for s in xls.sheet_names])
            self.all_data.columns = self.all_data.columns.str.strip()
            self.batch_stock_deducted = {}
            self.board_sel.clear()
            self.board_sel.addItems(xls.sheet_names)
            self.update_board_combobox()

    def is_board_completed(self, board_name):
        if self.all_data is None:
            return False
        board_data = self.all_data[self.all_data['Sheet'] == board_name]
        unique_parts = set(board_data['Name'].unique())
        if not unique_parts:
            return False
        done = self.instr_completed.get(board_name, [])
        return all(p in done for p in unique_parts)

    def update_board_combobox(self):
        for i in range(self.board_sel.count()):
            b_name = self.board_sel.itemText(i)
            if self.is_board_completed(b_name):
                self.board_sel.setItemData(i, QColor("#1b5e20"), Qt.ItemDataRole.BackgroundRole)
                self.board_sel.setItemData(i, QColor("white"), Qt.ItemDataRole.ForegroundRole)
            else:
                self.board_sel.setItemData(i, QColor("#007AFF"), Qt.ItemDataRole.BackgroundRole)

    def load_progress(self):
        if os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if 'setup' not in data:
                        return {'setup': data, 'instr': {}, 'batch_stock_deducted': {}}
                    return data
            except Exception:
                pass
        return {'setup': {}, 'instr': {}, 'batch_stock_deducted': {}}

    def save_progress(self):
        tape_limits = {str(s): self.spins[s].value() for s in self.tape_sizes}
        feeder_map = getattr(self, 'global_feeder_map', None)
        if feeder_map is None:
            feeder_map = {}

        payload = {
            'setup': self.setup_completed,
            'instr': self.instr_completed,
            'global_feeder_map': feeder_map,
            'tape_limits': tape_limits,
            'batch_stock_deducted': self.batch_stock_deducted
        }
        try:
            with open(self.progress_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print("Ошибка сохранения:", e)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    w = SMTNavigator()
    w.show()
    sys.exit(app.exec())
