"""
Pure Python warehouse logic — no Qt dependencies.
Ported from core/warehouse_manager.py (WarehouseMixin).
"""
import re
import pandas as pd

TAPE_SIZES = [8, 12, 16, 24, 32, 44]
DEFAULT_TAPE_WIDTH = 8
COLS = ["Номер", "Название", "ШиринаЛенты", "Катушка", "Остаток"]


def load_warehouse_from_records(records):
    """Build a clean DataFrame from a list of dict records."""
    df = pd.DataFrame(records) if records else pd.DataFrame()
    for c in COLS:
        if c not in df.columns:
            df[c] = "" if c in ("Номер", "Название", "Катушка") else 0
    if not df.empty:
        df = df[COLS]
        df["Название"] = df["Название"].astype(str).str.strip()
        df["Номер"] = df["Номер"].astype(str)
        df["Катушка"] = df["Катушка"].astype(str)
        df["ШиринаЛенты"] = (
            pd.to_numeric(df["ШиринаЛенты"], errors="coerce")
            .fillna(DEFAULT_TAPE_WIDTH).astype(int)
        )
        df["Остаток"] = pd.to_numeric(df["Остаток"], errors="coerce").fillna(0).astype(int)
        df = df[df["Название"] != ""]
        df = df[df["Остаток"] > 0]
        df = df.reset_index(drop=True)
    else:
        df = pd.DataFrame(columns=COLS)
    return df


def upsert_warehouse(df, num, name, width, qty):
    """Add a new coil entry for *name*."""
    if qty <= 0:
        return df
    same = df[df["Название"] == name] if not df.empty else pd.DataFrame()
    next_idx = len(same) + 1
    coil_id = f"{name}-{next_idx}"
    new_row = pd.DataFrame([{
        "Номер": str(num),
        "Название": str(name),
        "ШиринаЛенты": int(width),
        "Катушка": coil_id,
        "Остаток": int(qty),
    }])
    return pd.concat([df, new_row], ignore_index=True)


def consume_from_first_coil(df, comp_name, qty_to_subtract):
    """
    Subtract *qty_to_subtract* from the first coil of *comp_name*.
    Returns (updated_df, leftover_qty).
    """
    if qty_to_subtract <= 0 or df.empty:
        return df, 0
    comp_rows = df[df["Название"] == comp_name]
    if comp_rows.empty:
        return df, int(qty_to_subtract)
    first_idx = comp_rows.index[0]
    current_qty = int(df.at[first_idx, "Остаток"])
    consume = min(current_qty, int(qty_to_subtract))
    df.at[first_idx, "Остаток"] = current_qty - consume
    left = int(qty_to_subtract) - consume
    df = df[df["Остаток"] > 0].reset_index(drop=True)
    return df, left


def sort_warehouse(df):
    """Return a sorted copy: by name, position number, coil index."""
    if df.empty:
        return df
    df_sorted = df.copy()
    df_sorted["Остаток"] = pd.to_numeric(df_sorted["Остаток"], errors="coerce").fillna(0).astype(int)

    def _coil_num(value):
        m = re.search(r"(\d+)$", str(value))
        return int(m.group(1)) if m else 999999

    df_sorted["__coil_num"] = df_sorted["Катушка"].map(_coil_num)
    df_sorted["__coil_txt"] = df_sorted["Катушка"].astype(str)
    df_sorted = df_sorted[df_sorted["Остаток"] > 0]
    df_sorted = df_sorted.sort_values(
        by=["Название", "Номер", "__coil_num", "__coil_txt"], na_position='last'
    )
    return df_sorted.drop(columns=["__coil_num", "__coil_txt"]).reset_index(drop=True)


def warehouse_to_records(df):
    """Convert DataFrame to list of plain dicts (for JSON / DB serialisation)."""
    if df is None or df.empty:
        return []
    clean = sort_warehouse(df)
    return clean.to_dict('records')


def load_from_excel_bytes(file_bytes, default_width=DEFAULT_TAPE_WIDTH):
    """
    Parse a warehouse Excel file from raw bytes.
    Expected columns: Номер, Название, [ШиринаЛенты,] Остаток
    Returns (df, error_string_or_None).
    """
    import io
    try:
        raw = pd.read_excel(io.BytesIO(file_bytes))
        if len(raw.columns) >= 4:
            raw = raw.iloc[:, :4]
            raw.columns = ["Номер", "Название", "ШиринаЛенты", "Остаток"]
        elif len(raw.columns) >= 3:
            raw = raw.iloc[:, :3]
            raw.columns = ["Номер", "Название", "Остаток"]
            raw["ШиринаЛенты"] = default_width
        else:
            return None, "Excel должен содержать минимум 3 колонки."

        raw["Остаток"] = pd.to_numeric(raw["Остаток"], errors='coerce').fillna(0).astype(int)
        raw["ШиринаЛенты"] = (
            pd.to_numeric(raw["ШиринаЛенты"], errors='coerce').fillna(default_width).astype(int)
        )
        return raw, None
    except Exception as exc:
        return None, str(exc)
