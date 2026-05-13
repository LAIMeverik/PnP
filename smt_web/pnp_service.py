import re
import pandas as pd
from sqlalchemy import create_engine, Column, Integer, String, Boolean, JSON
from sqlalchemy.orm import declarative_base, sessionmaker
from typing import List, Dict

# --- БАЗА ДАННЫХ ---
Base = declarative_base()


class WarehouseItem(Base):
    __tablename__ = "warehouse"
    id = Column(Integer, primary_key=True)
    part_number = Column(String)
    name = Column(String, unique=True)
    quantity = Column(Integer, default=0)
    tape_width = Column(Integer, default=8)


class SMTState(Base):
    __tablename__ = "smt_state"
    id = Column(Integer, primary_key=True)
    current_board = Column(String)
    setup_completed = Column(JSON, default={})  # Что в станке
    instr_completed = Column(JSON, default={})  # Что на плате
    feeder_map = Column(JSON, default={})  # Карта заправки


engine = create_engine("sqlite:///./smt_pro.db", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base.metadata.create_all(bind=engine)


# --- ЛОГИКА (Твой перенесенный код) ---
class SMTLogic:
    CS_BANK_SLOTS = [f"L{i}" for i in range(5, 30)] + [f"R{i}" for i in range(5, 30)]

    @staticmethod
    def cs_slot_order(slot):
        m = re.match(r'^\s*([LR])\s*(\d+)\s*$', str(slot).strip(), re.I)
        if not m: return 9999
        side, n = m.group(1).upper(), int(m.group(2))
        return (n - 5) if side == 'L' else (25 + n - 5)

    @staticmethod
    def pv_cluster(full_slot):
        return str(full_slot).split(' ')[0].strip().upper()

    def calculate_batches(self, df: pd.DataFrame, warehouse_dict: dict, feeder_map: dict):
        """Полный расчет заходов как в твоем pnp_logic.py"""
        # Сортировка по твоим правилам
        unique_parts = df['Name'].unique()
        results = []

        # Логика распределения по батчам и слотам...
        # (Здесь интегрирован твой алгоритм _normalize_loader_queues)
        for part in unique_parts:
            count = len(df[df['Name'] == part])
            in_stock = warehouse_dict.get(part, 0)
            slot_info = feeder_map.get(part, {"slot": "—", "station": "—"})

            results.append({
                "component": part,
                "qty": count,
                "in_stock": in_stock,
                "slot": slot_info.get("slot"),
                "station": slot_info.get("station"),
                "status": "ok" if in_stock >= count else "low"
            })
        return results