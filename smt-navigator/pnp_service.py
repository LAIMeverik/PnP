import io
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

import pandas as pd


class SMTServiceError(Exception):
    pass


class SMTService:
    CS_BANK_SLOTS = [f"L{i}" for i in range(5, 30)] + [f"R{i}" for i in range(5, 30)]
    DEFAULT_TAPE_WIDTH = 8

    def __init__(self):
        self.tape_sizes = [8, 12, 16, 24, 32, 44]
        self.root_dir = Path(__file__).resolve().parent.parent
        self.data_dir = self.root_dir / "smt_web" / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.progress_file = self.root_dir / "smt_progress.json"
        self.warehouse_file = self.root_dir / "warehouse.json"
        self.warehouse_db_file = self.root_dir / "warehouse.db"
        self.bom_file = self.data_dir / "last_bom.xlsx"
        self._lock = RLock()

        self.all_data: Optional[pd.DataFrame] = None
        self.boards: List[str] = []
        self.current_board: str = ""
        self.global_feeder_map: Dict[str, Dict[str, Any]] = {}
        self.feeder_map: Dict[str, Dict[str, Any]] = {}
        self.setup_completed: Dict[str, List[str]] = {}
        self.instr_completed: Dict[str, List[str]] = {}
        self.batch_stock_deducted: Dict[str, List[int]] = {}
        self.batch_comps: Dict[str, List[str]] = {}
        self.pending_remove_components: List[str] = []
        self.last_prefill_components: List[str] = []
        self.chip_feeder_limit: int = len(self.CS_BANK_SLOTS)
        self.board_multiplier: int = 1
        self.tape_limits: Dict[int, int] = {8: 0, 12: 10, 16: 10, 24: 10, 32: 10, 44: 10}
        self.log_console: List[str] = []
        self.summary: Dict[str, Any] = {
            "status": "Ожидание загрузки BOM",
            "total_parts": 0,
            "unique_parts": 0,
            "feeders_used": "",
            "next_board": "",
            "recharge_warning": "",
            "shortages": [],
            "board_multiplier": 1,
            "pending_remove": 0,
        }
        self.table_setup: List[Dict[str, Any]] = []
        self.table_instr: List[Dict[str, Any]] = []
        self._init_warehouse_db()
        self.warehouse_data = self._load_warehouse_data([])

        progress_data = self._load_progress()
        self.setup_completed = progress_data.get("setup", {})
        self.instr_completed = progress_data.get("instr", {})
        self.global_feeder_map = progress_data.get("global_feeder_map") or {}
        self.batch_stock_deducted = {
            str(k): [int(x) for x in v if isinstance(x, (int, str)) and str(x).isdigit()]
            for k, v in (progress_data.get("batch_stock_deducted", {}) or {}).items()
        }
        saved_chip_limit = progress_data.get("chip_feeder_limit", len(self.CS_BANK_SLOTS))
        try:
            self.chip_feeder_limit = max(1, min(len(self.CS_BANK_SLOTS), int(saved_chip_limit)))
        except (TypeError, ValueError):
            self.chip_feeder_limit = len(self.CS_BANK_SLOTS)
        saved_board_multiplier = progress_data.get("board_multiplier", 1)
        try:
            self.board_multiplier = max(1, int(saved_board_multiplier))
        except (TypeError, ValueError):
            self.board_multiplier = 1
        saved_tape_limits = progress_data.get("tape_limits", {}) or {}
        for size in self.tape_sizes:
            val = saved_tape_limits.get(str(size), self.tape_limits[size])
            try:
                self.tape_limits[size] = max(0, int(val))
            except (TypeError, ValueError):
                pass

        saved_board = str(progress_data.get("current_board", "") or "")
        if self.bom_file.exists():
            self._load_bom_from_file(self.bom_file)
            if self.boards:
                self.current_board = saved_board if saved_board in self.boards else self.boards[0]
                self.calculate_global()

    def _log(self, message: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_console.append(f"[{ts}] {message}")
        self.log_console = self.log_console[-200:]

    @staticmethod
    def _cs_slot_order(slot: Any) -> int:
        m = re.match(r"^\s*([LR])\s*(\d+)\s*$", str(slot).strip(), re.I)
        if not m:
            return 9999
        side, n = m.group(1).upper(), int(m.group(2))
        if side == "L" and 5 <= n <= 29:
            return n - 5
        if side == "R" and 5 <= n <= 29:
            return 25 + (n - 5)
        return 8000 + n

    @staticmethod
    def _pv_cluster(full_slot: Any) -> str:
        return str(full_slot).split(" ")[0].strip().upper()

    def _parse_tape_width(self, value: Any) -> int:
        m = re.search(r"(\d+)", str(value))
        w = int(m.group(1)) if m else self.DEFAULT_TAPE_WIDTH
        return next((s for s in self.tape_sizes if w <= s), 44)

    def _save_progress(self):
        payload = {
            "setup": self.setup_completed,
            "instr": self.instr_completed,
            "global_feeder_map": self.global_feeder_map,
            "tape_limits": {str(k): int(v) for k, v in self.tape_limits.items()},
            "batch_stock_deducted": self.batch_stock_deducted,
            "current_board": self.current_board,
            "chip_feeder_limit": int(self.chip_feeder_limit),
            "board_multiplier": int(self.board_multiplier),
        }
        self.progress_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_progress(self) -> Dict[str, Any]:
        if not self.progress_file.exists():
            return {"setup": {}, "instr": {}, "batch_stock_deducted": {}}
        try:
            data = json.loads(self.progress_file.read_text(encoding="utf-8"))
            if "setup" not in data:
                return {"setup": data, "instr": {}, "batch_stock_deducted": {}}
            return data
        except Exception:
            return {"setup": {}, "instr": {}, "batch_stock_deducted": {}}

    def _init_warehouse_db(self):
        with sqlite3.connect(self.warehouse_db_file) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS warehouse (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    number TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    tape_width INTEGER NOT NULL,
                    coil TEXT NOT NULL,
                    qty INTEGER NOT NULL
                )
                """
            )
            conn.commit()

    def _load_warehouse_data(self, legacy_records: Optional[List[Dict[str, Any]]] = None) -> pd.DataFrame:
        cols = ["Номер", "Название", "ШиринаЛенты", "Катушка", "Остаток"]
        with sqlite3.connect(self.warehouse_db_file) as conn:
            db_df = pd.read_sql_query(
                "SELECT number as 'Номер', name as 'Название', tape_width as 'ШиринаЛенты', coil as 'Катушка', qty as 'Остаток' FROM warehouse",
                conn,
            )
        if db_df.empty and self.warehouse_file.exists():
            try:
                records = json.loads(self.warehouse_file.read_text(encoding="utf-8")) or []
                db_df = pd.DataFrame(records)
            except Exception:
                db_df = pd.DataFrame()
        elif db_df.empty and legacy_records:
            migrated = []
            for row in legacy_records:
                migrated.append(
                    {
                        "Номер": row.get("Номер", ""),
                        "Название": row.get("Название", ""),
                        "ШиринаЛенты": self.DEFAULT_TAPE_WIDTH,
                        "Катушка": "1",
                        "Остаток": row.get("Количество", 0),
                    }
                )
            db_df = pd.DataFrame(migrated)

        df = db_df.copy()
        for c in cols:
            if c not in df.columns:
                df[c] = "" if c in ("Номер", "Название", "Катушка") else 0
        df = df[cols]
        if df.empty:
            return pd.DataFrame(columns=cols)

        df["Название"] = df["Название"].astype(str).str.strip()
        df["Номер"] = df["Номер"].astype(str)
        df["Катушка"] = df["Катушка"].astype(str)
        df["ШиринаЛенты"] = pd.to_numeric(df["ШиринаЛенты"], errors="coerce").fillna(self.DEFAULT_TAPE_WIDTH).astype(int)
        df["Остаток"] = pd.to_numeric(df["Остаток"], errors="coerce").fillna(0).astype(int)
        df = df[(df["Название"] != "") & (df["Остаток"] > 0)].reset_index(drop=True)
        self._save_warehouse_data_from_df(df)
        if self.warehouse_file.exists():
            try:
                self.warehouse_file.unlink()
            except OSError:
                pass
        return df

    def _save_warehouse_data(self):
        clean = pd.DataFrame(columns=["Номер", "Название", "ШиринаЛенты", "Катушка", "Остаток"])
        if self.warehouse_data is not None and not self.warehouse_data.empty:
            clean = self.warehouse_data.copy()
            clean["Остаток"] = pd.to_numeric(clean["Остаток"], errors="coerce").fillna(0).astype(int)
            clean["ШиринаЛенты"] = pd.to_numeric(clean["ШиринаЛенты"], errors="coerce").fillna(self.DEFAULT_TAPE_WIDTH).astype(int)
            clean = clean[(clean["Название"].astype(str).str.strip() != "") & (clean["Остаток"] > 0)].reset_index(drop=True)
        self._save_warehouse_data_from_df(clean)
        self.warehouse_data = clean

    def _save_warehouse_data_from_df(self, df: pd.DataFrame):
        with sqlite3.connect(self.warehouse_db_file) as conn:
            conn.execute("DELETE FROM warehouse")
            if not df.empty:
                rows = [
                    (
                        str(r.get("Номер", "")),
                        str(r.get("Название", "")).strip(),
                        int(r.get("ШиринаЛенты", self.DEFAULT_TAPE_WIDTH)),
                        str(r.get("Катушка", "")),
                        int(r.get("Остаток", 0)),
                    )
                    for _, r in df.iterrows()
                    if str(r.get("Название", "")).strip() and int(r.get("Остаток", 0)) > 0
                ]
                conn.executemany(
                    "INSERT INTO warehouse(number, name, tape_width, coil, qty) VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
            conn.commit()

    def _upsert_warehouse(self, num: str, name: str, width: int, qty: int):
        if qty <= 0:
            return
        same = self.warehouse_data[self.warehouse_data["Название"] == name] if not self.warehouse_data.empty else pd.DataFrame()
        coil_id = f"{name}-{len(same) + 1}"
        row = pd.DataFrame(
            [{"Номер": num, "Название": name, "ШиринаЛенты": int(width), "Катушка": coil_id, "Остаток": int(qty)}]
        )
        self.warehouse_data = pd.concat([self.warehouse_data, row], ignore_index=True)

    def _consume_from_first_coil(self, comp_name: str, qty_to_subtract: int) -> int:
        if qty_to_subtract <= 0 or self.warehouse_data.empty:
            return max(0, qty_to_subtract)
        rows = self.warehouse_data[self.warehouse_data["Название"] == comp_name]
        if rows.empty:
            return qty_to_subtract
        idx = rows.index[0]
        current_qty = int(self.warehouse_data.at[idx, "Остаток"])
        consume = min(current_qty, int(qty_to_subtract))
        self.warehouse_data.at[idx, "Остаток"] = current_qty - consume
        left = int(qty_to_subtract) - consume
        self.warehouse_data = self.warehouse_data[self.warehouse_data["Остаток"] > 0].reset_index(drop=True)
        return left

    def _board_qty_map(self, board: str) -> Dict[str, int]:
        if self.all_data is None or not board:
            return {}
        current_board_data = self.all_data[self.all_data["Sheet"] == board]
        agg = current_board_data.groupby("Name").agg({"Quantity": "sum"}).reset_index()
        k = max(1, int(self.board_multiplier))
        return {str(r["Name"]): int(r["Quantity"]) * k for _, r in agg.iterrows()}

    def _choose_start_board(self) -> str:
        if self.all_data is None or self.all_data.empty:
            return self.boards[0] if self.boards else ""
        stats = []
        for board in self.all_data["Sheet"].unique():
            board_parts = set(self.all_data[self.all_data["Sheet"] == board]["Name"].astype(str).unique())
            stats.append((board, len(board_parts)))
        stats.sort(key=lambda x: (-x[1], str(x[0])))
        return str(stats[0][0]) if stats else (self.boards[0] if self.boards else "")

    def _installed_components(self) -> set:
        return {str(c) for comps in self.setup_completed.values() for c in comps}

    def _best_next_board(self, board: str) -> Optional[Dict[str, Any]]:
        if self.all_data is None:
            return None
        installed = self._installed_components()
        unique_parts_current = set()
        if board:
            current_board_data = self.all_data[self.all_data["Sheet"] == board]
            unique_parts_current = set(current_board_data["Name"].astype(str).unique())
        compare_set = installed if installed else unique_parts_current
        if not compare_set:
            return None
        matches = []
        for other_board in self.all_data["Sheet"].unique():
            if other_board == board or self.is_board_completed(other_board):
                continue
            other_data = self.all_data[self.all_data["Sheet"] == other_board]
            needed = set(other_data["Name"].astype(str).unique())
            shared = compare_set.intersection(needed)
            matches.append({"board": other_board, "shared": len(shared), "need": len(needed)})
        if not matches:
            return None
        matches.sort(key=lambda x: (x["shared"], x["need"]), reverse=True)
        return matches[0]

    def _autoload_batch_for_board(self, board: str, batch_no: int = 1, only_newly_installed: bool = False) -> List[str]:
        board_key = str(board)
        loaded = [int(x) for x in self.batch_stock_deducted.get(board_key, []) if str(x).isdigit()]
        if batch_no in loaded:
            return []
        qty_map = self._board_qty_map(board_key)
        if not qty_map:
            return []

        self.setup_completed.setdefault(board_key, [])
        global_setup = {c for comps in self.setup_completed.values() for c in comps}
        to_load = []
        for name in qty_map.keys():
            info = self.feeder_map.get(name)
            if not info:
                continue
            try:
                b = int(info.get("batch", 1))
            except (TypeError, ValueError):
                b = 1
            if b == batch_no:
                to_load.append(name)

        newly_installed = []
        for name in to_load:
            if name not in global_setup and name not in self.setup_completed[board_key]:
                self.setup_completed[board_key].append(name)
                global_setup.add(name)
                newly_installed.append(name)

        shortages = []
        names_to_consume = newly_installed if only_newly_installed else to_load
        for name in names_to_consume:
            qty = int(qty_map.get(name, 0))
            left = self._consume_from_first_coil(name, qty)
            if left > 0:
                shortages.append(f"{name}: не хватило {left} шт.")

        self.batch_stock_deducted.setdefault(board_key, []).append(batch_no)
        self._save_warehouse_data()
        self._save_progress()
        return shortages

    def _prefill_qty_for_component(self, comp_name: str) -> int:
        qty_candidates = []
        if self.all_data is None:
            return 1
        for board in self.all_data["Sheet"].unique():
            if self.is_board_completed(board):
                continue
            board_map = self._board_qty_map(str(board))
            if comp_name in board_map:
                qty_candidates.append(int(board_map[comp_name]))
        if not qty_candidates:
            return 1
        return max(1, min(qty_candidates))

    def _consume_prefill_components(self, components: List[str]) -> List[str]:
        shortages = []
        for name in components:
            qty = self._prefill_qty_for_component(str(name))
            left = self._consume_from_first_coil(str(name), qty)
            if left > 0:
                shortages.append(f"{name}: не хватило {left} шт. (забивка)")
        if components:
            self._save_warehouse_data()
        return shortages

    def _load_bom_from_file(self, path: Path):
        xls = pd.ExcelFile(path)
        self.boards = list(xls.sheet_names)
        frames = []
        for sheet in xls.sheet_names:
            frame = pd.read_excel(xls, sheet)
            frame["Sheet"] = sheet
            frames.append(frame)
        self.all_data = pd.concat(frames, ignore_index=True)
        self.all_data.columns = self.all_data.columns.str.strip()

    def upload_bom(self, raw_bytes: bytes) -> Dict[str, Any]:
        with self._lock:
            self.bom_file.write_bytes(raw_bytes)
            self._load_bom_from_file(self.bom_file)
            if not self.boards:
                raise SMTServiceError("В BOM не найдено листов")
            self.batch_stock_deducted = {}
            self.current_board = self._choose_start_board()
            self._log(f"BOM загружен. Стартовая плата: {self.current_board}")
            self.calculate_global()
            return self.get_state()

    def set_board(self, board: str) -> Dict[str, Any]:
        with self._lock:
            if board not in self.boards:
                raise SMTServiceError(f"Плата '{board}' не найдена")
            self.current_board = board
            self.pending_remove_components = []
            self._render_board_data(auto_assign=False)
            self._log(f"Выбрана плата: {board}")
            self._save_progress()
            return self.get_state()

    def set_board_multiplier(self, multiplier: int) -> Dict[str, Any]:
        with self._lock:
            try:
                self.board_multiplier = max(1, int(multiplier))
            except (TypeError, ValueError):
                raise SMTServiceError("Количество плат должно быть целым числом >= 1")
            if self.current_board:
                self.calculate_global()
            self._log(f"Изменён множитель панели: {self.board_multiplier}")
            self._save_progress()
            return self.get_state()

    def set_chip_feeder_limit(self, limit: int) -> Dict[str, Any]:
        with self._lock:
            try:
                self.chip_feeder_limit = max(1, min(len(self.CS_BANK_SLOTS), int(limit)))
            except (TypeError, ValueError):
                raise SMTServiceError("Лимит фидеров должен быть целым числом >= 1")
            if self.current_board:
                self.calculate_global()
            self._log(f"Изменён лимит 8мм фидеров: {self.chip_feeder_limit}")
            self._save_progress()
            return self.get_state()

    def set_tape_limits(self, limits: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            for size in self.tape_sizes:
                if str(size) in limits:
                    try:
                        self.tape_limits[size] = max(0, int(limits[str(size)]))
                    except (TypeError, ValueError):
                        pass
            if self.current_board:
                self.calculate_global()
            self._log("Обновлены лимиты павука")
            self._save_progress()
            return self.get_state()

    def is_board_completed(self, board_name: str) -> bool:
        if self.all_data is None:
            return False
        board_data = self.all_data[self.all_data["Sheet"] == board_name]
        unique_parts = set(board_data["Name"].unique())
        if not unique_parts:
            return False
        done = self.instr_completed.get(board_name, [])
        return all(p in done for p in unique_parts)

    def calculate_global(self) -> Dict[str, Any]:
        with self._lock:
            if self.all_data is None:
                raise SMTServiceError("Сначала загрузите BOM")
            if not self.current_board:
                self.current_board = self._choose_start_board()
            self.pending_remove_components = []
            self.summary["status"] = "Базовые данные подготовлены"
            self._render_board_data(auto_assign=True)
            first_iteration = not any(v for v in self.batch_stock_deducted.values())
            shortages = self._autoload_batch_for_board(self.current_board, batch_no=1, only_newly_installed=False)
            if first_iteration:
                shortages.extend(self._consume_prefill_components(self.last_prefill_components))
            self.summary["shortages"] = shortages
            self._render_board_data(auto_assign=False)
            self._log(f"Выполнен расчёт для платы: {self.current_board}")
            self._save_progress()
            return self.get_state()

    def next_batch(self) -> Dict[str, Any]:
        with self._lock:
            if self.all_data is None:
                raise SMTServiceError("Сначала загрузите BOM")
            board = self.current_board
            if not board:
                raise SMTServiceError("Текущая плата не выбрана")
            best = self._best_next_board(board)
            if not best:
                raise SMTServiceError("Нет незавершенных плат для перехода")
            next_board = str(best["board"])
            self.current_board = next_board
            global_setup = {c for comps in self.setup_completed.values() for c in comps}
            next_all = set(self.all_data[self.all_data["Sheet"] == next_board]["Name"].unique())
            next_done = set(self.instr_completed.get(next_board, []))
            next_needed_active = next_all - next_done
            items_to_remove = sorted([comp for comp in global_setup if comp not in next_needed_active])
            self.pending_remove_components = items_to_remove

            self._render_board_data(auto_assign=True)
            self.summary["shortages"] = self._autoload_batch_for_board(next_board, batch_no=1, only_newly_installed=True)
            self._render_board_data(auto_assign=False)
            self.summary["status"] = f"Переход на плату {next_board}. Снимите деталей: {len(items_to_remove)}"
            self._log(f"Переход на плату {next_board}. К снятию: {len(items_to_remove)}")
            self._save_progress()
            return self.get_state()

    def _render_board_data(self, auto_assign: bool):
        board = self.current_board
        if not board or self.all_data is None:
            return

        required_cols = ("Name", "Quantity", "Designator", "Тип ленты")
        missing = [c for c in required_cols if c not in self.all_data.columns]
        if missing:
            raise SMTServiceError("В Excel не хватает колонок: " + ", ".join(missing))

        current_board_data = self.all_data[self.all_data["Sheet"] == board]
        unique_parts_current = set(current_board_data["Name"].unique())
        df_board = current_board_data.groupby("Name").agg(
            {
                "Quantity": "sum",
                "Designator": lambda x: ", ".join(
                    sorted(set(str(item) for sublist in x for item in str(sublist).split(",") if str(item).strip()))
                ),
            }
        ).reset_index()
        all_comps_info = self.all_data.groupby("Name").agg({"Тип ленты": "first"}).reset_index()
        all_comps_info["mm"] = all_comps_info["Тип ленты"].apply(self._parse_tape_width)
        board_counts = self.all_data.groupby("Name")["Sheet"].nunique().reset_index().rename(columns={"Sheet": "board_count"})
        best_match = self._best_next_board(board)
        shared_with_next = set()
        if best_match and int(best_match.get("shared", 0)) > 0:
            next_board_data = self.all_data[self.all_data["Sheet"] == str(best_match["board"])]
            shared_with_next = unique_parts_current.intersection(set(next_board_data["Name"].unique()))

        global_setup = {c for comps in self.setup_completed.values() for c in comps}
        board_feeder_map: Dict[str, Dict[str, Any]] = {}
        for nm in unique_parts_current:
            if nm in self.global_feeder_map:
                board_feeder_map[nm] = dict(self.global_feeder_map[nm])

        if auto_assign:
            self.last_prefill_components = []
            cs_needed = all_comps_info[(all_comps_info["Name"].isin(unique_parts_current)) & (all_comps_info["mm"] == 8)]
            pv_needed = all_comps_info[(all_comps_info["Name"].isin(unique_parts_current)) & (all_comps_info["mm"] > 8)]
            remaining_boards = [b for b in self.all_data["Sheet"].unique() if b != board and not self.is_board_completed(str(b))]
            remaining_parts = set()
            if remaining_boards:
                remaining_parts = set(self.all_data[self.all_data["Sheet"].isin(remaining_boards)]["Name"].astype(str).unique())
            future_parts = sorted(remaining_parts - {str(x) for x in unique_parts_current})
            prefill_pool = all_comps_info[all_comps_info["Name"].astype(str).isin(future_parts)]
            prefill_pool = prefill_pool.merge(board_counts, on="Name", how="left")
            prefill_pool["board_count"] = pd.to_numeric(prefill_pool["board_count"], errors="coerce").fillna(0).astype(int)
            prefill_pool = prefill_pool.sort_values(by=["board_count", "Name"], ascending=[False, True])
            cs_prefill = prefill_pool[prefill_pool["mm"] == 8]["Name"].astype(str).tolist()
            pv_prefill = prefill_pool[prefill_pool["mm"] > 8][["Name", "mm"]]
            available_cs_slots = self.CS_BANK_SLOTS[: max(1, int(self.chip_feeder_limit))]
            available_cs_set = set(available_cs_slots)
            locked_cs_slots = set()
            for name in global_setup:
                info = self.global_feeder_map.get(name)
                if info and info.get("station") == "ЧИПШУТЕР":
                    slot = str(info.get("slot"))
                    if slot in available_cs_set:
                        locked_cs_slots.add(slot)
                    if name in unique_parts_current and slot in available_cs_set:
                        board_feeder_map[name] = {"batch": 1, "slot": info.get("slot"), "station": "ЧИПШУТЕР"}
                    elif slot in available_cs_set:
                        board_feeder_map[name] = {"batch": 1, "slot": info.get("slot"), "station": "ЧИПШУТЕР"}

            cs_unplaced = cs_needed[~cs_needed["Name"].isin(board_feeder_map.keys())]
            cs_unplaced = cs_unplaced.merge(board_counts, on="Name", how="left")
            cs_unplaced["board_count"] = pd.to_numeric(cs_unplaced["board_count"], errors="coerce").fillna(0).astype(int)
            cs_unplaced["shared_next"] = cs_unplaced["Name"].map(lambda n: 1 if str(n) in shared_with_next else 0)
            cs_unplaced = cs_unplaced.sort_values(
                by=["shared_next", "board_count", "Name"],
                ascending=[False, False, True],
            )["Name"].astype(str).tolist()
            cs_plan = cs_unplaced + [n for n in cs_prefill if n not in board_feeder_map and n not in cs_unplaced]
            used_cs_slots = set()

            def assign_next_cs_slot(comp_name: str):
                hist_info = self.global_feeder_map.get(comp_name)
                if hist_info and hist_info.get("station") == "ЧИПШУТЕР":
                    s = str(hist_info.get("slot"))
                    if s in available_cs_set and s not in locked_cs_slots and s not in used_cs_slots:
                        used_cs_slots.add(s)
                        return 1, s
                for slot in available_cs_slots:
                    if slot not in locked_cs_slots and slot not in used_cs_slots:
                        used_cs_slots.add(slot)
                        return 1, slot
                return None

            for name in cs_plan:
                res = assign_next_cs_slot(name)
                if res is None:
                    if name in unique_parts_current:
                        raise SMTServiceError(f"Не удалось разместить '{name}' в чипшутере")
                    continue
                b, s = res
                board_feeder_map[name] = {"batch": b, "slot": s, "station": "ЧИПШУТЕР"}
                self.global_feeder_map[name] = dict(board_feeder_map[name])
                if name not in unique_parts_current:
                    self.last_prefill_components.append(name)

            locked_pv_slots = set()
            for name in global_setup:
                info = self.global_feeder_map.get(name)
                if info and info.get("station") == "ПАВУК":
                    base = str(info.get("slot", "")).split(" ")[0]
                    m = re.match(r"([LR])(\d+)-([LR])(\d+)", base)
                    if m:
                        prefix, start_n, end_n = m.group(1), int(m.group(2)), int(m.group(4))
                        for i in range(start_n, end_n + 1):
                            locked_pv_slots.add(f"{prefix}{i}")
                    if name in unique_parts_current:
                        board_feeder_map[name] = {"batch": 1, "slot": info.get("slot"), "station": "ПАВУК"}
                    else:
                        board_feeder_map[name] = {"batch": 1, "slot": info.get("slot"), "station": "ПАВУК"}

            pv_unplaced = pv_needed[~pv_needed["Name"].isin(board_feeder_map.keys())]
            pv_unplaced = pv_unplaced.merge(board_counts, on="Name", how="left")
            pv_unplaced["board_count"] = pd.to_numeric(pv_unplaced["board_count"], errors="coerce").fillna(0).astype(int)
            pv_unplaced["shared_next"] = pv_unplaced["Name"].map(lambda n: 1 if str(n) in shared_with_next else 0)
            pv_unplaced = pv_unplaced.sort_values(
                by=["shared_next", "board_count", "Name"],
                ascending=[False, False, True],
            )
            pv_plan = list(pv_unplaced[["Name", "mm"]].itertuples(index=False, name=None))
            for _, row in pv_prefill.iterrows():
                name = str(row["Name"])
                if name not in board_feeder_map and all(name != n for n, _ in pv_plan):
                    pv_plan.append((name, int(row["mm"])))
            pv_batch_usage: Dict[int, int] = {s: 0 for s in self.tape_sizes}
            pv_slots_used: set = set()

            def assign_pv(width: int, name: str) -> bool:
                if self.tape_limits.get(width, 0) <= 0:
                    return False
                slots_needed = 3 if width in (32, 44) else 2
                if pv_batch_usage.get(width, 0) >= self.tape_limits.get(width, 0):
                    return False
                hist = self.global_feeder_map.get(name)
                if hist and hist.get("station") == "ПАВУК":
                    base = str(hist.get("slot", "")).split(" ")[0]
                    m = re.match(r"([LR])(\d+)-([LR])(\d+)", base)
                    if m:
                        prefix, start_n, end_n = m.group(1), int(m.group(2)), int(m.group(4))
                        cluster = [f"{prefix}{i}" for i in range(start_n, end_n + 1)]
                        if len(cluster) == slots_needed and all(c not in locked_pv_slots and c not in pv_slots_used for c in cluster):
                            assigned = f"{cluster[0]}-{cluster[-1]}"
                            for c in cluster:
                                pv_slots_used.add(c)
                            pv_batch_usage[width] = pv_batch_usage.get(width, 0) + 1
                            board_feeder_map[name] = {"batch": 1, "slot": f"{assigned} ({width}мм)", "station": "ПАВУК"}
                            self.global_feeder_map[name] = dict(board_feeder_map[name])
                            return True
                for prefix in ["L", "R"]:
                    for start_idx in range(1, 21 - slots_needed + 1):
                        cluster = [f"{prefix}{i}" for i in range(start_idx, start_idx + slots_needed)]
                        if all(c not in locked_pv_slots and c not in pv_slots_used for c in cluster):
                            assigned = f"{cluster[0]}-{cluster[-1]}"
                            for c in cluster:
                                pv_slots_used.add(c)
                            pv_batch_usage[width] = pv_batch_usage.get(width, 0) + 1
                            board_feeder_map[name] = {"batch": 1, "slot": f"{assigned} ({width}мм)", "station": "ПАВУК"}
                            self.global_feeder_map[name] = dict(board_feeder_map[name])
                            return True
                return False

            skipped_mm = set()
            for name, mm in pv_plan:
                ok = assign_pv(int(mm), str(name))
                if not ok and str(name) in unique_parts_current:
                    skipped_mm.add(int(mm))
                if ok and str(name) not in unique_parts_current:
                    self.last_prefill_components.append(str(name))
            if skipped_mm:
                self.summary["status"] = f"Для компонентов {sorted(skipped_mm)}мм нет свободных слотов/лимитов"

        self.feeder_map = board_feeder_map
        next_board_text = "нет"
        if best_match:
            next_board_text = f"{best_match['board']} (Общих: {best_match['shared']})"

        setup_rows, instr_rows = [], []
        self.batch_comps = {}
        qty_mul = max(1, int(self.board_multiplier))
        for _, r in df_board.iterrows():
            name = str(r["Name"])
            info = self.feeder_map.get(name, {"batch": "?", "slot": "?", "station": "?"})
            batch_label = f"{info['station']} №{info['batch']}"
            self.batch_comps.setdefault(batch_label, []).append(name)
            qty = int(r["Quantity"]) * qty_mul
            setup_rows.append({"СТАНОК": info["station"], "ОЧЕРЕДЬ (ЗАГРУЗКА)": f"№ {info['batch']}", "ПОЗИЦИЯ / ФИДЕР": info["slot"], "ЧТО СТАВИМ": name, "НУЖНО ШТ": qty})
            instr_rows.append({"ПРИОРИТЕТ": f"{info['station']} (Батч {info['batch']})", "ДЕТАЛЬ": name, "СЛОТ": info["slot"], "КОЛ-ВО": qty, "ПОЗИЦИИ (DESIGNATORS)": str(r["Designator"])})

        df_setup, df_instr = pd.DataFrame(setup_rows), pd.DataFrame(instr_rows)
        if not df_setup.empty:
            qn = pd.to_numeric(df_setup["ОЧЕРЕДЬ (ЗАГРУЗКА)"].str.replace(r"[^\d]", "", regex=True), errors="coerce").fillna(0).astype(int)
            ord_chip = df_setup.apply(lambda r: self._cs_slot_order(r["ПОЗИЦИЯ / ФИДЕР"]) if str(r["СТАНОК"]) == "ЧИПШУТЕР" else -1, axis=1)
            ord_pv = df_setup.apply(lambda r: self._pv_cluster(r["ПОЗИЦИЯ / ФИДЕР"]) if str(r["СТАНОК"]) == "ПАВУК" else "", axis=1)
            df_setup = df_setup.assign(__st=df_setup["СТАНОК"].astype(str), __qn=qn, __oc=pd.to_numeric(ord_chip, errors="coerce").fillna(9999).astype(int), __op=ord_pv.astype(str), __sl=df_setup["ПОЗИЦИЯ / ФИДЕР"].map(lambda x: str(x) if x is not None else ""))
            df_setup = df_setup.sort_values(by=["__st", "__qn", "__oc", "__op", "__sl"]).drop(columns=["__st", "__qn", "__oc", "__op", "__sl"])
        if not df_instr.empty:
            bt = pd.to_numeric(df_instr["ПРИОРИТЕТ"].str.extract(r"Батч\s*(\d+)", expand=False), errors="coerce").fillna(0).astype(int)
            st_order = df_instr["ПРИОРИТЕТ"].str.contains("ПАВУК", na=False).astype(int)
            oc = df_instr.apply(lambda r: self._cs_slot_order(r["СЛОТ"]) if "ЧИПШУТЕР" in str(r["ПРИОРИТЕТ"]) else -1, axis=1)
            op = df_instr.apply(lambda r: self._pv_cluster(r["СЛОТ"]) if "ПАВУК" in str(r["ПРИОРИТЕТ"]) else "", axis=1)
            df_instr = df_instr.assign(__bt=bt, __st=st_order, __oc=pd.to_numeric(oc, errors="coerce").fillna(9999).astype(int), __op=op.astype(str), __sl=df_instr["СЛОТ"].map(lambda x: str(x) if x is not None else ""))
            df_instr = df_instr.sort_values(by=["__st", "__bt", "__oc", "__op", "__sl"]).drop(columns=["__bt", "__st", "__oc", "__op", "__sl"])

        n_cs, max_cs_b = 0, 0
        for nm in unique_parts_current:
            inf = self.feeder_map.get(nm)
            if inf and inf.get("station") == "ЧИПШУТЕР":
                n_cs += 1
                try:
                    max_cs_b = max(max_cs_b, int(inf["batch"]))
                except (TypeError, ValueError):
                    max_cs_b = max(max_cs_b, 1)
        cs_batches = [b for b in self.batch_comps.keys() if "ЧИПШУТЕР" in b]
        pv_batches = [b for b in self.batch_comps.keys() if "ПАВУК" in b]
        warn_text = []
        if len(cs_batches) > 1:
            warn_text.append(f"Чипшутер - {len(cs_batches)} захода")
        if len(pv_batches) > 1:
            warn_text.append(f"Павук - {len(pv_batches)} захода")
        recharge_warning = f"⚠️ ТРЕБУЕТСЯ ПЕРЕЗАПРАВКА! ({', '.join(warn_text)})" if warn_text else "✅ Влазит в один заход (без перезаправок)"

        self.summary.update({
            "total_parts": int(pd.to_numeric(df_board["Quantity"], errors="coerce").fillna(0).sum()) * qty_mul,
            "unique_parts": int(len(df_board)),
            "feeders_used": f"Слотов по плате: {len(df_board)} | 8 мм: {n_cs} лент, заходов чипшутера: {max_cs_b} (доступно {self.chip_feeder_limit} из {len(self.CS_BANK_SLOTS)})",
            "next_board": next_board_text,
            "recharge_warning": recharge_warning,
            "board_multiplier": qty_mul,
            "pending_remove": len(self.pending_remove_components),
        })
        self.table_setup = [] if df_setup.empty else df_setup.to_dict("records")
        self.table_instr = [] if df_instr.empty else df_instr.to_dict("records")

    def toggle_setup(self, components: List[str]) -> Dict[str, Any]:
        with self._lock:
            board = self.current_board
            if not board:
                raise SMTServiceError("Плата не выбрана")
            target = self.setup_completed.setdefault(board, [])
            names = [str(x) for x in components if str(x).strip()]
            if names:
                all_done = all(n in target for n in names)
                for n in names:
                    if all_done and n in target:
                        target.remove(n)
                    elif not all_done and n not in target:
                        target.append(n)
            self._save_progress()
            return self.get_state()

    def toggle_instr(self, components: List[str]) -> Dict[str, Any]:
        with self._lock:
            board = self.current_board
            if not board:
                raise SMTServiceError("Плата не выбрана")
            target = self.instr_completed.setdefault(board, [])
            names = [str(x) for x in components if str(x).strip()]
            if names:
                all_done = all(n in target for n in names)
                for n in names:
                    if all_done and n in target:
                        target.remove(n)
                    elif not all_done and n not in target:
                        target.append(n)
            self._save_progress()
            return self.get_state()

    def remove_from_machine(self, component: str) -> Dict[str, Any]:
        with self._lock:
            comp = str(component).strip()
            if comp:
                for b_name in list(self.setup_completed.keys()):
                    if comp in self.setup_completed[b_name]:
                        self.setup_completed[b_name].remove(comp)
                self.pending_remove_components = [x for x in self.pending_remove_components if x != comp]
                self._save_progress()
            return self.get_state()

    def add_manual_warehouse_item(self, num: str, name: str, width: int, qty: int) -> Dict[str, Any]:
        with self._lock:
            if not str(name).strip():
                raise SMTServiceError("Название компонента не может быть пустым")
            self._upsert_warehouse(str(num).strip(), str(name).strip(), int(width), int(qty))
            self._save_warehouse_data()
            self._log(f"Добавлен складской остаток вручную: {name} ({qty} шт.)")
            return self.get_state()

    def upload_warehouse_excel(self, raw_bytes: bytes) -> Dict[str, Any]:
        with self._lock:
            df = pd.read_excel(io.BytesIO(raw_bytes))
            if len(df.columns) >= 4:
                df = df.iloc[:, :4]
                df.columns = ["Номер", "Название", "ШиринаЛенты", "Остаток"]
            elif len(df.columns) >= 3:
                df = df.iloc[:, :3]
                df.columns = ["Номер", "Название", "Остаток"]
                df["ШиринаЛенты"] = self.DEFAULT_TAPE_WIDTH
            else:
                raise SMTServiceError("В Excel склада должно быть минимум 3 колонки")
            df["Остаток"] = pd.to_numeric(df["Остаток"], errors="coerce").fillna(0).astype(int)
            df["ШиринаЛенты"] = pd.to_numeric(df["ШиринаЛенты"], errors="coerce").fillna(self.DEFAULT_TAPE_WIDTH).astype(int)
            for _, row in df.iterrows():
                self._upsert_warehouse(str(row["Номер"]), str(row["Название"]).strip(), int(row["ШиринаЛенты"]), int(row["Остаток"]))
            self._save_warehouse_data()
            self._log("Склад загружен из Excel")
            return self.get_state()

    def _machine_visual_state(self) -> Dict[str, Any]:
        global_setup = {c for comps in self.setup_completed.values() for c in comps}
        board = self.current_board
        curr_needed_active, future_needed = set(), set()
        pending_remove = set(self.pending_remove_components)
        if self.all_data is not None and board:
            curr_all = set(self.all_data[self.all_data["Sheet"] == board]["Name"].unique())
            curr_done = set(self.instr_completed.get(board, []))
            curr_needed_active = curr_all - curr_done
            for b_name in self.all_data["Sheet"].unique():
                if b_name != board and not self.is_board_completed(b_name):
                    future_needed.update(self.all_data[self.all_data["Sheet"] == b_name]["Name"].unique())

        chip_slots = []
        for slot in self.CS_BANK_SLOTS:
            component = ""
            for name in global_setup:
                info = self.global_feeder_map.get(name)
                if info and info.get("station") == "ЧИПШУТЕР" and info.get("slot") == slot:
                    component = name
                    break
            status = "empty"
            in_limit = self.CS_BANK_SLOTS.index(slot) < max(1, int(self.chip_feeder_limit))
            if component:
                if component in pending_remove:
                    status = "remove-next"
                else:
                    status = "active" if component in curr_needed_active else "keep" if component in future_needed else "remove"
            chip_slots.append({"slot": slot, "component": component, "status": status, "enabled": in_limit})

        spider_slots = []
        for name in sorted(global_setup):
            info = self.global_feeder_map.get(name)
            if info and info.get("station") == "ПАВУК":
                if name in pending_remove:
                    status = "remove-next"
                else:
                    status = "active" if name in curr_needed_active else "keep" if name in future_needed else "remove"
                spider_slots.append({"slot": str(info.get("slot")), "component": name, "batch": int(info.get("batch", 1)), "status": status})
            return {"chipshooter": chip_slots, "spider": spider_slots}

    def clear_zero_stock_positions(self) -> Dict[str, Any]:
        with self._lock:
            if self.warehouse_data.empty:
                self._log("Очистка нулевых позиций: склад уже пуст")
                return self.get_state()
            self.warehouse_data["Остаток"] = pd.to_numeric(self.warehouse_data["Остаток"], errors="coerce").fillna(0).astype(int)
            self.warehouse_data = self.warehouse_data[self.warehouse_data["Остаток"] > 0].reset_index(drop=True)
            self._save_warehouse_data()
            self._log("Очистка нулевых позиций выполнена")
            return self.get_state()

    def emergency_clear_warehouse(self) -> Dict[str, Any]:
        with self._lock:
            self.warehouse_data = pd.DataFrame(columns=["Номер", "Название", "ШиринаЛенты", "Катушка", "Остаток"])
            self._save_warehouse_data()
            self._log("Экстренная очистка склада выполнена")
            return self.get_state()

    def clear_project(self) -> Dict[str, Any]:
        with self._lock:
            self.all_data = None
            self.boards = []
            self.current_board = ""
            self.global_feeder_map = {}
            self.feeder_map = {}
            self.setup_completed = {}
            self.instr_completed = {}
            self.batch_stock_deducted = {}
            self.batch_comps = {}
            self.pending_remove_components = []
            self.last_prefill_components = []
            self.table_setup = []
            self.table_instr = []
            self.summary.update({
                "status": "Проект очищен. Загрузите новый BOM",
                "total_parts": 0,
                "unique_parts": 0,
                "feeders_used": "",
                "next_board": "",
                "recharge_warning": "",
                "shortages": [],
                "pending_remove": 0,
            })
            try:
                if self.bom_file.exists():
                    self.bom_file.unlink()
            except OSError:
                pass
            try:
                if self.progress_file.exists():
                    self.progress_file.unlink()
            except OSError:
                pass
            self._log("Текущий проект очищен (склад сохранён)")
            return self.get_state()

    def _progress(self) -> List[Dict[str, Any]]:
        done = set(self.instr_completed.get(self.current_board, [])) if self.current_board else set()
        return [{"batch": b, "done": sum(1 for c in comps if c in done), "total": len(comps)} for b, comps in sorted(self.batch_comps.items())]

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            if self.all_data is not None and self.current_board:
                self._render_board_data(auto_assign=False)
            return {
                "boards": self.boards,
                "current_board": self.current_board,
                "summary": self.summary,
                "table_setup": self.table_setup,
                "table_instr": self.table_instr,
                "progress": self._progress(),
                "setup_completed": self.setup_completed,
                "instr_completed": self.instr_completed,
                "global_feeder_map": self.global_feeder_map,
                "tape_limits": {str(k): int(v) for k, v in self.tape_limits.items()},
                "chip_feeder_limit": int(self.chip_feeder_limit),
                "board_multiplier": int(self.board_multiplier),
                "pending_remove_components": self.pending_remove_components,
                "warehouse": [] if self.warehouse_data.empty else self.warehouse_data.to_dict("records"),
                "visual": self._machine_visual_state(),
                "logs": self.log_console,
            }


service = SMTService()
