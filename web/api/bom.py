"""BOM upload and parsing blueprint."""
import io
import json

import pandas as pd
from flask import Blueprint, jsonify, request

from web.db import create_project, update_project_bom, list_projects

bp = Blueprint('bom', __name__, url_prefix='/api/bom')


@bp.post('/upload')
def upload_bom():
    """
    Upload an Excel BOM file.
    Creates a new project (or reuses one if name matches) and returns project info.
    """
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400

    filename = f.filename or 'project'
    project_name = filename.rsplit('.', 1)[0]

    try:
        xls = pd.ExcelFile(io.BytesIO(f.read()))
        sheets = xls.sheet_names
        frames = []
        for s in sheets:
            df = pd.read_excel(xls, s)
            df.columns = df.columns.str.strip()
            df['Sheet'] = s
            frames.append(df)
        all_data = pd.concat(frames, ignore_index=True)
    except Exception as exc:
        return jsonify({'error': f'Ошибка чтения Excel: {exc}'}), 400

    required = {'Name', 'Quantity', 'Designator', 'Тип ленты'}
    missing = required - set(all_data.columns)
    if missing:
        return jsonify({
            'error': f"В Excel не хватает колонок: {', '.join(sorted(missing))}",
            'available_columns': list(all_data.columns),
        }), 400

    # Serialise to plain records (drop NaN)
    bom_records = json.loads(all_data.to_json(orient='records', force_ascii=False))

    project_id = create_project(project_name, bom_records)

    return jsonify({
        'project_id': project_id,
        'project_name': project_name,
        'sheets': sheets,
        'columns': list(all_data.columns),
        'total_rows': len(all_data),
    })


@bp.get('/projects')
def get_projects():
    return jsonify(list_projects())
