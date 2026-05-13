"""
Pure Python PnP logic — no Qt dependencies.
Ported from core/pnp_logic.py (PnPLogicMixin).
"""
import re
import pandas as pd

CS_BANK_SLOTS = [f"L{i}" for i in range(5, 30)] + [f"R{i}" for i in range(5, 30)]
TAPE_SIZES = [8, 12, 16, 24, 32, 44]
DEFAULT_TAPE_WIDTH = 8


def cs_slot_order(slot):
    m = re.match(r'^\s*([LR])\s*(\d+)\s*$', str(slot).strip(), re.I)
    if not m:
        return 9999
    side, n = m.group(1).upper(), int(m.group(2))
    if side == 'L' and 5 <= n <= 29:
        return n - 5
    if side == 'R' and 5 <= n <= 29:
        return 25 + (n - 5)
    return 8000 + n


def pv_cluster(full_slot):
    return str(full_slot).split(' ')[0].strip().upper()


def normalize_loader_queues(feeder_map, global_feeder_map, board_names):
    """Renumber batch indices so they start from 1 without gaps."""
    for station in ('ЧИПШУТЕР', 'ПАВУК'):
        rows = []
        for name in board_names:
            info = feeder_map.get(name)
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
            bank = len(CS_BANK_SLOTS)

            def _chip_linear_key(row):
                _name, ob, sl = row
                so = cs_slot_order(sl)
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
                feeder_map[name]['batch'] = nb
                global_feeder_map[name]['batch'] = nb
        else:
            rows.sort(key=lambda x: (x[1], pv_cluster(x[2]), str(x[2])))
            order = []
            for _, ob, _ in rows:
                if ob not in order:
                    order.append(ob)
            mapping = {old: i + 1 for i, old in enumerate(order)}
            for name, ob, _ in rows:
                nb = mapping[ob]
                feeder_map[name]['batch'] = nb
                global_feeder_map[name]['batch'] = nb


def is_board_completed(all_data, board_name, instr_completed):
    board_data = all_data[all_data['Sheet'] == board_name]
    unique_parts = set(board_data['Name'].unique())
    if not unique_parts:
        return False
    done = set(instr_completed.get(board_name, []))
    return all(p in done for p in unique_parts)


