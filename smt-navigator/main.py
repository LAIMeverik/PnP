import os
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


class BoardMultiplierRequest(BaseModel):
    multiplier: int = 1


class ToggleRequest(BaseModel):
    components: List[str]


class RemoveRequest(BaseModel):
    component: str


class TapeLimitsRequest(BaseModel):
    limits: Dict[str, Any]


class FeedersLimitRequest(BaseModel):
    limit: int = 50


class ManualWarehouseRequest(BaseModel):
    num: str = ""
    name: str
    width: int = 8
    qty: int = 0


app = FastAPI(title="SMT Navigator Web API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

root_dir = Path(__file__).resolve().parent.parent
static_dir = root_dir / "static"
if not static_dir.exists():
    alt_static = root_dir / "static ui"
    if alt_static.exists():
        static_dir = alt_static
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


@app.post("/api/board-multiplier")
def set_board_multiplier(req: BoardMultiplierRequest):
    return _result(lambda: service.set_board_multiplier(req.multiplier))


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


@app.post("/api/feeders-limit")
def set_feeders_limit(req: FeedersLimitRequest):
    return _result(lambda: service.set_chip_feeder_limit(req.limit))


@app.post("/api/warehouse/manual")
def add_manual_warehouse(req: ManualWarehouseRequest):
    return _result(lambda: service.add_manual_warehouse_item(req.num, req.name, req.width, req.qty))


@app.post("/api/warehouse/upload")
async def upload_warehouse(file: UploadFile = File(...)):
    raw = await file.read()
    return _result(lambda: service.upload_warehouse_excel(raw))


@app.post("/api/warehouse/clear-zero")
def clear_zero_warehouse():
    return _result(service.clear_zero_stock_positions)


@app.post("/api/warehouse/clear-all")
def clear_all_warehouse():
    return _result(service.emergency_clear_warehouse)


@app.post("/api/project/clear")
def clear_project():
    return _result(service.clear_project)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.getenv("SMT_HOST", "0.0.0.0"),
        port=int(os.getenv("SMT_PORT", "8000")),
        reload=os.getenv("SMT_RELOAD", "0") == "1",
    )
