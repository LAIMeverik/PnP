"""SQLite database layer for the PnP web application."""
import json
import sqlite3
import os

DB_PATH = os.environ.get('PNP_DB_PATH', os.path.join(os.path.dirname(__file__), 'pnp.db'))


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    """Create all tables and seed default tape limits if missing."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            bom_data    TEXT,
            created_at  TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS warehouse (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            position_num TEXT    NOT NULL DEFAULT '',
            name         TEXT    NOT NULL,
            tape_width   INTEGER NOT NULL DEFAULT 8,
            coil_id      TEXT    NOT NULL DEFAULT '',
            quantity     INTEGER NOT NULL DEFAULT 0,
            updated_at   TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS tape_limits (
            tape_size   INTEGER PRIMARY KEY,
            slots_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS feeder_map (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL,
            comp_name   TEXT    NOT NULL,
            station     TEXT    NOT NULL DEFAULT '',
            slot        TEXT    NOT NULL DEFAULT '',
            batch_no    INTEGER NOT NULL DEFAULT 1,
            UNIQUE(project_id, comp_name),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS setup_completed (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL,
            board_name  TEXT    NOT NULL,
            comp_name   TEXT    NOT NULL,
            UNIQUE(project_id, board_name, comp_name),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS instr_completed (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL,
            board_name  TEXT    NOT NULL,
            comp_name   TEXT    NOT NULL,
            UNIQUE(project_id, board_name, comp_name),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS batch_stock_deducted (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL,
            board_name  TEXT    NOT NULL,
            batch_no    INTEGER NOT NULL,
            UNIQUE(project_id, board_name, batch_no),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        """)

        # Seed default tape limits
        for size in [8, 12, 16, 24, 32, 44]:
            conn.execute(
                "INSERT OR IGNORE INTO tape_limits (tape_size, slots_count) VALUES (?, ?)",
                (size, 0 if size == 8 else 10),
            )
        conn.commit()


# ── Projects ──────────────────────────────────────────────────────────────

def list_projects():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, created_at FROM projects ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_project(project_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, bom_data, created_at FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
    return dict(row) if row else None


def create_project(name, bom_data):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO projects (name, bom_data) VALUES (?, ?)",
            (name, json.dumps(bom_data, ensure_ascii=False)),
        )
        conn.commit()
    return cur.lastrowid


def update_project_bom(project_id, bom_data):
    with get_conn() as conn:
        conn.execute(
            "UPDATE projects SET bom_data = ? WHERE id = ?",
            (json.dumps(bom_data, ensure_ascii=False), project_id),
        )
        conn.commit()


def delete_project(project_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()


# ── Warehouse ──────────────────────────────────────────────────────────────

def list_warehouse():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, position_num, name, tape_width, coil_id, quantity "
            "FROM warehouse WHERE quantity > 0 ORDER BY name, position_num, coil_id"
        ).fetchall()
    return [dict(r) for r in rows]


def add_warehouse_item(position_num, name, tape_width, coil_id, quantity):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO warehouse (position_num, name, tape_width, coil_id, quantity) "
            "VALUES (?, ?, ?, ?, ?)",
            (position_num, name, tape_width, coil_id, quantity),
        )
        conn.commit()
    return cur.lastrowid


def update_warehouse_quantity(item_id, quantity):
    with get_conn() as conn:
        conn.execute("UPDATE warehouse SET quantity = ? WHERE id = ?", (quantity, item_id))
        conn.commit()


def delete_warehouse_item(item_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM warehouse WHERE id = ?", (item_id,))
        conn.commit()


def get_warehouse_by_name(name):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, position_num, name, tape_width, coil_id, quantity "
            "FROM warehouse WHERE name = ? AND quantity > 0 ORDER BY coil_id",
            (name,),
        ).fetchall()
    return [dict(r) for r in rows]


def consume_warehouse(name, qty_to_subtract):
    """
    Subtract qty_to_subtract from the first coil of name.
    Returns leftover (0 if successful).
    """
    rows = get_warehouse_by_name(name)
    if not rows:
        return int(qty_to_subtract)
    remaining = int(qty_to_subtract)
    with get_conn() as conn:
        for row in rows:
            if remaining <= 0:
                break
            consume = min(int(row['quantity']), remaining)
            new_qty = int(row['quantity']) - consume
            remaining -= consume
            if new_qty <= 0:
                conn.execute("DELETE FROM warehouse WHERE id = ?", (row['id'],))
            else:
                conn.execute(
                    "UPDATE warehouse SET quantity = ? WHERE id = ?", (new_qty, row['id'])
                )
        conn.commit()
    return remaining


