from fastapi import FastAPI, UploadFile, File, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import pandas as pd
import io

from pnp_service import SessionLocal, WarehouseItem, SMTState, SMTLogic

app = FastAPI()
logic = SMTLogic()

# Разрешаем доступ с любых устройств в сети (планшеты, телефоны)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"])

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

@app.get("/api/state")
def get_current_state(db: Session = Depends(get_db)):
    state = db.query(SMTState).first()
    if not state:
        state = SMTState(current_board="", setup_completed={}, instr_completed={}, feeder_map={})
        db.add(state)
        db.commit()
    return state

@app.post("/api/upload-bom")
async def upload_bom(file: UploadFile = File(...), db: Session = Depends(get_db)):
    contents = await file.read()
    df_dict = pd.read_excel(io.BytesIO(contents), sheet_name=None)
    boards = list(df_dict.keys())
    # Сохраняем первую плату как активную
    state = db.query(SMTState).first()
    state.current_board = boards[0]
    db.commit()
    return {"boards": boards}

@app.get("/api/warehouse")
def list_warehouse(db: Session = Depends(get_db)):
    return db.query(WarehouseItem).all()

# Запуск: uvicorn main:app --host 0.0.0.0 --port 8000