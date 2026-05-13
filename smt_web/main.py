from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pnp_service import SMTServiceError, service


class BoardRequest(BaseModel):
    board: str


class ToggleRequest(BaseModel):
    components: List[str]


class RemoveRequest(BaseModel):
    component: str


class TapeLimitsRequest(BaseModel):
    limits: Dict[str, Any]


class ManualWarehouseRequest(BaseModel):
    num: str = ""
    name: str
    width: int = 8
    qty: int = 0


app = FastAPI(title="SMT Navigator Web API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def _result(action):
    try:
        return action()
    except SMTServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Internal error: {exc}")


@app.get("/")
def root():
    return FileResponse(static_dir / "index.html")


@app.get("/api/state")
def get_state():
    return _result(service.get_state)


@app.post("/api/upload-bom")
async def upload_bom(file: UploadFile = File(...)):
    raw = await file.read()
    return _result(lambda: service.upload_bom(raw))


@app.post("/api/calculate")
def calculate():
    return _result(service.calculate_global)


@app.post("/api/board")
def set_board(req: BoardRequest):
    return _result(lambda: service.set_board(req.board))


@app.post("/api/next-batch")
def next_batch():
    return _result(service.next_batch)


@app.post("/api/toggle-setup")
def toggle_setup(req: ToggleRequest):
    return _result(lambda: service.toggle_setup(req.components))


@app.post("/api/toggle-instr")
def toggle_instr(req: ToggleRequest):
    return _result(lambda: service.toggle_instr(req.components))


@app.post("/api/remove-component")
def remove_component(req: RemoveRequest):
    return _result(lambda: service.remove_from_machine(req.component))


@app.post("/api/tape-limits")
def set_tape_limits(req: TapeLimitsRequest):
    return _result(lambda: service.set_tape_limits(req.limits))


@app.post("/api/warehouse/manual")
def add_manual_warehouse(req: ManualWarehouseRequest):
    return _result(lambda: service.add_manual_warehouse_item(req.num, req.name, req.width, req.qty))


@app.post("/api/warehouse/upload")
async def upload_warehouse(file: UploadFile = File(...)):
    raw = await file.read()
    return _result(lambda: service.upload_warehouse_excel(raw))
