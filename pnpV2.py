import sys
import pandas as pd
import re
import json
import os
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QFileDialog,
                             QSpinBox, QGroupBox, QMessageBox, QTableWidget,
                             QTableWidgetItem, QHeaderView, QTabWidget, QComboBox, QProgressBar, QScrollArea, QLineEdit)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QShortcut, QKeySequence


class SMTNavigator(QMainWindow):
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

    @staticmethod
    def _cs_slot_order(slot):
        """Порядок слота чипшутера L5…L29, затем R5…R29 (для сортировки и заходов)."""
        m = re.match(r'^\s*([LR])\s*(\d+)\s*$', str(slot).strip(), re.I)
        if not m:
            return 9999
        side, n = m.group(1).upper(), int(m.group(2))
        if side == 'L' and 5 <= n <= 29:
            return n - 5
        if side == 'R' and 5 <= n <= 29:
            return 25 + (n - 5)
        return 8000 + n

    @staticmethod
    def _pv_cluster(full_slot):
        """Кластер павука без суффикса «(12мм)» — то, что физически занимает одну позицию захода."""
        return str(full_slot).split(' ')[0].strip().upper()

    def _normalize_loader_queues_for_board(self, board_names):
        """
        Номера очередей для выбранной платы (только среди её компонентов).

        ЧИПШУТЕР: заход = до 50 слотов по физической линейке L5–L29, R5–R29. Номер захода
        пересчитывается по позиции в отсортированном списке (без «лишнего» 4-го номера из-за
        дыр в старых batch в JSON).

        ПАВУК: как раньше — сжатие уникальных старых номеров заходов к 1…K.
        """
        if not board_names:
            return
        for station in ('ЧИПШУТЕР', 'ПАВУК'):
            rows = []
            for name in board_names:
                info = self.feeder_map.get(name)
                if not info or info['station'] != station:
                    continue
                try:
                    old_b = int(info['batch'])
                except (TypeError, ValueError):
                    old_b = 1
                rows.append((name, old_b, info['slot']))
            if not rows:
                continue

            if station == 'ЧИПШУТЕР':
                bank = len(self.CS_BANK_SLOTS)

                def _chip_linear_key(row):
                    _name, ob, sl = row
                    so = self._cs_slot_order(sl)
                    if not (0 <= so < bank):
                        so = 0
                    try:
                        b = int(ob)
                    except (TypeError, ValueError):
                        b = 1
                    return (b - 1) * bank + so

                rows_sorted = sorted(rows, key=_chip_linear_key)
                for i, (name, _ob, _sl) in enumerate(rows_sorted):
                    nb = i // bank + 1
                    self.feeder_map[name]['batch'] = nb
                    self.global_feeder_map[name]['batch'] = nb
            else:
                rows.sort(key=lambda x: (x[1], self._pv_cluster(x[2]), str(x[2])))
                order = []
                for _, ob, _ in rows:
                    if ob not in order:
                        order.append(ob)
                mapping = {old: i + 1 for i, old in enumerate(order)}
                for name, ob, _ in rows:
                    nb = mapping[ob]
                    self.feeder_map[name]['batch'] = nb
                    self.global_feeder_map[name]['batch'] = nb

    def setup_visual_tab(self):
        v_layout = QVBoxLayout(self.tab_visual)
        ctrl_layout = QHBoxLayout()
        ctrl_layout.addWidget(QLabel("<b>Отображаемый станок:</b>"))
        self.visual_batch_sel = QComboBox()
        self.visual_batch_sel.setFixedHeight(30)
        self.visual_batch_sel.addItems(["ЧИПШУТЕР", "ПАВУК"])
        self.visual_batch_sel.currentIndexChanged.connect(self.render_visual_map)
        ctrl_layout.addWidget(self.visual_batch_sel)
        ctrl_layout.addStretch()
        v_layout.addLayout(ctrl_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.visual_container = QWidget()
        self.visual_layout = QVBoxLayout(self.visual_container)
        scroll.setWidget(self.visual_container)
        v_layout.addWidget(scroll)
        self.visual_slot_widgets = {}
        self._visual_slot_by_pos = {}

    def setup_warehouse_tab(self):
        w_layout = QVBoxLayout(self.tab_warehouse)
        ctrl_layout = QHBoxLayout()
        btn_load_wh = QPushButton("📂 ЗАГРУЗИТЬ ИЗ EXCEL (Добавить/Обновить)")
        btn_load_wh.setFixedHeight(35)
        btn_load_wh.clicked.connect(self.load_warehouse_excel)
        ctrl_layout.addWidget(btn_load_wh)

        self.wh_input_num = QLineEdit()
        self.wh_input_num.setPlaceholderText("Номер позиции")
        self.wh_input_name = QLineEdit()
        self.wh_input_name.setPlaceholderText("Название")
        self.wh_input_qty = QSpinBox()
        self.wh_input_qty.setRange(0, 999999)
        self.wh_input_qty.setPrefix("Шт: ")
        self.wh_input_width = QComboBox()
        self.wh_input_width.addItems([str(x) for x in self.tape_sizes])
        self.wh_input_width.setCurrentText(str(self.DEFAULT_TAPE_WIDTH))

        btn_add_wh = QPushButton("➕ ДОБАВИТЬ ВРУЧНУЮ")
        btn_add_wh.setStyleSheet("background-color: #2E7D32; color: white;")
        btn_add_wh.clicked.connect(self.add_manual_warehouse_item)

        ctrl_layout.addWidget(self.wh_input_num)
        ctrl_layout.addWidget(self.wh_input_name)
        ctrl_layout.addWidget(self.wh_input_width)
        ctrl_layout.addWidget(self.wh_input_qty)
        ctrl_layout.addWidget(btn_add_wh)

        self.wh_status_label = QLabel(f"Позиций на складе: {len(self.warehouse_data)}")
        ctrl_layout.addWidget(self.wh_status_label)
        ctrl_layout.addStretch()
        w_layout.addLayout(ctrl_layout)

        main_split = QHBoxLayout()
        w_layout.addLayout(main_split)

        vis_group = QGroupBox("ВИЗУАЛЬНЫЙ СКЛАД")
        vis_lay = QVBoxLayout(vis_group)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.warehouse_container = QWidget()
        self.warehouse_layout = QVBoxLayout(self.warehouse_container)
        scroll.setWidget(self.warehouse_container)
        vis_lay.addWidget(scroll)
        main_split.addWidget(vis_group, stretch=2)

        list_group = QGroupBox("СПИСОК СКЛАДА")
        list_lay = QVBoxLayout(list_group)
        self.warehouse_table = QTableWidget()
        self.warehouse_table.setColumnCount(5)
        self.warehouse_table.setHorizontalHeaderLabels(["НОМЕР ПОЗИЦИИ", "НАЗВАНИЕ", "ШИРИНА ЛЕНТЫ", "КАТУШКА", "ОСТАТОК"])
        self.warehouse_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.warehouse_table.setStyleSheet("background-color: #222; color: white;")
        list_lay.addWidget(self.warehouse_table)
        main_split.addWidget(list_group, stretch=1)

        self.render_warehouse_map()

    def load_warehouse_data(self, legacy_records=None):
        cols = ["Номер", "Название", "ШиринаЛенты", "Катушка", "Остаток"]
        records = []

        if os.path.exists(self.warehouse_file):
            try:
                with open(self.warehouse_file, "r", encoding="utf-8") as f:
                    records = json.load(f) or []
            except Exception:
                records = []
        elif legacy_records:
            for row in legacy_records:
                records.append({
                    "Номер": row.get("Номер", ""),
                    "Название": row.get("Название", ""),
                    "ШиринаЛенты": self.DEFAULT_TAPE_WIDTH,
                    "Катушка": "1",
                    "Остаток": row.get("Количество", 0),
                })

        df = pd.DataFrame(records)
        for c in cols:
            if c not in df.columns:
                df[c] = "" if c in ("Номер", "Название", "Катушка") else 0
        df = df[cols]
        if not df.empty:
            df["Название"] = df["Название"].astype(str).str.strip()
            df["Номер"] = df["Номер"].astype(str)
            df["Катушка"] = df["Катушка"].astype(str)
            df["ШиринаЛенты"] = pd.to_numeric(df["ШиринаЛенты"], errors="coerce").fillna(self.DEFAULT_TAPE_WIDTH).astype(int)
            df["Остаток"] = pd.to_numeric(df["Остаток"], errors="coerce").fillna(0).astype(int)
            df = df[df["Название"] != ""]
            df = df[df["Остаток"] > 0]
            df = df.reset_index(drop=True)
        else:
            df = pd.DataFrame(columns=cols)

        if not os.path.exists(self.warehouse_file):
            self.warehouse_data = df
            self.save_warehouse_data()
        return df

    def save_warehouse_data(self):
        try:
            records = []
            if getattr(self, "warehouse_data", None) is not None and not self.warehouse_data.empty:
                clean = self.warehouse_data.copy()
                clean["Остаток"] = pd.to_numeric(clean["Остаток"], errors="coerce").fillna(0).astype(int)
                clean = clean[clean["Остаток"] > 0]
                records = clean.to_dict("records")
            with open(self.warehouse_file, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print("Ошибка сохранения склада:", e)

    def _upsert_warehouse(self, num, name, width, qty):
        if qty <= 0:
            return
        same = self.warehouse_data[self.warehouse_data["Название"] == name] if not self.warehouse_data.empty else pd.DataFrame()
        next_idx = len(same) + 1
        coil_id = f"{name}-{next_idx}"
        new_row = pd.DataFrame([{
            "Номер": num,
            "Название": name,
            "ШиринаЛенты": int(width),
            "Катушка": coil_id,
            "Остаток": int(qty),
        }])
        self.warehouse_data = pd.concat([self.warehouse_data, new_row], ignore_index=True)

    def add_manual_warehouse_item(self):
        num = self.wh_input_num.text().strip()
        name = self.wh_input_name.text().strip()
        qty = self.wh_input_qty.value()
        width = int(self.wh_input_width.currentText())

        if not name:
            self.wh_status_label.setText("Ошибка: Название не может быть пустым.")
            return

        self._upsert_warehouse(num, name, width, qty)
        self.save_warehouse_data()
        self.wh_input_num.clear()
        self.wh_input_name.clear()
        self.wh_input_qty.setValue(0)
        self.render_warehouse_map()
        self.wh_status_label.setText(f"Добавлено. Позиций: {len(self.warehouse_data)}")

    def load_warehouse_excel(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Загрузить склад", "", "Excel (*.xlsx *.xls)")
        if fn:
            try:
                df = pd.read_excel(fn)
                if len(df.columns) >= 4:
                    df = df.iloc[:, :4]
                    df.columns = ["Номер", "Название", "ШиринаЛенты", "Остаток"]
                elif len(df.columns) >= 3:
                    df = df.iloc[:, :3]
                    df.columns = ["Номер", "Название", "Остаток"]
                    df["ШиринаЛенты"] = self.DEFAULT_TAPE_WIDTH
                else:
                    self.wh_status_label.setText("Ошибка: в Excel должно быть минимум 3 колонки.")
                    return
                df["Остаток"] = pd.to_numeric(df["Остаток"], errors='coerce').fillna(0).astype(int)
                df["ШиринаЛенты"] = pd.to_numeric(df["ШиринаЛенты"], errors='coerce').fillna(self.DEFAULT_TAPE_WIDTH).astype(int)

                for _, row in df.iterrows():
                    self._upsert_warehouse(str(row['Номер']), str(row['Название']), int(row['ШиринаЛенты']), int(row['Остаток']))

                self.wh_status_label.setText(f"Excel загружен. Позиций: {len(self.warehouse_data)}")
                self.save_warehouse_data()
                self.render_warehouse_map()
            except Exception as e:
                self.wh_status_label.setText(f"Ошибка загрузки: {e}")

    def render_warehouse_map(self):
        def clear_layout(layout):
            if layout is not None:
                while layout.count():
                    item = layout.takeAt(0)
                    widget = item.widget()
                    if widget is not None:
                        widget.deleteLater()
                    else:
                        clear_layout(item.layout())

        clear_layout(self.warehouse_layout)
        self.warehouse_table.setRowCount(0)

        if self.warehouse_data.empty:
            return

        df_sorted = self.warehouse_data.copy()
        df_sorted["Остаток"] = pd.to_numeric(df_sorted["Остаток"], errors="coerce").fillna(0).astype(int)
        def _coil_num(value):
            m = re.search(r"(\d+)$", str(value))
            return int(m.group(1)) if m else 999999

        df_sorted["__coil_num"] = df_sorted["Катушка"].map(_coil_num)
        df_sorted["__coil_txt"] = df_sorted["Катушка"].astype(str)
        df_sorted = df_sorted[df_sorted["Остаток"] > 0]
        df_sorted = df_sorted.sort_values(by=["Название", "Номер", "__coil_num", "__coil_txt"], na_position='last')
        df_sorted = df_sorted.drop(columns=["__coil_num", "__coil_txt"])

        self.warehouse_table.setRowCount(len(df_sorted))
        for i, (_, row) in enumerate(df_sorted.iterrows()):
            self.warehouse_table.setItem(i, 0, QTableWidgetItem(str(row['Номер'])))
            self.warehouse_table.setItem(i, 1, QTableWidgetItem(str(row['Название'])))
            self.warehouse_table.setItem(i, 2, QTableWidgetItem(f"{int(row['ШиринаЛенты'])}мм"))
            self.warehouse_table.setItem(i, 3, QTableWidgetItem(str(row['Катушка'])))
            qty_item = QTableWidgetItem(str(row['Остаток']))
            if row['Остаток'] <= 0:
                qty_item.setForeground(QColor("#FF5252"))
            else:
                qty_item.setForeground(QColor("#4CAF50"))
            self.warehouse_table.setItem(i, 4, qty_item)

        row_layouts = [QHBoxLayout() for _ in range(4)]
        for layout in row_layouts:
            self.warehouse_layout.addLayout(layout)

        grouped = df_sorted.groupby(["Номер", "Название", "ШиринаЛенты"], dropna=False)
        for i, ((num, name, width), grp) in enumerate(grouped):
            w = QWidget()
            w.setStyleSheet("background-color: #333; border: 1px solid #555; margin: 2px; border-radius: 4px;")
            l = QVBoxLayout(w)
            l.setContentsMargins(5, 5, 5, 5)

            lbl_num = QLabel(f"Поз: {num}")
            lbl_num.setStyleSheet("color: #AAA; border: none; font-size: 11px;")

            lbl_name = QLabel(f"<b>{name}</b> ({int(width)}мм)")
            lbl_name.setStyleSheet("color: #90CAF9; border: none;")
            lbl_name.setWordWrap(True)

            coils = [f"{c}: {int(q)}" for c, q in zip(grp["Катушка"], grp["Остаток"])]
            total_qty = int(pd.to_numeric(grp["Остаток"], errors="coerce").fillna(0).sum())
            lbl_coils = QLabel(f"Катушек: {len(grp)} | {'; '.join(coils)}")
            lbl_coils.setWordWrap(True)
            lbl_coils.setStyleSheet("color: #DDD; border: none;")

            lbl_qty = QLabel(f"Итого: {total_qty}")
            lbl_qty.setStyleSheet("color: #FF5252; border: none;" if total_qty <= 0 else "color: #4CAF50; border: none;")

            l.addWidget(lbl_num)
            l.addWidget(lbl_name)
            l.addWidget(lbl_coils)
            l.addWidget(lbl_qty)

            row_layouts[i % len(row_layouts)].addWidget(w)

        for layout in row_layouts:
            layout.addStretch()

    def _consume_from_first_coil(self, comp_name, qty_to_subtract):
        if qty_to_subtract <= 0:
            return 0
        if self.warehouse_data.empty:
            return qty_to_subtract

        comp_rows = self.warehouse_data[self.warehouse_data["Название"] == comp_name]
        if comp_rows.empty:
            return qty_to_subtract

        first_idx = comp_rows.index[0]
        current_qty = int(self.warehouse_data.at[first_idx, "Остаток"])
        consume = min(current_qty, int(qty_to_subtract))
        self.warehouse_data.at[first_idx, "Остаток"] = current_qty - consume
        left = int(qty_to_subtract) - consume
        self.warehouse_data = self.warehouse_data[self.warehouse_data["Остаток"] > 0].reset_index(drop=True)
        return left

    def update_warehouse_qty(self, comp_name, qty_to_subtract=0, qty_to_add=0, show_messages=True):
        if self.warehouse_data.empty and qty_to_add <= 0:
            return

        warning_msg = None
        if qty_to_subtract > 0:
            left = self._consume_from_first_coil(comp_name, int(qty_to_subtract))
            if left > 0:
                warning_msg = f"{comp_name}: катушка закончилась, осталось списать {left} шт. Перезагрузите катушку и повторите заход."

        if qty_to_add > 0:
            comp_rows = self.warehouse_data[self.warehouse_data["Название"] == comp_name]
            if comp_rows.empty:
                self._upsert_warehouse("", comp_name, self.DEFAULT_TAPE_WIDTH, int(qty_to_add))
            else:
                idx = comp_rows.index[0]
                self.warehouse_data.at[idx, "Остаток"] = int(self.warehouse_data.at[idx, "Остаток"]) + int(qty_to_add)

        self.save_warehouse_data()
        self.render_warehouse_map()
        if warning_msg and show_messages:
            QMessageBox.warning(self, "Склад", warning_msg)

    def _on_setup_selection_changed(self):
        """Выбор строк в «Карте заправки» — обновить визуальную карту (предпросмотр в слотах)."""
        if self.table_setup.rowCount() == 0:
            return
        self.render_visual_map()

    def _preview_for_setup_selection(self, global_setup, station_key):
        """
        Слот/кластер → имя компонента из выделения таблицы (ещё не в станке).
        station_key: «ЧИПШУТЕР» или «ПАВУК».
        """
        prev = {}
        rows = sorted({i.row() for i in self.table_setup.selectedItems()})
        for r in rows:
            it0 = self.table_setup.item(r, 0)
            it2 = self.table_setup.item(r, 2)
            it3 = self.table_setup.item(r, 3)
            if not (it0 and it2 and it3):
                continue
            if station_key not in str(it0.text()):
                continue
            nm = it3.text()
            if not nm or nm in global_setup:
                continue
            raw = str(it2.text()).strip()
            if station_key == 'ЧИПШУТЕР':
                m = re.match(r'^\s*([LR]\d+)\s*$', raw, re.I)
                if m:
                    prev[m.group(1).upper()] = nm
            else:
                key = raw.split()[0].strip().upper()
                if key:
                    prev[key] = nm
        return prev

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
        if self.all_data is None: return False
        board_data = self.all_data[self.all_data['Sheet'] == board_name]
        unique_parts = set(board_data['Name'].unique())
        if not unique_parts: return False
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

    def calculate_global(self):
        if self.all_data is None: return

        self.global_parts = self.all_data.groupby('Name').agg({'Quantity': 'sum'}).reset_index()
        self.status_label.setText("СТАТУС: Базовые данные подготовлены. Выберите плату для работы.")
        QMessageBox.information(self, "Готово", "Данные загружены. Выберите плату для расчета.")

        # Если feeder_map пуст, мы должны инициализировать слоты
        if not hasattr(self, 'global_feeder_map'):
            self.global_feeder_map = {}

        # НЕ пересчитываем расстановку сразу для текущей платы при переключении,
        # только если она пустая.
        self.render_board_data(rebuild_visual=True, auto_assign=True)
        board = self.board_sel.currentText()
        if board:
            self._autoload_batch_for_board(board, batch_no=1)
            self.render_board_data(rebuild_visual=True, auto_assign=False)

    def _on_board_changed(self, _index=None):
        """Смена платы: пересчёт таблиц без перерисовки визуальной карты и БЕЗ автоматической перезаписи слотов."""
        self.render_board_data(rebuild_visual=False, auto_assign=False)

    def next_batch(self):
        """
        Автоматически выбирает следующую плату по наибольшему совпадению компонентов,
        удаляет ненужные компоненты со станков и рассчитывает очереди для новой платы.
        """
        if self.all_data is None: return
        board = self.board_sel.currentText()
        if not board: return

        # Ищем следующую лучшую плату
        current_board_data = self.all_data[self.all_data['Sheet'] == board]
        unique_parts_current = set(current_board_data['Name'].unique())

        other_boards_matches = []
        for other_board in self.all_data['Sheet'].unique():
            if other_board == board: continue
            if self.is_board_completed(other_board): continue

            other_data = self.all_data[self.all_data['Sheet'] == other_board]
            unique_other = set(other_data['Name'].unique())
            shared = unique_parts_current.intersection(unique_other)
            other_boards_matches.append({
                'ПЛАТА': other_board,
                'ОБЩИХ КОМП.': len(shared)
            })

        other_boards_matches.sort(key=lambda x: x['ОБЩИХ КОМП.'], reverse=True)

        if not other_boards_matches:
            QMessageBox.information(self, "Оптимизация", "Нет незавершенных плат для перехода.")
            return

        next_board = other_boards_matches[0]['ПЛАТА']

        # Переключаемся на следующую плату
        idx = self.board_sel.findText(next_board)
        if idx >= 0:
            self.board_sel.blockSignals(True)
            self.board_sel.setCurrentIndex(idx)
            self.board_sel.blockSignals(False)

        board = next_board

        # Определяем статусы для всех компонентов в станке
        global_setup = set()
        for comps in self.setup_completed.values():
            global_setup.update(comps)

        next_all = set(self.all_data[self.all_data['Sheet'] == board]['Name'].unique())
        next_done = set(self.instr_completed.get(board, []))
        next_needed_active = next_all - next_done
        items_to_remove = []
        for comp in global_setup:
            if comp not in next_needed_active:
                items_to_remove.append(comp)

        # Снимаем коричневые компоненты
        for comp in items_to_remove:
            for b_name in list(self.setup_completed.keys()):
                if comp in self.setup_completed[b_name]:
                    self.setup_completed[b_name].remove(comp)

        self.table_setup.clearSelection()
        self.save_progress()
        self.render_board_data(rebuild_visual=True, auto_assign=True)
        self._autoload_batch_for_board(board, batch_no=1)
        self.render_board_data(rebuild_visual=True, auto_assign=False)
        QMessageBox.information(self, "Оптимизация", f"Успешный переход на плату: {board}\nУдалено ненужных деталей: {len(items_to_remove)}. Батчи пересчитаны.")

    def _board_qty_map(self, board):
        if self.all_data is None or not board:
            return {}
        current_board_data = self.all_data[self.all_data['Sheet'] == board]
        if current_board_data.empty:
            return {}
        agg = current_board_data.groupby('Name').agg({'Quantity': 'sum'}).reset_index()
        return {str(r['Name']): int(r['Quantity']) for _, r in agg.iterrows()}

    def _autoload_batch_for_board(self, board, batch_no=1):
        if not board:
            return
        board_key = str(board)
        loaded = []
        for x in self.batch_stock_deducted.get(board_key, []):
            try:
                loaded.append(int(x))
            except (TypeError, ValueError):
                print(f"Некорректный номер батча в batch_stock_deducted[{board_key}]: {x}")
                continue
        if batch_no in loaded:
            return

        qty_map = self._board_qty_map(board_key)
        board_components = set(qty_map.keys())
        if not board_components:
            return

        if board_key not in self.setup_completed:
            self.setup_completed[board_key] = []

        global_setup = set()
        for comps in self.setup_completed.values():
            global_setup.update(comps)

        to_load = []
        for name in board_components:
            info = self.feeder_map.get(name)
            if not info:
                continue
            try:
                b = int(info.get('batch', 1))
            except (TypeError, ValueError):
                b = 1
            if b == batch_no:
                to_load.append(name)

        for name in to_load:
            if name not in global_setup and name not in self.setup_completed[board_key]:
                self.setup_completed[board_key].append(name)
                global_setup.add(name)

        shortages = []
        for name in to_load:
            qty = int(qty_map.get(name, 0))
            if qty <= 0:
                continue
            left = self._consume_from_first_coil(name, qty)
            if left > 0:
                shortages.append(f"{name}: не хватило {left} шт.")

        self.batch_stock_deducted.setdefault(board_key, [])
        self.batch_stock_deducted[board_key].append(batch_no)
        self.save_warehouse_data()
        self.save_progress()
        self.render_warehouse_map()
        if shortages:
            QMessageBox.warning(self, "Склад", "Часть компонентов требует перезагрузки катушки:\n" + "\n".join(shortages))

    def render_board_data(self, rebuild_visual=True, auto_assign=False):
        board = self.board_sel.currentText()
        if not board:
            return
        if self.all_data is None:
            return

        required_cols = ('Name', 'Quantity', 'Designator', 'Тип ленты')
        missing = [c for c in required_cols if c not in self.all_data.columns]
        if missing:
            QMessageBox.critical(
                self,
                "Ошибка BOM",
                "В Excel не хватает колонок: "
                + ", ".join(missing)
                + ".\nПроверьте заголовки на всех листах (включая «Main»).",
            )
            return

        current_board_data = self.all_data[self.all_data['Sheet'] == board]
        unique_parts_current = set(current_board_data['Name'].unique())

        df_board = current_board_data.groupby('Name').agg({
            'Quantity': 'sum',
            'Designator': lambda x: ", ".join(
                sorted(set(str(item) for sublist in x for item in str(sublist).split(',') if str(item).strip()))),
        }).reset_index()

        def parse_w(x):
            m = re.search(r'(\d+)', str(x))
            w = int(m.group(1)) if m else self.DEFAULT_TAPE_WIDTH
            return next((s for s in self.tape_sizes if w <= s), 44)

        # Вытягиваем глобальную информацию о ширине ленты для всех компонентов проекта
        all_comps_info = self.all_data.groupby('Name').agg({'Тип ленты': 'first'}).reset_index()
        all_comps_info['mm'] = all_comps_info['Тип ленты'].apply(parse_w)

        if not hasattr(self, 'global_feeder_map'):
            self.global_feeder_map = {}

        global_setup = set()
        for comps in self.setup_completed.values():
            global_setup.update(comps)

        # Для текущей платы берем уже существующую глобальную карту (если auto_assign = False)
        # Если auto_assign = True, то пересчитываем батчи.

        board_feeder_map = {}
        for nm in unique_parts_current:
            if nm in self.global_feeder_map:
                board_feeder_map[nm] = self.global_feeder_map[nm]

        if auto_assign:
            cs_needed = all_comps_info[(all_comps_info['Name'].isin(unique_parts_current)) & (all_comps_info['mm'] == 8)]
            pv_needed = all_comps_info[(all_comps_info['Name'].isin(unique_parts_current)) & (all_comps_info['mm'] > 8)]

            # 1. Загружаем то, что УЖЕ на физическом станке (заблокировано для переназначения)
            locked_cs_slots = set()
            for name in global_setup:
                info = self.global_feeder_map.get(name)
                if info and info['station'] == 'ЧИПШУТЕР':
                    locked_cs_slots.add(info['slot'])
                    # Если деталь есть на текущей плате — она сразу попадает в 1 очередь
                    if name in unique_parts_current:
                        board_feeder_map[name] = {'batch': 1, 'slot': info['slot'], 'station': 'ЧИПШУТЕР'}

            cs_unplaced_df = cs_needed[~cs_needed['Name'].isin(board_feeder_map.keys())]
            cs_unplaced_df = cs_unplaced_df.merge(board_counts, on='Name', how='left')
            cs_unplaced_df['board_count'] = pd.to_numeric(cs_unplaced_df['board_count'], errors='coerce').fillna(0).astype(int)
            cs_unplaced_df = cs_unplaced_df.sort_values(by=['board_count', 'Name'], ascending=[False, True])
            cs_unplaced = cs_unplaced_df['Name'].tolist()
            used_cs_slots_in_batch = {}

            def assign_next_cs_slot(comp_name):
                b = 1
                while True:
                    if b not in used_cs_slots_in_batch: used_cs_slots_in_batch[b] = set()

                    # Пытаемся взять исторический слот, если он свободен
                    hist_info = self.global_feeder_map.get(comp_name)
                    if hist_info and hist_info['station'] == 'ЧИПШУТЕР':
                        s = hist_info['slot']
                        if s not in locked_cs_slots and s not in used_cs_slots_in_batch[b]:
                            used_cs_slots_in_batch[b].add(s)
                            return b, s

                    for slot in self.CS_BANK_SLOTS:
                        if slot not in locked_cs_slots and slot not in used_cs_slots_in_batch[b]:
                            used_cs_slots_in_batch[b].add(slot)
                            return b, slot
                    b += 1
                    if b > 50: return None

            # Count the number of boards each component appears on to prioritize common components
            board_counts = self.all_data.groupby('Name')['Sheet'].nunique().reset_index()
            board_counts.rename(columns={'Sheet': 'board_count'}, inplace=True)

            for name in cs_unplaced:
                slot_res = assign_next_cs_slot(name)
                if slot_res is None:
                    QMessageBox.critical(self, "ЧИПШУТЕР", f"Ошибка: не удалось разместить '{name}' (нет свободных слотов).")
                    return
                b, s = slot_res
                board_feeder_map[name] = {'batch': b, 'slot': s, 'station': 'ЧИПШУТЕР'}
                self.global_feeder_map[name] = board_feeder_map[name]

            # Для Павука аналогично - блокируем занятые физически места
            locked_pv_slots = set()
            for name in global_setup:
                info = self.global_feeder_map.get(name)
                if info and info['station'] == 'ПАВУК':
                    base_slot_label = info['slot'].split(' ')[0]
                    m = re.match(r'([LR])(\d+)-([LR])(\d+)', base_slot_label)
                    if m:
                        prefix = m.group(1)
                        start_n, end_n = int(m.group(2)), int(m.group(4))
                        for i in range(start_n, end_n + 1):
                            locked_pv_slots.add(f"{prefix}{i}")
                    if name in unique_parts_current:
                        board_feeder_map[name] = {'batch': 1, 'slot': info['slot'], 'station': 'ПАВУК'}

            pv_unplaced = pv_needed[~pv_needed['Name'].isin(board_feeder_map.keys())]
            pv_unplaced = pv_unplaced.merge(board_counts, on='Name', how='left')
            pv_unplaced['board_count'] = pd.to_numeric(pv_unplaced['board_count'], errors='coerce').fillna(0).astype(int)
            pv_unplaced = pv_unplaced.sort_values(by=['board_count', 'Name'], ascending=[False, True])

            pv_limits = {s: self.spins[s].value() for s in self.tape_sizes}
            pv_batch_usage = {}
            pv_batch_slots_used = {}

            def assign_pv(w, name):
                if pv_limits.get(w, 0) <= 0: return False
                slots_needed = 3 if w in (32, 44) else 2
                b = 1
                while True:
                    if b not in pv_batch_usage: pv_batch_usage[b] = {s: 0 for s in self.tape_sizes}
                    if b not in pv_batch_slots_used: pv_batch_slots_used[b] = set()

                    if pv_batch_usage[b].get(w, 0) < pv_limits.get(w, 0):
                        hist_info = self.global_feeder_map.get(name)
                        if hist_info and hist_info['station'] == 'ПАВУК':
                            base_slot_label = hist_info['slot'].split(' ')[0]
                            m = re.match(r'([LR])(\d+)-([LR])(\d+)', base_slot_label)
                            if m:
                                prefix = m.group(1)
                                start_n, end_n = int(m.group(2)), int(m.group(4))
                                clusters = [f"{prefix}{i}" for i in range(start_n, end_n + 1)]
                                if len(clusters) == slots_needed and all(c not in locked_pv_slots and c not in pv_batch_slots_used[b] for c in clusters):
                                    assigned_cluster = f"{clusters[0]}-{clusters[-1]}"
                                    for c in clusters: pv_batch_slots_used[b].add(c)
                                    pv_batch_usage[b][w] = pv_batch_usage[b].get(w, 0) + 1
                                    board_feeder_map[name] = {'batch': b, 'slot': f'{assigned_cluster} ({w}мм)', 'station': 'ПАВУК'}
                                    self.global_feeder_map[name] = board_feeder_map[name]
                                    return True

                        for prefix in ['L', 'R']:
                            for start_idx in range(1, 21 - slots_needed + 1):
                                cluster = [f"{prefix}{i}" for i in range(start_idx, start_idx + slots_needed)]
                                if all(c not in locked_pv_slots and c not in pv_batch_slots_used[b] for c in cluster):
                                    assigned_cluster = f"{cluster[0]}-{cluster[-1]}"
                                    for c in cluster: pv_batch_slots_used[b].add(c)
                                    pv_batch_usage[b][w] = pv_batch_usage[b].get(w, 0) + 1
                                    board_feeder_map[name] = {'batch': b, 'slot': f'{assigned_cluster} ({w}мм)', 'station': 'ПАВУК'}
                                    self.global_feeder_map[name] = board_feeder_map[name]
                                    return True
                    b += 1
                    if b > 50: return False

            pv_skipped_mm = set()
            for _, r in pv_unplaced.iterrows():
                if not assign_pv(r['mm'], r['Name']):
                    pv_skipped_mm.add(r['mm'])

            if pv_skipped_mm:
                QMessageBox.warning(self, "Павук: нет слотов", f"Для компонентов {pv_skipped_mm}мм нет мест/лимитов.")

        self.feeder_map = board_feeder_map

        # === АНАЛИЗ СОВМЕСТИМОСТИ ПЛАТ (РЕКОМЕНДАЦИЯ) ===
        other_boards_matches = []
        for other_board in self.all_data['Sheet'].unique():
            if other_board == board: continue
            if self.is_board_completed(other_board): continue

            other_data = self.all_data[self.all_data['Sheet'] == other_board]
            unique_other = set(other_data['Name'].unique())
            shared = unique_parts_current.intersection(unique_other)
            other_boards_matches.append({
                'ПЛАТА': other_board,
                'ОБЩИХ КОМП.': len(shared)
            })

        other_boards_matches.sort(key=lambda x: x['ОБЩИХ КОМП.'], reverse=True)
        if other_boards_matches and other_boards_matches[0]['ОБЩИХ КОМП.'] > 0:
            best_match = other_boards_matches[0]
            self.lbl_next_board.setText(f" РЕКОМЕНДУЕМ СЛЕДУЮЩЕЙ: {best_match['ПЛАТА']} (Общих: {best_match['ОБЩИХ КОМП.']}) ")
            self.lbl_next_board.setStyleSheet("font-size: 14px; color: #FFFFFF; background-color: #D84315; padding: 5px; border-radius: 5px; font-weight: bold;")
        else:
            self.lbl_next_board.setText("Рекомендаций для след. платы: нет")
            self.lbl_next_board.setStyleSheet("font-size: 14px; color: #E0E0E0; background-color: #333; padding: 5px; border-radius: 5px;")

        # === ФОРМИРОВАНИЕ ТАБЛИЦ ДЛЯ ВЫБРАННОЙ ПЛАТЫ ===
        total_qty = df_board['Quantity'].sum()
        unique_parts = len(df_board)
        self.lbl_total_parts.setText(f"Всего компонентов: {total_qty}")
        self.lbl_unique_parts.setText(f"Уникальных: {unique_parts}")

        setup_rows = []
        instr_rows = []
        self.batch_comps = {}

        # Формируем таблицы только для тех компонентов, которые есть на текущей плате
        for _, r in df_board.iterrows():
            info = self.feeder_map.get(r['Name'], {'batch': '?', 'slot': '?', 'station': '?'})
            b_name = f"{info['station']} №{info['batch']}"

            if b_name not in self.batch_comps:
                self.batch_comps[b_name] = []
            self.batch_comps[b_name].append(r['Name'])

            setup_rows.append({
                'СТАНОК': info['station'],
                'ОЧЕРЕДЬ (ЗАГРУЗКА)': f"№ {info['batch']}",
                'ПОЗИЦИЯ / ФИДЕР': info['slot'],
                'ЧТО СТАВИМ': r['Name'],
                'НУЖНО ШТ': r['Quantity']
            })

            instr_rows.append({
                'ПРИОРИТЕТ': f"{info['station']} (Батч {info['batch']})",
                'ДЕТАЛЬ': r['Name'],
                'СЛОТ': info['slot'],
                'КОЛ-ВО': r['Quantity'],
                'ПОЗИЦИИ (DESIGNATORS)': r['Designator']
            })

        n_cs = 0
        max_cs_b = 0
        for nm in unique_parts_current:
            inf = self.feeder_map.get(nm)
            if inf and inf.get('station') == 'ЧИПШУТЕР':
                n_cs += 1
                try:
                    max_cs_b = max(max_cs_b, int(inf['batch']))
                except (TypeError, ValueError):
                    max_cs_b = max(max_cs_b, 1)

        self.lbl_feeders_used.setText(
            f"Слотов по плате: {unique_parts} | 8 мм: {n_cs} лент, заходов чипшутера: {max_cs_b} "
            f"(по {len(self.CS_BANK_SLOTS)}: L5–L29, затем R5–R29)"
        )
        cs_batches = [b for b in self.batch_comps.keys() if "ЧИПШУТЕР" in b]
        pv_batches = [b for b in self.batch_comps.keys() if "ПАВУК" in b]

        warn_text = []
        if len(cs_batches) > 1: warn_text.append(f"Чипшутер - {len(cs_batches)} захода")
        if len(pv_batches) > 1: warn_text.append(f"Павук - {len(pv_batches)} захода")

        if warn_text:
            self.recharge_warning.setText(f"⚠️ ТРЕБУЕТСЯ ПЕРЕЗАПРАВКА! ({', '.join(warn_text)})")
            self.recharge_warning.setStyleSheet("color: #FF5252; font-weight: bold; font-size: 14px;")
        else:
            self.recharge_warning.setText("✅ Влазит в один заход (без перезаправок)")
            self.recharge_warning.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 14px;")

        df_setup = pd.DataFrame(setup_rows)
        if not df_setup.empty:
            qn = pd.to_numeric(
                df_setup['ОЧЕРЕДЬ (ЗАГРУЗКА)'].str.replace(r'[^\d]', '', regex=True),
                errors='coerce',
            ).fillna(0).astype(int)
            ord_chip = df_setup.apply(
                lambda r: self._cs_slot_order(r['ПОЗИЦИЯ / ФИДЕР'])
                if str(r['СТАНОК']) == 'ЧИПШУТЕР'
                else -1,
                axis=1,
            )
            ord_pv = df_setup.apply(
                lambda r: self._pv_cluster(r['ПОЗИЦИЯ / ФИДЕР'])
                if str(r['СТАНОК']) == 'ПАВУК'
                else '',
                axis=1,
            )
            df_setup = df_setup.assign(
                __st=df_setup['СТАНОК'].astype(str),
                __qn=qn,
                __oc=pd.to_numeric(ord_chip, errors='coerce').fillna(9999).astype(int),
                __op=ord_pv.astype(str),
                __sl=df_setup['ПОЗИЦИЯ / ФИДЕР'].map(lambda x: str(x) if x is not None else ''),
            )
            df_setup = df_setup.sort_values(
                by=['__st', '__qn', '__oc', '__op', '__sl']
            ).drop(columns=['__st', '__qn', '__oc', '__op', '__sl'])
        else:
            df_setup = pd.DataFrame(
                columns=['СТАНОК', 'ОЧЕРЕДЬ (ЗАГРУЗКА)', 'ПОЗИЦИЯ / ФИДЕР', 'ЧТО СТАВИМ', 'НУЖНО ШТ']
            )

        df_instr = pd.DataFrame(instr_rows)
        if not df_instr.empty:
            bt = pd.to_numeric(
                df_instr['ПРИОРИТЕТ'].str.extract(r'Батч\s*(\d+)', expand=False),
                errors='coerce',
            ).fillna(0).astype(int)
            st_order = df_instr['ПРИОРИТЕТ'].str.contains('ПАВУК', na=False).astype(int)
            oc = df_instr.apply(
                lambda r: self._cs_slot_order(r['СЛОТ']) if 'ЧИПШУТЕР' in str(r['ПРИОРИТЕТ']) else -1,
                axis=1,
            )
            op = df_instr.apply(
                lambda r: self._pv_cluster(r['СЛОТ']) if 'ПАВУК' in str(r['ПРИОРИТЕТ']) else '',
                axis=1,
            )
            df_instr = df_instr.assign(
                __bt=bt,
                __st=st_order,
                __oc=pd.to_numeric(oc, errors='coerce').fillna(9999).astype(int),
                __op=op.astype(str),
                __sl=df_instr['СЛОТ'].map(lambda x: str(x) if x is not None else ''),
            )
            df_instr = df_instr.sort_values(
                by=['__st', '__bt', '__oc', '__op', '__sl']
            ).drop(columns=['__bt', '__st', '__oc', '__op', '__sl'])
        else:
            df_instr = pd.DataFrame(
                columns=['ПРИОРИТЕТ', 'ДЕТАЛЬ', 'СЛОТ', 'КОЛ-ВО', 'ПОЗИЦИИ (DESIGNATORS)']
            )

        self.table_setup.blockSignals(True)
        self.fill_t(self.table_setup, df_setup)
        self.table_setup.blockSignals(False)
        self.fill_t(self.table_instr, df_instr)
        self.render_progress_panel(board)
        if rebuild_visual or not getattr(self, 'visual_slot_widgets', None):
            self.render_visual_map()
        else:
            self._on_setup_selection_changed()
        self.refresh_ui_status()

    def render_visual_map(self):
        def clear_layout(layout):
            if layout is not None:
                while layout.count():
                    item = layout.takeAt(0)
                    widget = item.widget()
                    if widget is not None:
                        widget.deleteLater()
                    else:
                        clear_layout(item.layout())

        clear_layout(self.visual_layout)
        self.visual_slot_widgets.clear()
        self._visual_slot_by_pos = {}

        station_filter = self.visual_batch_sel.currentText()
        if not station_filter:
            return

        is_chipshooter = "ЧИПШУТЕР" in station_filter

        main_split = QHBoxLayout()
        self.visual_layout.addLayout(main_split)

        machine_group = QGroupBox(
            f"СТАНОК: {station_filter} — зелёный: в работе; оранжевый: бережём (нужен потом); коричневый: можно снимать; голубой: выделено"
        )
        m_lay = QHBoxLayout(machine_group)

        self.visual_warning_lbl = QLabel("")
        self.visual_warning_lbl.setStyleSheet("color: #FF5252; font-weight: bold; font-size: 14px;")
        self.visual_warning_lbl.setWordWrap(True)
        self.visual_layout.insertWidget(0, self.visual_warning_lbl)

        l_slots_lay = QVBoxLayout()
        r_slots_lay = QVBoxLayout()

        center_board = QLabel(
            "РАБОЧАЯ ЗОНА\n"
            "Смена платы вверху не сбрасывает карту станка.\n"
            "Выделите строки в «Карте заправки» — позиции подсветятся здесь (голубой), пока не отмечены в станке."
        )
        center_board.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_board.setStyleSheet(
            "background-color: #444; border: 2px dashed #777; border-radius: 10px; font-weight: bold; font-size: 12px; color: #FFF;")

        m_lay.addLayout(l_slots_lay)
        m_lay.addWidget(center_board, stretch=2)
        m_lay.addLayout(r_slots_lay)

        main_split.addWidget(machine_group, stretch=3)

        side_group = QGroupBox("ФИЗИЧЕСКИ НАХОДИТСЯ В СТАНКЕ")
        s_lay = QVBoxLayout(side_group)
        side_table = QTableWidget()
        side_table.setColumnCount(2)
        side_table.setHorizontalHeaderLabels(["СЛОТ", "КОМПОНЕНТ"])
        side_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        side_table.setStyleSheet("background-color: #222; color: white;")
        s_lay.addWidget(side_table)
        main_split.addWidget(side_group, stretch=2)

        global_setup = set()
        for comps in self.setup_completed.values():
            global_setup.update(comps)

        components = []
        for name in global_setup:
            info = self.global_feeder_map.get(name)
            if info and info['station'] == station_filter:
                components.append((name, info['slot']))

        limit = 50 if is_chipshooter else sum(sp.value() for sp in self.spins.values())
        if len(components) > limit:
            self.visual_warning_lbl.setText(
                f"⚠️ ВНИМАНИЕ: Установлено деталей ({len(components)} шт.) больше, чем доступно слотов ({limit} шт.)! Снимите лишние.")
        else:
            self.visual_warning_lbl.setText("")

        components.sort(key=lambda x: str(x[1]))
        side_table.setRowCount(len(components))
        for i, (comp, slot) in enumerate(components):
            side_table.setItem(i, 0, QTableWidgetItem(str(slot)))
            side_table.setItem(i, 1, QTableWidgetItem(str(comp)))

        chip_prev = self._preview_for_setup_selection(global_setup, 'ЧИПШУТЕР') if is_chipshooter else {}

        class ClickableLabel(QLabel):
            def __init__(self, text, comp_name, parent_ref):
                super().__init__(text)
                self.comp_name = comp_name
                self.parent_ref = parent_ref

            def mousePressEvent(self, event):
                if event.button() == Qt.MouseButton.LeftButton and self.comp_name:
                    removed = False
                    for b_name in list(self.parent_ref.setup_completed.keys()):
                        d_list = self.parent_ref.setup_completed[b_name]
                        if self.comp_name in d_list:
                            d_list.remove(self.comp_name)
                            removed = True
                    if removed:
                        self.parent_ref.table_setup.clearSelection()
                        self.parent_ref.save_progress()
                        self.parent_ref.render_board_data(rebuild_visual=True)
                        self.parent_ref.refresh_ui_status()

        def create_slot_widget(slot_id, display_text, removable_name=""):
            w = QWidget()
            w.setStyleSheet("background-color: #333; border: 1px solid #555; margin: 1px;")
            w.setMinimumHeight(24)
            l = QHBoxLayout(w)
            l.setContentsMargins(2, 2, 2, 2)
            lbl_s = QLabel(str(slot_id))
            lbl_s.setStyleSheet(
                "color: #AAA; font-weight: bold; font-size: 11px; border: none; background: transparent;")
            lbl_s.setFixedWidth(40)

            show = display_text if display_text else "—"
            lbl_c = ClickableLabel(show, removable_name, self)
            lbl_c.setWordWrap(True)
            if removable_name:
                lbl_c.setStyleSheet(
                    "color: white; font-size: 11px; border: none; background: transparent; cursor: pointer;")
                lbl_c.setToolTip("Кликните, чтобы снять деталь со станка")
            elif display_text and display_text != "—":
                lbl_c.setStyleSheet(
                    "color: #90CAF9; font-size: 11px; border: none; background: transparent;")
                lbl_c.setToolTip("Выделено в «Карте заправки» (ещё не в станке)")
            else:
                lbl_c.setStyleSheet(
                    "color: #777; font-size: 11px; border: none; background: transparent;")

            l.addWidget(lbl_s)
            l.addWidget(lbl_c)

            if removable_name:
                self.visual_slot_widgets[removable_name] = w
            return w

        if is_chipshooter:
            for i in range(5, 30):
                slot_l = f"L{i}"
                comp_l_list = [c[0] for c in components if c[1] == slot_l]
                inst_l = comp_l_list[0] if comp_l_list else ""
                pv_l = chip_prev.get(slot_l, "")
                w_l = create_slot_widget(slot_l, inst_l or pv_l, inst_l)
                l_slots_lay.addWidget(w_l)
                self._visual_slot_by_pos[slot_l] = w_l

                slot_r = f"R{i}"
                comp_r_list = [c[0] for c in components if c[1] == slot_r]
                inst_r = comp_r_list[0] if comp_r_list else ""
                pv_r = chip_prev.get(slot_r, "")
                w_r = create_slot_widget(slot_r, inst_r or pv_r, inst_r)
                r_slots_lay.addWidget(w_r)
                self._visual_slot_by_pos[slot_r] = w_r
        else:
            pv_sp = self._preview_for_setup_selection(global_setup, 'ПАВУК')
            l_comps = [(c, s) for c, s in components if str(s).startswith('L')]
            r_comps = [(c, s) for c, s in components if str(s).startswith('R')]

            for c, s in l_comps:
                cl = str(s).split()[0].strip().upper()
                disp = c or pv_sp.get(cl, "")
                rem = c if c else ""
                ww = create_slot_widget(str(s), disp or "—", rem)
                l_slots_lay.addWidget(ww)
                if cl:
                    self._visual_slot_by_pos[cl] = ww
            for c, s in r_comps:
                cl = str(s).split()[0].strip().upper()
                disp = c or pv_sp.get(cl, "")
                rem = c if c else ""
                ww = create_slot_widget(str(s), disp or "—", rem)
                r_slots_lay.addWidget(ww)
                if cl:
                    self._visual_slot_by_pos[cl] = ww
            l_slots_lay.addStretch()
            r_slots_lay.addStretch()

        self.refresh_ui_status()

    def _on_item_double_clicked(self, item):
        self.toggle_completed()

    def render_progress_panel(self, board):
        for i in reversed(range(self.progress_layout.count())):
            w = self.progress_layout.itemAt(i).widget()
            if w: w.deleteLater()

        if board not in self.instr_completed: self.instr_completed[board] = []

        sorted_batches = sorted(self.batch_comps.keys())
        for b in sorted_batches:
            comps_in_batch = self.batch_comps[b]
            total = len(comps_in_batch)
            completed = sum(1 for c in comps_in_batch if c in self.instr_completed[board])

            lbl = QLabel(f"{b}: {completed} / {total}")
            lbl.setStyleSheet("font-weight: bold; font-size: 12px; color: #FFF; min-width: 100px;")

            pb = QProgressBar()
            pb.setFixedHeight(12)
            pb.setMaximum(total)
            pb.setValue(completed)
            pb.setStyleSheet(
                "QProgressBar { border: 1px solid #555; border-radius: 5px; text-align: center; color: white; background-color: #222; } QProgressBar::chunk { background-color: #4CAF50; }")

            row_widget = QWidget()
            hlay = QHBoxLayout(row_widget)
            hlay.setContentsMargins(0, 0, 0, 5)
            hlay.addWidget(lbl)
            hlay.addWidget(pb)
            self.progress_layout.addWidget(row_widget)

    def toggle_completed(self):
        board = self.board_sel.currentText()
        if not board: return

        fw = QApplication.focusWidget()
        if fw == self.table_setup:
            table, name_col, target_dict = self.table_setup, 3, self.setup_completed
        elif fw == self.table_instr:
            table, name_col, target_dict = self.table_instr, 1, self.instr_completed
        else:
            return

        selected_items = table.selectedItems()
        if not selected_items: return

        # Save selection state to restore later if needed
        selected_rows = list(set(item.row() for item in selected_items))

        comps_to_toggle = [table.item(r, name_col).text() for r in selected_rows if table.item(r, name_col).text()]

        if board not in target_dict: target_dict[board] = []

        all_done = all(c in target_dict[board] for c in comps_to_toggle)
        for r in selected_rows:
            c = table.item(r, name_col).text() if table.item(r, name_col) else ""
            if not c: continue

            if all_done:
                if c in target_dict[board]:
                    target_dict[board].remove(c)
            else:
                if c not in target_dict[board]:
                    target_dict[board].append(c)

        self.save_progress()

        if fw == self.table_setup:
            self.refresh_ui_status()
            self.render_visual_map()
        else:
            self.render_progress_panel(board)
            self.refresh_ui_status()
            self.update_board_combobox()

    def refresh_ui_status(self):
        board = self.board_sel.currentText()
        setup_done = self.setup_completed.get(board, [])
        instr_done = self.instr_completed.get(board, [])

        self._color_table(self.table_setup, 3, setup_done, True)
        self._color_table(self.table_instr, 1, instr_done, False)

        global_setup = set()
        for comps in self.setup_completed.values():
            global_setup.update(comps)

        future_needed = set()
        if self.all_data is not None:
            for b_name in self.all_data['Sheet'].unique():
                if b_name != board and not self.is_board_completed(b_name):
                    future_needed.update(self.all_data[self.all_data['Sheet'] == b_name]['Name'].unique())

        curr_needed_active = set()
        if self.all_data is not None:
            curr_all = set(self.all_data[self.all_data['Sheet'] == board]['Name'].unique())
            curr_done = set(self.instr_completed.get(board, []))
            curr_needed_active = curr_all - curr_done

        if hasattr(self, 'visual_slot_widgets'):
            for comp, widget in self.visual_slot_widgets.items():
                if comp in global_setup:
                    if comp in curr_needed_active:
                        widget.setStyleSheet("background-color: #1b5e20; border: 2px solid #4CAF50; margin: 1px;") # Green
                    elif comp in future_needed:
                        widget.setStyleSheet("background-color: #D84315; border: 2px solid #FF7043; margin: 1px;") # Orange
                    else:
                        widget.setStyleSheet("background-color: #5D4037; border: 2px solid #8D6E63; margin: 1px;") # Brown
                else:
                    widget.setStyleSheet("background-color: #333; border: 1px solid #555; margin: 1px;")

    def _color_table(self, table, name_col, done_list, is_setup_table):
        for r in range(table.rowCount()):
            comp = table.item(r, name_col).text()
            is_done = comp in done_list

            bg = QColor("#1b5e20") if is_done else QColor("#222")

            for c in range(table.columnCount()):
                item = table.item(r, c)
                if not is_done:
                    val = item.text()
                    mq = re.search(r'№\s*(\d+)', val)
                    q_bad = mq is not None and int(mq.group(1)) > 1
                    item.setBackground(QColor("#424242") if q_bad else bg)
                else:
                    item.setBackground(bg)

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

    def fill_t(self, table, df):
        table.setRowCount(0)
        table.setColumnCount(len(df.columns))
        table.setHorizontalHeaderLabels(df.columns)
        table.setRowCount(len(df))
        for i in range(len(df)):
            for j in range(len(df.columns)):
                val = str(df.iloc[i, j])
                item = QTableWidgetItem(val)
                if "ЧИПШУТЕР" in val: item.setForeground(QColor("#81C784"))
                if "ПАВУК" in val: item.setForeground(QColor("#64B5F6"))
                table.setItem(i, j, item)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    w = SMTNavigator()
    w.show()
    sys.exit(app.exec())
