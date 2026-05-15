import re
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                             QScrollArea, QComboBox, QTableWidget,
                             QTableWidgetItem, QHeaderView, QLabel)
from PyQt6.QtCore import Qt


class VisualMapMixin:
    """Отрисовка слотов и станков."""

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
