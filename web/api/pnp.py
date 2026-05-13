"""
PnP calculation and next-batch API blueprint.
All heavy logic lives in web/core/pnp_engine.py.
"""
import json
import pandas as pd
from flask import Blueprint, jsonify, request

from web.db import (
    get_project,
    get_feeder_map, save_feeder_map,
    get_setup_completed, get_instr_completed,
    toggle_setup, toggle_instr,
    get_tape_limits, set_tape_limit,
    get_batch_stock_deducted, mark_batch_deducted,
    consume_warehouse, list_warehouse,
)
from web.core.pnp_engine import (
    calculate_board, find_best_next_board, is_board_completed, CS_BANK_SLOTS,
)
from web.core.warehouse_engine import consume_from_first_coil, load_warehouse_from_records

bp = Blueprint('pnp', __name__, url_prefix='/api/pnp')


def _load_all_data(project_id):
    """Load BOM records from DB and return a DataFrame."""
    proj = get_project(project_id)
    if not proj or not proj.get('bom_data'):
        return None, 'Проект не найден'
    try:
        records = json.loads(proj['bom_data'])
        df = pd.DataFrame(records)
        if 'Sheet' not in df.columns:
            return None, 'В данных BOM нет колонки Sheet'
        return df, None
    except Exception as exc:
        return None, str(exc)


# ── Tape limits ────────────────────────────────────────────────────────────

@bp.get('/tape_limits')
def get_limits():
    return jsonify(get_tape_limits())


@bp.post('/tape_limits')
def update_limits():
    """Body: {8: 0, 12: 5, ...}"""
    data = request.json or {}
    for size, count in data.items():
        set_tape_limit(int(size), int(count))
    return jsonify({'status': 'ok'})


# ── Calculate ──────────────────────────────────────────────────────────────

@bp.post('/calculate/<int:project_id>')
def calculate(project_id):
    """Calculate feeder assignments for the given board."""
    body = request.json or {}
    board = body.get('board', '')
    if not board:
        return jsonify({'error': 'board required'}), 400

    all_data, err = _load_all_data(project_id)
    if err:
        return jsonify({'error': err}), 404

    global_feeder_map = get_feeder_map(project_id)
    setup_completed = get_setup_completed(project_id)
    tape_limits = get_tape_limits()

    auto_assign = body.get('auto_assign', True)
    result = calculate_board(
        all_data, board, global_feeder_map, setup_completed,
        tape_limits, auto_assign=auto_assign,
    )
    if 'error' in result:
        return jsonify(result), 400

    # Persist updated feeder map
    save_feeder_map(project_id, result['global_feeder_map'])

    # Merge board feeder map back (it's already inside global)
    return jsonify(result)


# ── Boards ─────────────────────────────────────────────────────────────────

@bp.get('/boards/<int:project_id>')
def list_boards(project_id):
    all_data, err = _load_all_data(project_id)
    if err:
        return jsonify({'error': err}), 404
    instr_completed = get_instr_completed(project_id)
    boards = []
    for sheet in all_data['Sheet'].unique():
        completed = is_board_completed(all_data, sheet, instr_completed)
        boards.append({'name': sheet, 'completed': completed})
    return jsonify(boards)


# ── Progress ───────────────────────────────────────────────────────────────

@bp.get('/progress/<int:project_id>')
def get_progress(project_id):
    return jsonify({
        'setup': get_setup_completed(project_id),
        'instr': get_instr_completed(project_id),
        'batch_stock_deducted': get_batch_stock_deducted(project_id),
    })


@bp.post('/progress/<int:project_id>/toggle_setup')
def post_toggle_setup(project_id):
    body = request.json or {}
    board = body.get('board')
    comps = body.get('comps', [])
    mark_done = bool(body.get('mark_done', True))
    if not board or not comps:
        return jsonify({'error': 'board and comps required'}), 400
    toggle_setup(project_id, board, comps, mark_done)
    return jsonify({'status': 'ok'})


@bp.post('/progress/<int:project_id>/toggle_instr')
def post_toggle_instr(project_id):
    body = request.json or {}
    board = body.get('board')
    comps = body.get('comps', [])
    mark_done = bool(body.get('mark_done', True))
    if not board or not comps:
        return jsonify({'error': 'board and comps required'}), 400
    toggle_instr(project_id, board, comps, mark_done)
    return jsonify({'status': 'ok'})


# ── Next batch / board ─────────────────────────────────────────────────────