def calculate_board(all_data, board, global_feeder_map, setup_completed,
                    tape_limits, auto_assign=True):
    """
    Calculate setup and instruction tables for a board.

    Returns dict with:
      setup_rows, instr_rows, batch_comps, feeder_map (for board),
      global_feeder_map (updated), warnings, summary, next_boards
    """
    warnings = []

    required_cols = ('Name', 'Quantity', 'Designator', 'Тип ленты')
    missing = [c for c in required_cols if c not in all_data.columns]
    if missing:
        return {'error': f"В Excel не хватает колонок: {', '.join(missing)}"}

    current_board_data = all_data[all_data['Sheet'] == board]
    unique_parts_current = set(current_board_data['Name'].unique())

    df_board = current_board_data.groupby('Name').agg({
        'Quantity': 'sum',
        'Designator': lambda x: ", ".join(
            sorted(set(
                str(item).strip()
                for sublist in x
                for item in str(sublist).split(',')
                if str(item).strip()
            ))
        ),
    }).reset_index()

    def parse_w(x):
        m = re.search(r'(\d+)', str(x))
        w = int(m.group(1)) if m else DEFAULT_TAPE_WIDTH
        return next((s for s in TAPE_SIZES if w <= s), 44)

    all_comps_info = all_data.groupby('Name').agg({'Тип ленты': 'first'}).reset_index()
    all_comps_info['mm'] = all_comps_info['Тип ленты'].apply(parse_w)

    # Build board feeder map from global
    global_setup = set()
    for comps in setup_completed.values():
        global_setup.update(comps)

    board_feeder_map = {}
    for nm in unique_parts_current:
        if nm in global_feeder_map:
            board_feeder_map[nm] = dict(global_feeder_map[nm])

    if auto_assign:
        cs_needed = all_comps_info[
            all_comps_info['Name'].isin(unique_parts_current) &
            (all_comps_info['mm'] == 8)
        ]
        pv_needed = all_comps_info[
            all_comps_info['Name'].isin(unique_parts_current) &
            (all_comps_info['mm'] > 8)
        ]

        # Lock slots physically in the machine already
        locked_cs_slots = set()
        for name in global_setup:
            info = global_feeder_map.get(name)
            if info and info['station'] == 'ЧИПШУТЕР':
                locked_cs_slots.add(info['slot'])
                if name in unique_parts_current:
                    board_feeder_map[name] = {
                        'batch': 1, 'slot': info['slot'], 'station': 'ЧИПШУТЕР'
                    }

        board_counts = all_data.groupby('Name')['Sheet'].nunique().reset_index()
        board_counts.rename(columns={'Sheet': 'board_count'}, inplace=True)

        cs_unplaced_df = cs_needed[~cs_needed['Name'].isin(board_feeder_map.keys())]
        cs_unplaced_df = cs_unplaced_df.merge(board_counts, on='Name', how='left')
        cs_unplaced_df['board_count'] = (
            pd.to_numeric(cs_unplaced_df['board_count'], errors='coerce')
            .fillna(0).astype(int)
        )
        cs_unplaced_df = cs_unplaced_df.sort_values(
            by=['board_count', 'Name'], ascending=[False, True]
        )
        cs_unplaced = cs_unplaced_df['Name'].tolist()

        used_cs_slots_in_batch = {}

        def assign_next_cs_slot(comp_name):
            b = 1
            while True:
                if b not in used_cs_slots_in_batch:
                    used_cs_slots_in_batch[b] = set()
                hist_info = global_feeder_map.get(comp_name)
                if hist_info and hist_info['station'] == 'ЧИПШУТЕР':
                    s = hist_info['slot']
                    if s not in locked_cs_slots and s not in used_cs_slots_in_batch[b]:
                        used_cs_slots_in_batch[b].add(s)
                        return b, s
                for slot in CS_BANK_SLOTS:
                    if slot not in locked_cs_slots and slot not in used_cs_slots_in_batch[b]:
                        used_cs_slots_in_batch[b].add(slot)
                        return b, slot
                b += 1
                if b > 50:
                    return None

        for name in cs_unplaced:
            slot_res = assign_next_cs_slot(name)
            if slot_res is None:
                warnings.append(f"ЧИПШУТЕР: не удалось разместить '{name}' — нет свободных слотов.")
                continue
            b, s = slot_res
            board_feeder_map[name] = {'batch': b, 'slot': s, 'station': 'ЧИПШУТЕР'}
            global_feeder_map[name] = dict(board_feeder_map[name])

        # Spider (ПАВУК)
        locked_pv_slots = set()
        for name in global_setup:
            info = global_feeder_map.get(name)
            if info and info['station'] == 'ПАВУК':
                base_slot_label = info['slot'].split(' ')[0]
                m = re.match(r'([LR])(\d+)-([LR])(\d+)', base_slot_label)
                if m:
                    prefix = m.group(1)
                    start_n, end_n = int(m.group(2)), int(m.group(4))
                    for i in range(start_n, end_n + 1):
                        locked_pv_slots.add(f"{prefix}{i}")
                if name in unique_parts_current:
                    board_feeder_map[name] = {
                        'batch': 1, 'slot': info['slot'], 'station': 'ПАВУК'
                    }

        pv_unplaced = pv_needed[~pv_needed['Name'].isin(board_feeder_map.keys())]
        pv_unplaced = pv_unplaced.merge(board_counts, on='Name', how='left')
        pv_unplaced['board_count'] = (
            pd.to_numeric(pv_unplaced['board_count'], errors='coerce')
            .fillna(0).astype(int)
        )
        pv_unplaced = pv_unplaced.sort_values(
            by=['board_count', 'Name'], ascending=[False, True]
        )

        pv_batch_usage = {}
        pv_batch_slots_used = {}

        def assign_pv(w, name):
            if tape_limits.get(w, 0) <= 0:
                return False
            slots_needed = 3 if w in (32, 44) else 2
            b = 1
            while True:
                if b not in pv_batch_usage:
                    pv_batch_usage[b] = {s: 0 for s in TAPE_SIZES}
                if b not in pv_batch_slots_used:
                    pv_batch_slots_used[b] = set()

                if pv_batch_usage[b].get(w, 0) < tape_limits.get(w, 0):
                    hist_info = global_feeder_map.get(name)
                    if hist_info and hist_info['station'] == 'ПАВУК':
                        base_slot_label = hist_info['slot'].split(' ')[0]
                        mh = re.match(r'([LR])(\d+)-([LR])(\d+)', base_slot_label)
                        if mh:
                            prefix = mh.group(1)
                            start_n, end_n = int(mh.group(2)), int(mh.group(4))
                            clusters = [f"{prefix}{i}" for i in range(start_n, end_n + 1)]
                            if len(clusters) == slots_needed and all(
                                c not in locked_pv_slots and
                                c not in pv_batch_slots_used[b]
                                for c in clusters
                            ):
                                assigned_cluster = f"{clusters[0]}-{clusters[-1]}"
                                for c in clusters:
                                    pv_batch_slots_used[b].add(c)
                                pv_batch_usage[b][w] = pv_batch_usage[b].get(w, 0) + 1
                                board_feeder_map[name] = {
                                    'batch': b,
                                    'slot': f'{assigned_cluster} ({w}мм)',
                                    'station': 'ПАВУК',
                                }
                                global_feeder_map[name] = dict(board_feeder_map[name])
                                return True

                    for prefix in ['L', 'R']:
                        for start_idx in range(1, 21 - slots_needed + 1):
                            cluster = [
                                f"{prefix}{i}"
                                for i in range(start_idx, start_idx + slots_needed)
                            ]
                            if all(
                                c not in locked_pv_slots and
                                c not in pv_batch_slots_used[b]
                                for c in cluster
                            ):
                                assigned_cluster = f"{cluster[0]}-{cluster[-1]}"
                                for c in cluster:
                                    pv_batch_slots_used[b].add(c)
                                pv_batch_usage[b][w] = pv_batch_usage[b].get(w, 0) + 1
                                board_feeder_map[name] = {
                                    'batch': b,
                                    'slot': f'{assigned_cluster} ({w}мм)',
                                    'station': 'ПАВУК',
                                }
                                global_feeder_map[name] = dict(board_feeder_map[name])
                                return True
                b += 1
                if b > 50:
                    return False

        pv_skipped_mm = set()
        for _, r in pv_unplaced.iterrows():
            if not assign_pv(r['mm'], r['Name']):
                pv_skipped_mm.add(r['mm'])

        if pv_skipped_mm:
            warnings.append(
                f"Павук: для компонентов {sorted(pv_skipped_mm)}мм нет мест/лимитов."
            )

    # ── Build setup / instr rows ──────────────────────────────────────────
    setup_rows = []
    instr_rows = []
    batch_comps = {}

    for _, r in df_board.iterrows():
        info = board_feeder_map.get(r['Name'], {'batch': '?', 'slot': '?', 'station': '?'})
        b_name = f"{info['station']} №{info['batch']}"

        batch_comps.setdefault(b_name, []).append(r['Name'])

        setup_rows.append({
            'station': info['station'],
            'batch': f"№ {info['batch']}",
            'slot': info['slot'],
            'name': r['Name'],
            'quantity': int(r['Quantity']),
        })
        instr_rows.append({
            'priority': f"{info['station']} (Батч {info['batch']})",
            'name': r['Name'],
            'slot': info['slot'],
            'quantity': int(r['Quantity']),
            'designators': r['Designator'],
        })

    # Sort setup rows
    if setup_rows:
        df_s = pd.DataFrame(setup_rows)
        qn = pd.to_numeric(
            df_s['batch'].str.replace(r'[^\d]', '', regex=True), errors='coerce'
        ).fillna(0).astype(int)
        ord_chip = df_s.apply(
            lambda row: cs_slot_order(row['slot']) if str(row['station']) == 'ЧИПШУТЕР' else -1,
            axis=1,
        )
        ord_pv = df_s.apply(
            lambda row: pv_cluster(row['slot']) if str(row['station']) == 'ПАВУК' else '',
            axis=1,
        )
        df_s = df_s.assign(
            __st=df_s['station'].astype(str),
            __qn=qn,
            __oc=pd.to_numeric(ord_chip, errors='coerce').fillna(9999).astype(int),
            __op=ord_pv.astype(str),
            __sl=df_s['slot'].map(lambda x: str(x) if x is not None else ''),
        )
        df_s = df_s.sort_values(
            by=['__st', '__qn', '__oc', '__op', '__sl']
        ).drop(columns=['__st', '__qn', '__oc', '__op', '__sl'])
        setup_rows = df_s.to_dict('records')

    # Sort instr rows
    if instr_rows:
        df_i = pd.DataFrame(instr_rows)
        bt = pd.to_numeric(
            df_i['priority'].str.extract(r'Батч\s*(\d+)', expand=False), errors='coerce'
        ).fillna(0).astype(int)
        st_order = df_i['priority'].str.contains('ПАВУК', na=False).astype(int)
        oc = df_i.apply(
            lambda row: cs_slot_order(row['slot']) if 'ЧИПШУТЕР' in str(row['priority']) else -1,
            axis=1,
        )
        op = df_i.apply(
            lambda row: pv_cluster(row['slot']) if 'ПАВУК' in str(row['priority']) else '',
            axis=1,
        )
        df_i = df_i.assign(
            __bt=bt,
            __st=st_order,
            __oc=pd.to_numeric(oc, errors='coerce').fillna(9999).astype(int),
            __op=op.astype(str),
            __sl=df_i['slot'].map(lambda x: str(x) if x is not None else ''),
        )
        df_i = df_i.sort_values(
            by=['__st', '__bt', '__oc', '__op', '__sl']
        ).drop(columns=['__bt', '__st', '__oc', '__op', '__sl'])
        instr_rows = df_i.to_dict('records')

    # Summary
    n_cs = sum(
        1 for nm in unique_parts_current
        if board_feeder_map.get(nm, {}).get('station') == 'ЧИПШУТЕР'
    )
    max_cs_b = max(
        (int(board_feeder_map[nm]['batch']) for nm in unique_parts_current
         if board_feeder_map.get(nm, {}).get('station') == 'ЧИПШУТЕР'),
        default=0,
    )
    cs_batches = [b for b in batch_comps if "ЧИПШУТЕР" in b]
    pv_batches = [b for b in batch_comps if "ПАВУК" in b]

    # Next board recommendations
    other_boards = []
    for other_board in all_data['Sheet'].unique():
        if other_board == board:
            continue
        other_data = all_data[all_data['Sheet'] == other_board]
        unique_other = set(other_data['Name'].unique())
        shared = unique_parts_current.intersection(unique_other)
        other_boards.append({'board': other_board, 'shared': len(shared)})
    other_boards.sort(key=lambda x: x['shared'], reverse=True)

    return {
        'setup_rows': setup_rows,
        'instr_rows': instr_rows,
        'batch_comps': batch_comps,
        'feeder_map': board_feeder_map,
        'global_feeder_map': global_feeder_map,
        'warnings': warnings,
        'summary': {
            'total_parts': int(df_board['Quantity'].sum()),
            'unique_parts': len(df_board),
            'chipshooter_tapes': n_cs,
            'chipshooter_batches': max_cs_b,
            'cs_batch_count': len(cs_batches),
            'pv_batch_count': len(pv_batches),
        },
        'next_boards': other_boards[:5],
    }


def find_best_next_board(all_data, current_board, instr_completed):
    """Return name of the board that shares the most parts with current_board."""
    current_data = all_data[all_data['Sheet'] == current_board]
    current_parts = set(current_data['Name'].unique())

    best_board = None
    best_count = -1
    for other_board in all_data['Sheet'].unique():
        if other_board == current_board:
            continue
        if is_board_completed(all_data, other_board, instr_completed):
            continue
        other_data = all_data[all_data['Sheet'] == other_board]
        shared = len(current_parts.intersection(set(other_data['Name'].unique())))
        if shared > best_count:
            best_count = shared
            best_board = other_board

    return best_board, best_count