def replace_warehouse(records):
    """Replace entire warehouse with a list of dicts."""
    with get_conn() as conn:
        conn.execute("DELETE FROM warehouse")
        for r in records:
            conn.execute(
                "INSERT INTO warehouse (position_num, name, tape_width, coil_id, quantity) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    str(r.get('Номер', r.get('position_num', ''))),
                    str(r.get('Название', r.get('name', ''))),
                    int(r.get('ШиринаЛенты', r.get('tape_width', 8))),
                    str(r.get('Катушка', r.get('coil_id', ''))),
                    int(r.get('Остаток', r.get('quantity', 0))),
                ),
            )
        conn.commit()


# ── Tape limits ────────────────────────────────────────────────────────────

def get_tape_limits():
    with get_conn() as conn:
        rows = conn.execute("SELECT tape_size, slots_count FROM tape_limits").fetchall()
    return {int(r['tape_size']): int(r['slots_count']) for r in rows}


def set_tape_limit(tape_size, slots_count):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tape_limits (tape_size, slots_count) VALUES (?, ?) "
            "ON CONFLICT(tape_size) DO UPDATE SET slots_count = excluded.slots_count",
            (tape_size, slots_count),
        )
        conn.commit()


# ── Feeder map ─────────────────────────────────────────────────────────────

def get_feeder_map(project_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT comp_name, station, slot, batch_no "
            "FROM feeder_map WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    return {r['comp_name']: {'station': r['station'], 'slot': r['slot'], 'batch': r['batch_no']}
            for r in rows}


def save_feeder_map(project_id, feeder_map):
    with get_conn() as conn:
        conn.execute("DELETE FROM feeder_map WHERE project_id = ?", (project_id,))
        for comp_name, info in feeder_map.items():
            conn.execute(
                "INSERT INTO feeder_map (project_id, comp_name, station, slot, batch_no) "
                "VALUES (?, ?, ?, ?, ?)",
                (project_id, comp_name, info.get('station', ''),
                 info.get('slot', ''), int(info.get('batch', 1))),
            )
        conn.commit()


# ── Progress ───────────────────────────────────────────────────────────────

def get_setup_completed(project_id):
    """Return {board_name: [comp_name, ...]}"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT board_name, comp_name FROM setup_completed WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    result = {}
    for r in rows:
        result.setdefault(r['board_name'], []).append(r['comp_name'])
    return result


def get_instr_completed(project_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT board_name, comp_name FROM instr_completed WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    result = {}
    for r in rows:
        result.setdefault(r['board_name'], []).append(r['comp_name'])
    return result


def toggle_setup(project_id, board_name, comp_names, mark_done):
    with get_conn() as conn:
        for comp in comp_names:
            if mark_done:
                conn.execute(
                    "INSERT OR IGNORE INTO setup_completed (project_id, board_name, comp_name) "
                    "VALUES (?, ?, ?)",
                    (project_id, board_name, comp),
                )
            else:
                conn.execute(
                    "DELETE FROM setup_completed WHERE project_id=? AND board_name=? AND comp_name=?",
                    (project_id, board_name, comp),
                )
        conn.commit()


def toggle_instr(project_id, board_name, comp_names, mark_done):
    with get_conn() as conn:
        for comp in comp_names:
            if mark_done:
                conn.execute(
                    "INSERT OR IGNORE INTO instr_completed (project_id, board_name, comp_name) "
                    "VALUES (?, ?, ?)",
                    (project_id, board_name, comp),
                )
            else:
                conn.execute(
                    "DELETE FROM instr_completed WHERE project_id=? AND board_name=? AND comp_name=?",
                    (project_id, board_name, comp),
                )
        conn.commit()


def get_batch_stock_deducted(project_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT board_name, batch_no FROM batch_stock_deducted WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    result = {}
    for r in rows:
        result.setdefault(r['board_name'], []).append(int(r['batch_no']))
    return result


def mark_batch_deducted(project_id, board_name, batch_no):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO batch_stock_deducted (project_id, board_name, batch_no) "
            "VALUES (?, ?, ?)",
            (project_id, board_name, batch_no),
        )
        conn.commit()