@bp.post('/next_batch/<int:project_id>')
def next_batch(project_id):
    """
    Auto-select the best next board, strip components no longer needed, 
    persist and return the updated board name.
    """
    body = request.json or {}
    current_board = body.get('board', '')
    if not current_board:
        return jsonify({'error': 'board required'}), 400

    all_data, err = _load_all_data(project_id)
    if err:
        return jsonify({'error': err}), 404

    instr_completed = get_instr_completed(project_id)
    next_board, shared_count = find_best_next_board(all_data, current_board, instr_completed)

    if not next_board:
        return jsonify({'message': 'Нет незавершённых плат для перехода.'})

    # Remove components from setup that are not needed for the next board
    global_feeder_map = get_feeder_map(project_id)
    setup_completed = get_setup_completed(project_id)

    global_setup = set()
    for comps in setup_completed.values():
        global_setup.update(comps)

    next_all = set(all_data[all_data['Sheet'] == next_board]['Name'].unique())
    next_done = set(instr_completed.get(next_board, []))
    next_needed_active = next_all - next_done

    removed = []
    for comp in list(global_setup):
        if comp not in next_needed_active:
            # remove from setup_completed across all boards
            for board_name, comps in setup_completed.items():
                if comp in comps:
                    toggle_setup(project_id, board_name, [comp], mark_done=False)
            removed.append(comp)

    # Recalculate for next board
    setup_completed = get_setup_completed(project_id)
    tape_limits = get_tape_limits()
    result = calculate_board(
        all_data, next_board, global_feeder_map, setup_completed,
        tape_limits, auto_assign=True,
    )
    if 'error' not in result:
        save_feeder_map(project_id, result['global_feeder_map'])

    return jsonify({
        'next_board': next_board,
        'shared_components': shared_count,
        'removed_from_machine': removed,
        'board_data': result if 'error' not in result else None,
        'error': result.get('error'),
    })


# ── Stock deduction ────────────────────────────────────────────────────────

@bp.post('/deduct_batch/<int:project_id>')
def deduct_batch(project_id):
    """Subtract component quantities for a batch from the warehouse."""
    body = request.json or {}
    board = body.get('board', '')
    batch_no = int(body.get('batch_no', 1))

    if not board:
        return jsonify({'error': 'board required'}), 400

    already = get_batch_stock_deducted(project_id)
    if batch_no in already.get(board, []):
        return jsonify({'status': 'already_done'})

    all_data, err = _load_all_data(project_id)
    if err:
        return jsonify({'error': err}), 404

    feeder_map = get_feeder_map(project_id)
    board_data = all_data[all_data['Sheet'] == board]
    qty_map = {
        str(r['Name']): int(r['Quantity'])
        for _, r in board_data.groupby('Name').agg({'Quantity': 'sum'}).reset_index().iterrows()
    }

    shortages = []
    for comp_name, qty in qty_map.items():
        info = feeder_map.get(comp_name)
        if not info:
            continue
        try:
            b = int(info.get('batch', 1))
        except (TypeError, ValueError):
            b = 1
        if b != batch_no:
            continue
        left = consume_warehouse(comp_name, qty)
        if left > 0:
            shortages.append({'comp': comp_name, 'shortage': left})

    mark_batch_deducted(project_id, board, batch_no)
    return jsonify({'status': 'ok', 'shortages': shortages})


# ── Visual map data ────────────────────────────────────────────────────────

@bp.get('/visual_map/<int:project_id>')
def visual_map(project_id):
    """Return slot assignments for the visual machine map."""
    all_data, err = _load_all_data(project_id)
    if err:
        return jsonify({'error': err}), 404

    feeder_map = get_feeder_map(project_id)
    setup_completed = get_setup_completed(project_id)
    instr_completed = get_instr_completed(project_id)

    board = request.args.get('board', '')
    station = request.args.get('station', 'ЧИПШУТЕР')

    global_setup: set = set()
    for comps in setup_completed.values():
        global_setup.update(comps)

    future_needed: set = set()
    if all_data is not None and board:
        for b_name in all_data['Sheet'].unique():
            if b_name != board and not is_board_completed(all_data, b_name, instr_completed):
                future_needed.update(all_data[all_data['Sheet'] == b_name]['Name'].unique())

    curr_all: set = set()
    curr_done: set = set()
    if all_data is not None and board:
        curr_all = set(all_data[all_data['Sheet'] == board]['Name'].unique())
        curr_done = set(instr_completed.get(board, []))
    curr_needed_active = curr_all - curr_done

    slots = []
    components_in_machine = [
        (name, info)
        for name, info in feeder_map.items()
        if info['station'] == station and name in global_setup
    ]

    if station == 'ЧИПШУТЕР':
        slot_map = {info['slot']: name for name, info in components_in_machine}
        for slot_id in CS_BANK_SLOTS:
            comp = slot_map.get(slot_id, '')
            status = _comp_status(comp, global_setup, curr_needed_active, future_needed)
            slots.append({'slot': slot_id, 'comp': comp, 'status': status})
    else:
        for name, info in sorted(components_in_machine, key=lambda x: x[1]['slot']):
            status = _comp_status(name, global_setup, curr_needed_active, future_needed)
            slots.append({'slot': info['slot'], 'comp': name, 'status': status})

    return jsonify({'slots': slots, 'station': station})


def _comp_status(comp, global_setup, curr_needed_active, future_needed):
    if not comp or comp not in global_setup:
        return 'empty'
    if comp in curr_needed_active:
        return 'active'
    if comp in future_needed:
        return 'reserved'
    return 'removable'
