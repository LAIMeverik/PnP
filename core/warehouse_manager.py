import os
import re
import json
import pandas as pd
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QLabel, QFileDialog, QSpinBox, QGroupBox,
                             QMessageBox, QTableWidget, QTableWidgetItem,
                             QHeaderView, QComboBox, QScrollArea, QLineEdit)
from PyQt6.QtGui import QColor


class WarehouseMixin:
    """Управление складом: сохранение в warehouse.json, вычет компонентов, логика катушек."""

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
        cols = ["Номер", "Название", "Ширина Ленты", "Катушка", "Остаток"]
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
