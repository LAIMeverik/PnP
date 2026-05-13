"""Warehouse CRUD API blueprint."""
import io
from flask import Blueprint, jsonify, request

from web.db import (
    list_warehouse, add_warehouse_item, update_warehouse_quantity,
    delete_warehouse_item, replace_warehouse,
)
from web.core.warehouse_engine import (
    load_warehouse_from_records, upsert_warehouse,
    warehouse_to_records, load_from_excel_bytes, TAPE_SIZES, DEFAULT_TAPE_WIDTH,
)

bp = Blueprint('warehouse', __name__, url_prefix='/api/warehouse')


@bp.get('/')
def get_warehouse():
    return jsonify(list_warehouse())


@bp.post('/')
def add_item():
    d = request.json or {}
    name = str(d.get('name', '')).strip()
    if not name:
        return jsonify({'error': 'name required'}), 400

    position_num = str(d.get('position_num', '')).strip()
    tape_width = int(d.get('tape_width', DEFAULT_TAPE_WIDTH))
    quantity = int(d.get('quantity', 0))
    if quantity <= 0:
        return jsonify({'error': 'quantity must be > 0'}), 400

    # Build coil_id like the desktop app does
    existing = list_warehouse()
    same_count = sum(1 for r in existing if r['name'] == name)
    coil_id = f"{name}-{same_count + 1}"

    item_id = add_warehouse_item(position_num, name, tape_width, coil_id, quantity)
    return jsonify({'id': item_id, 'status': 'ok'})


@bp.patch('/<int:item_id>')
def patch_item(item_id):
    d = request.json or {}
    qty = d.get('quantity')
    if qty is None:
        return jsonify({'error': 'quantity required'}), 400
    update_warehouse_quantity(item_id, int(qty))
    return jsonify({'status': 'ok'})


@bp.delete('/<int:item_id>')
def remove_item(item_id):
    delete_warehouse_item(item_id)
    return jsonify({'status': 'ok'})


@bp.post('/upload')
def upload_warehouse_excel():
    """Upload an Excel file and merge into the warehouse."""
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400

    raw_df, err = load_from_excel_bytes(f.read())
    if err:
        return jsonify({'error': err}), 400

    # Fetch current warehouse as df
    current = list_warehouse()
    df = load_warehouse_from_records([
        {'Номер': r['position_num'], 'Название': r['name'],
         'ШиринаЛенты': r['tape_width'], 'Катушка': r['coil_id'],
         'Остаток': r['quantity']}
        for r in current
    ])

    added = 0
    for _, row in raw_df.iterrows():
        df = upsert_warehouse(df, str(row['Номер']), str(row['Название']),
                              int(row['ШиринаЛенты']), int(row['Остаток']))
        added += 1

    replace_warehouse(warehouse_to_records(df))
    return jsonify({'status': 'ok', 'added': added, 'total': len(list_warehouse())})
