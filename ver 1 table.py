import sys
import pandas as pd
import re
import json
import os
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QFileDialog,
                             QSpinBox, QGroupBox, QMessageBox, QTableWidget,
                             QTableWidgetItem, QHeaderView, QTabWidget, QFormLayout, QComboBox, QProgressBar)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QShortcut, QKeySequence


class SMTNavigator(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SMT Navigator: Пошаговая Перезаправка")
        self.resize(1300, 900)

        self.all_data = None
        self.feeder_map = {}
        self.tape_sizes = [8, 12, 16, 24, 32, 44]
        self.spins = {}
        self.progress_file = "smt_progress.json"
        # Теперь храним список выполненных деталей (по именам) для каждой платы
        self.completed_components = self.load_progress()
        self.batch_comps = {}

        # Горячие клавиши для отметки выполнения (Enter)
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

        # Панель управления
        top = QHBoxLayout()
        cfg_box = QGroupBox("Наличие фидеров в цеху")
        cfg_grid = QFormLayout()
        for s in self.tape_sizes:
            sp = QSpinBox();
            sp.setRange(0, 500);
            sp.setValue(0 if s == 8 else 10)
            cfg_grid.addRow(f"Фидеры {s}мм:", sp)
            self.spins[s] = sp
        cfg_box.setLayout(cfg_grid)
        top.addWidget(cfg_box)

        ctrl_layout = QVBoxLayout()
        self.btn_load = QPushButton("📂 ЗАГРУЗИТЬ ПРОЕКТ")
        self.btn_load.clicked.connect(self.load_file)

        self.btn_run = QPushButton("⚡ РАССЧИТАТЬ ВСЕ ПЛАТЫ")
        self.btn_run.setStyleSheet("background-color: #2E7D32; color: white;")
        self.btn_run.clicked.connect(self.calculate_global)

        ctrl_layout.addWidget(self.btn_load)
        ctrl_layout.addWidget(self.btn_run)
        top.addLayout(ctrl_layout)
        layout.addLayout(top)

        # СЕЛЕКТОР ПЛАТЫ (Главный элемент)
        board_sel_box = QGroupBox("УПРАВЛЕНИЕ ТЕКУЩЕЙ СБОРКОЙ")
        bs_layout = QHBoxLayout()
        bs_layout.addWidget(QLabel("<b>ВЫБЕРИТЕ ПЛАТУ ДЛЯ РАБОТЫ:</b>"))
        self.board_sel = QComboBox()
        self.board_sel.currentIndexChanged.connect(self.render_board_data)
        bs_layout.addWidget(self.board_sel)
        self.recharge_warning = QLabel("")  # Тут будет текст "НУЖНА ПЕРЕЗАПРАВКА"
        bs_layout.addWidget(self.recharge_warning)
        board_sel_box.setLayout(bs_layout)
        layout.addWidget(board_sel_box)

        # ПАНЕЛЬ ПРОГРЕССА
        self.progress_box = QGroupBox("ПРОГРЕСС ОЧЕРЕДЕЙ (ВЫДЕЛИТЕ В ТАБЛИЦЕ И НАЖМИТЕ ENTER)")
        self.progress_layout = QVBoxLayout()
        self.progress_box.setLayout(self.progress_layout)
        layout.addWidget(self.progress_box)

        # СВОДКА ПРОЕКТА (Добавляем визуальную информацию)
        self.summary_box = QGroupBox("ОБЩАЯ ИНФОРМАЦИЯ ПО ПЛАТЕ")
        slayout = QHBoxLayout()
        self.lbl_total_parts = QLabel("Всего компонентов: 0")
        self.lbl_unique_parts = QLabel("Уникальных: 0")
        self.lbl_feeders_used = QLabel("Занято фидеров: 0")
        for lbl in (self.lbl_total_parts, self.lbl_unique_parts, self.lbl_feeders_used):
            lbl.setStyleSheet("font-size: 14px; color: #E0E0E0; background-color: #333; padding: 5px; border-radius: 5px;")
            slayout.addWidget(lbl)
        self.summary_box.setLayout(slayout)
        layout.addWidget(self.summary_box)

        # ТАБЛИЦЫ
        self.tabs = QTabWidget()

        self.table_setup = QTableWidget()
        self.tabs.addTab(self.table_setup, "🛠 КАРТА ЗАПРАВКИ (Что поставить в станок)")

        self.table_instr = QTableWidget()
        self.tabs.addTab(self.table_instr, "📋 СПИСОК УСТАНОВКИ (Куда ставить на плате)")

        layout.addWidget(self.tabs)

    def load_file(self):
        fn, _ = QFileDialog.getOpenFileName(self, "BOM", "", "Excel (*.xlsx *.xls)")
        if fn:
            xls = pd.ExcelFile(fn)
            self.all_data = pd.concat([pd.read_excel(xls, s).assign(Sheet=s) for s in xls.sheet_names])
            self.all_data.columns = self.all_data.columns.str.strip()
            self.board_sel.clear()
            self.board_sel.addItems(xls.sheet_names)

    def calculate_global(self):
        if self.all_data is None: return

        # 1. Глобальная привязка всех компонентов к очередям (как в прошлый раз)
        grouped = self.all_data.groupby(['Name', 'Тип ленты']).agg(
            {'Quantity': 'sum', 'Sheet': 'nunique'}).reset_index()
        grouped = grouped.sort_values(by=['Sheet', 'Quantity'], ascending=False)

        def parse_w(x):
            m = re.search(r'(\d+)', str(x));
            w = int(m.group(1)) if m else 8
            return next((s for s in self.tape_sizes if w <= s), 44)

        grouped['mm'] = grouped['Тип ленты'].apply(parse_w)
        self.feeder_map = {}  # Name -> {Batch, Slot, Station}

        # Расчет очередей
        # (Логика Batching остается, но теперь мы сохраняем ID очереди в карту)
        # --- 8мм Чипшутер ---
        cs_8 = grouped[grouped['mm'] == 8].copy()
        b_id = 1
        while not cs_8.empty:
            batch = cs_8.head(50)
            for i, (idx, row) in enumerate(batch.iterrows()):
                pos = f"{'L' if i < 25 else 'R'}{i % 25 + 5}"
                self.feeder_map[row['Name']] = {'batch': b_id, 'slot': pos, 'station': 'ЧИПШУТЕР'}
            cs_8 = cs_8.drop(batch.index)
            b_id += 1

        # --- Остальное Павук ---
        rem = grouped[grouped['mm'] != 8].copy()
        limits = {s: self.spins[s].value() for s in self.tape_sizes}
        sp_b_id = 1
        while not rem.empty:
            usage = {s: 0 for s in self.tape_sizes}
            dropped = []
            added = False
            l_idx = 1
            r_idx = 1
            for idx, row in rem.iterrows():
                w = row['mm']
                slots_needed = 3 if w in (32, 44) else 2

                if limits[w] > 0 and usage[w] < limits[w]:
                    assigned_slot = None
                    if l_idx + slots_needed - 1 <= 20:
                        assigned_slot = f"L{l_idx}-L{l_idx + slots_needed - 1}"
                        l_idx += slots_needed
                    elif r_idx + slots_needed - 1 <= 20:
                        assigned_slot = f"R{r_idx}-R{r_idx + slots_needed - 1}"
                        r_idx += slots_needed

                    if assigned_slot:
                        usage[w] += 1
                        self.feeder_map[row['Name']] = {'batch': sp_b_id, 'slot': f'{assigned_slot} ({w}мм)', 'station': 'ПАВУК'}
                        dropped.append(idx)
                        added = True

            if not added: break
            rem = rem.drop(dropped)
            sp_b_id += 1

        self.render_board_data()
        QMessageBox.information(self, "Готово", "Глобальный расчет завершен. Выберите плату.")

    def render_board_data(self):
        board = self.board_sel.currentText()
        if not board or not self.feeder_map: return

        # Данные только для этой платы
        df = self.all_data[self.all_data['Sheet'] == board].groupby('Name').agg({
            'Quantity': 'sum', 'Designator': lambda x: ", ".join(x.astype(str))
        }).reset_index()

        # Обновляем сводку
        total_qty = df['Quantity'].sum()
        unique_parts = len(df)
        self.lbl_total_parts.setText(f"Всего компонентов: {total_qty}")
        self.lbl_unique_parts.setText(f"Уникальных: {unique_parts}")
        self.lbl_feeders_used.setText(f"Задействовано слотов: {unique_parts}")

        setup_rows = []
        instr_rows = []
        self.batch_comps = {}

        for _, r in df.iterrows():
            info = self.feeder_map.get(r['Name'], {'batch': '?', 'slot': '?', 'station': '?'})
            b_name = f"{info['station']} №{info['batch']}"

            if b_name not in self.batch_comps:
                self.batch_comps[b_name] = []
            self.batch_comps[b_name].append(r['Name'])

            # Данные для заправки
            setup_rows.append({
                'СТАНОК': info['station'],
                'ОЧЕРЕДЬ (ЗАГРУЗКА)': f"№ {info['batch']}",
                'ПОЗИЦИЯ / ФИДЕР': info['slot'],
                'ЧТО СТАВИМ': r['Name'],
                'НУЖНО ШТ': r['Quantity']
            })

            # Данные для инструкции
            instr_rows.append({
                'ПРИОРИТЕТ': f"{info['station']} (Батч {info['batch']})",
                'ДЕТАЛЬ': r['Name'],
                'СЛОТ': info['slot'],
                'КОЛ-ВО': r['Quantity'],
                'ПОЗИЦИИ (DESIGNATORS)': r['Designator']
            })

        # Обновляем предупреждение о перезаправке
        cs_batches = [b for b in self.batch_comps.keys() if "ЧИПШУТЕР" in b]
        pv_batches = [b for b in self.batch_comps.keys() if "ПАВУК" in b]

        warn_text = []
        if len(cs_batches) > 1:
            warn_text.append(f"Чипшутер - {len(cs_batches)} захода")
        if len(pv_batches) > 1:
            warn_text.append(f"Павук - {len(pv_batches)} захода")

        if warn_text:
            self.recharge_warning.setText(f"⚠️ ТРЕБУЕТСЯ ПЕРЕЗАПРАВКА! ({', '.join(warn_text)})")
            self.recharge_warning.setStyleSheet("color: #FF5252; font-weight: bold; font-size: 14px;")
        else:
            self.recharge_warning.setText("✅ Влазит в один заход (без перезаправок)")
            self.recharge_warning.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 14px;")

        # Сортируем и выводим
        df_setup = pd.DataFrame(setup_rows).sort_values(by=['СТАНОК', 'ОЧЕРЕДЬ (ЗАГРУЗКА)'])
        df_instr = pd.DataFrame(instr_rows).sort_values(by='ПРИОРИТЕТ')

        self.fill_t(self.table_setup, df_setup)
        self.fill_t(self.table_instr, df_instr)
        self.render_progress_panel(board)
        self.refresh_ui_status()

    def render_progress_panel(self, board):
        # Очищаем старые виджеты
        for i in reversed(range(self.progress_layout.count())):
            w = self.progress_layout.itemAt(i).widget()
            if w:
                w.deleteLater()

        if board not in self.completed_components:
            self.completed_components[board] = []

        sorted_batches = sorted(self.batch_comps.keys())
        for b in sorted_batches:
            comps_in_batch = self.batch_comps[b]
            total = len(comps_in_batch)
            completed = sum(1 for c in comps_in_batch if c in self.completed_components[board])

            lbl = QLabel(f"{b}: {completed} / {total}")
            lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #FFF; min-width: 150px;")

            pb = QProgressBar()
            pb.setMaximum(total)
            pb.setValue(completed)
            pb.setStyleSheet("""
                QProgressBar { border: 1px solid #555; border-radius: 5px; text-align: center; color: white; background-color: #222; }
                QProgressBar::chunk { background-color: #4CAF50; }
            """)

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
            table = self.table_setup
            name_col = 3  # 'ЧТО СТАВИМ'
        elif fw == self.table_instr:
            table = self.table_instr
            name_col = 1  # 'ДЕТАЛЬ'
        else:
            return

        selected_rows = list(set(item.row() for item in table.selectedItems()))
        if not selected_rows: return

        comps_to_toggle = []
        for r in selected_rows:
            comp = table.item(r, name_col).text()
            if comp: comps_to_toggle.append(comp)

        if board not in self.completed_components:
            self.completed_components[board] = []

        # Если ВСЕ выделенные компоненты уже выполнены, значит мы их снимаем.
        # Если хотя бы один не выполнен - мы их все отмечаем выполненными.
        all_done = all(c in self.completed_components[board] for c in comps_to_toggle)
        for c in comps_to_toggle:
            if all_done:
                if c in self.completed_components[board]:
                    self.completed_components[board].remove(c)
            else:
                if c not in self.completed_components[board]:
                    self.completed_components[board].append(c)

        self.save_progress()
        self.render_progress_panel(board)
        self.refresh_ui_status()

    def refresh_ui_status(self):
        board = self.board_sel.currentText()
        done_list = self.completed_components.get(board, [])
        self._color_table(self.table_setup, 3, done_list)
        self._color_table(self.table_instr, 1, done_list)

    def _color_table(self, table, name_col, done_list):
        for r in range(table.rowCount()):
            comp = table.item(r, name_col).text()
            is_done = comp in done_list
            bg = QColor("#1b5e20") if is_done else QColor("#222")
            for c in range(table.columnCount()):
                item = table.item(r, c)
                if not is_done:
                    val = item.text()
                    if "№" in val and "1" not in val:
                        item.setBackground(QColor("#424242"))
                    else:
                        item.setBackground(bg)
                else:
                    item.setBackground(bg)

    def load_progress(self):
        if os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_progress(self):
        try:
            with open(self.progress_file, "w", encoding="utf-8") as f:
                json.dump(self.completed_components, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print("Ошибка сохранения прогресса:", e)

    def fill_t(self, table, df):
        table.setRowCount(0);
        table.setColumnCount(len(df.columns))
        table.setHorizontalHeaderLabels(df.columns)
        table.setRowCount(len(df))
        for i in range(len(df)):
            for j in range(len(df.columns)):
                val = str(df.iloc[i, j])
                item = QTableWidgetItem(val)
                if "ЧИПШУТЕР" in val: item.setForeground(QColor("#81C784"))
                if "ПАВУК" in val: item.setForeground(QColor("#64B5F6"))
                # Выделение серым фоном теперь происходит в `_color_table`
                table.setItem(i, j, item)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)


if __name__ == "__main__":
    app = QApplication(sys.argv);
    app.setStyle('Fusion')
    w = SMTNavigator();
    w.show();
    sys.exit(app.exec())