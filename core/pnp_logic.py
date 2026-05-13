import re
import pandas as pd
from PyQt6.QtWidgets import QMessageBox


class PnPLogicMixin:
    """Математика батчей, расчет очередей станков, логика поиска совпадений между платами."""

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

    def calculate_global(self):
        if self.all_data is None:
            return

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
        if self.all_data is None:
            return
        board = self.board_sel.currentText()
        if not board:
            return

        # Ищем следующую лучшую плату
        current_board_data = self.all_data[self.all_data['Sheet'] == board]
        unique_parts_current = set(current_board_data['Name'].unique())

        other_boards_matches = []
        for other_board in self.all_data['Sheet'].unique():
            if other_board == board:
                continue
            if self.is_board_completed(other_board):
                continue

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

            # Count the number of boards each component appears on to prioritize common components
            board_counts = self.all_data.groupby('Name')['Sheet'].nunique().reset_index()
            board_counts.rename(columns={'Sheet': 'board_count'}, inplace=True)

            cs_unplaced_df = cs_needed[~cs_needed['Name'].isin(board_feeder_map.keys())]
            cs_unplaced_df = cs_unplaced_df.merge(board_counts, on='Name', how='left')
            cs_unplaced_df['board_count'] = pd.to_numeric(cs_unplaced_df['board_count'], errors='coerce').fillna(0).astype(int)
            cs_unplaced_df = cs_unplaced_df.sort_values(by=['board_count', 'Name'], ascending=[False, True])
            cs_unplaced = cs_unplaced_df['Name'].tolist()
            used_cs_slots_in_batch = {}

            def assign_next_cs_slot(comp_name):
                b = 1
                while True:
                    if b not in used_cs_slots_in_batch:
                        used_cs_slots_in_batch[b] = set()

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
                    if b > 50:
                        return None

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
                if pv_limits.get(w, 0) <= 0:
                    return False
                slots_needed = 3 if w in (32, 44) else 2
                b = 1
                while True:
                    if b not in pv_batch_usage:
                        pv_batch_usage[b] = {s: 0 for s in self.tape_sizes}
                    if b not in pv_batch_slots_used:
                        pv_batch_slots_used[b] = set()

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
                                    for c in clusters:
                                        pv_batch_slots_used[b].add(c)
                                    pv_batch_usage[b][w] = pv_batch_usage[b].get(w, 0) + 1
                                    board_feeder_map[name] = {'batch': b, 'slot': f'{assigned_cluster} ({w}мм)', 'station': 'ПАВУК'}
                                    self.global_feeder_map[name] = board_feeder_map[name]
                                    return True

                        for prefix in ['L', 'R']:
                            for start_idx in range(1, 21 - slots_needed + 1):
                                cluster = [f"{prefix}{i}" for i in range(start_idx, start_idx + slots_needed)]
                                if all(c not in locked_pv_slots and c not in pv_batch_slots_used[b] for c in cluster):
                                    assigned_cluster = f"{cluster[0]}-{cluster[-1]}"
                                    for c in cluster:
                                        pv_batch_slots_used[b].add(c)
                                    pv_batch_usage[b][w] = pv_batch_usage[b].get(w, 0) + 1
                                    board_feeder_map[name] = {'batch': b, 'slot': f'{assigned_cluster} ({w}мм)', 'station': 'ПАВУК'}
                                    self.global_feeder_map[name] = board_feeder_map[name]
                                    return True
                    b += 1
                    if b > 50:
                        return False

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
            if other_board == board:
                continue
            if self.is_board_completed(other_board):
                continue

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
