import re
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                             QProgressBar, QApplication, QLabel,
                             QTableWidgetItem, QHeaderView)
from PyQt6.QtGui import QColor


class ProgressPanelMixin:
    """Таблицы и панели прогресса установки."""

    def _on_item_double_clicked(self, item):
        self.toggle_completed()

    def render_progress_panel(self, board):
        for i in reversed(range(self.progress_layout.count())):
            w = self.progress_layout.itemAt(i).widget()
            if w:
                w.deleteLater()

        if board not in self.instr_completed:
            self.instr_completed[board] = []

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
        if not board:
            return

        fw = QApplication.focusWidget()
        if fw == self.table_setup:
            table, name_col, target_dict = self.table_setup, 3, self.setup_completed
        elif fw == self.table_instr:
            table, name_col, target_dict = self.table_instr, 1, self.instr_completed
        else:
            return

        selected_items = table.selectedItems()
        if not selected_items:
            return

        # Save selection state to restore later if needed
        selected_rows = list(set(item.row() for item in selected_items))

        comps_to_toggle = [table.item(r, name_col).text() for r in selected_rows if table.item(r, name_col).text()]

        if board not in target_dict:
            target_dict[board] = []

        all_done = all(c in target_dict[board] for c in comps_to_toggle)
        for r in selected_rows:
            c = table.item(r, name_col).text() if table.item(r, name_col) else ""
            if not c:
                continue

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
                        widget.setStyleSheet("background-color: #1b5e20; border: 2px solid #4CAF50; margin: 1px;")  # Green
                    elif comp in future_needed:
                        widget.setStyleSheet("background-color: #D84315; border: 2px solid #FF7043; margin: 1px;")  # Orange
                    else:
                        widget.setStyleSheet("background-color: #5D4037; border: 2px solid #8D6E63; margin: 1px;")  # Brown
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

    def fill_t(self, table, df):
        table.setRowCount(0)
        table.setColumnCount(len(df.columns))
        table.setHorizontalHeaderLabels(df.columns)
        table.setRowCount(len(df))
        for i in range(len(df)):
            for j in range(len(df.columns)):
                val = str(df.iloc[i, j])
                item = QTableWidgetItem(val)
                if "ЧИПШУТЕР" in val:
                    item.setForeground(QColor("#81C784"))
                if "ПАВУК" in val:
                    item.setForeground(QColor("#64B5F6"))
                table.setItem(i, j, item)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
