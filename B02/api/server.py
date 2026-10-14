"""B02 Step 4 — 时序查询 Web 服务

提供时序曲线页面和查询接口。
B07 外部调用走 get_current_environment() / get_environment_history()，
不经过 HTTP。

路由：
    GET  /                 → 时序曲线页面
    GET  /api/stats        → 统计摘要（query: start, end）
    GET  /api/timeseries   → 时序数据（query: start, end）
    POST /api/generate     → 重新生成数据（query: force=1）
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

_self_path = Path(__file__).resolve().parent
_b02_root = _self_path.parent
_root = _b02_root.parent
if str(_b02_root) not in sys.path:
    sys.path.insert(0, str(_b02_root))

from api.history import (
    get_current_environment,
    get_environment_history,
    query_stats,
)
from database.store import (
    DB_PATH,
    init_schema,
    is_empty,
    write_samples,
    row_count,
    verify_count,
    verify_order,
)
from simulator.engine import GreenhouseSimulator


STATIC_DIR = _b02_root / "static"
STATIC_DIR.mkdir(exist_ok=True)

app = FastAPI(title="GreenMate B02 — 时序数据", version="0.1.0")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "history.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/stats")
def stats_endpoint(
    start: str = Query(default=None, description="ISO 格式起始时间"),
    end:   str = Query(default=None, description="ISO 格式结束时间"),
):
    if not start or not end:
        current = get_current_environment()
        if not current:
            raise HTTPException(status_code=404, detail="数据库无数据，请先调用 POST /api/generate")
        end = current.timestamp.isoformat()
        start_dt = datetime.fromisoformat(end) - timedelta(days=3)
        start = start_dt.isoformat()

    try:
        result = query_stats(start, end)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return result


@app.get("/api/timeseries")
def timeseries_endpoint(
    start: str = Query(..., description="ISO 格式起始时间"),
    end:   str = Query(..., description="ISO 格式结束时间"),
):
    try:
        samples = get_environment_history(start, end)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "count": len(samples),
        "start": start,
        "end": end,
        "series": [
            {
                "t": s.timestamp.isoformat(),
                "temp": s.temperature,
                "humid": s.humidity,
                "lux": s.lux,
                "scenario": s.sim_scenario,
            }
            for s in samples
        ],
    }


class GenerateRequest(BaseModel):
    force: bool = False


@app.post("/api/generate")
def generate_endpoint(req: GenerateRequest = GenerateRequest()):
    init_schema()
    if not is_empty() and not req.force:
        return {"message": "数据已存在，跳过生成（传 force=true 强制重刷）", "count": row_count()}

    sim = GreenhouseSimulator(seed=42)
    samples = sim.generate()
    inserted = write_samples(samples)

    ok_count, msg_count = verify_count()
    ok_order, msg_order = verify_order()

    return {
        "message": f"生成并写入 {inserted} 条样本",
        "count": inserted,
        "count_ok": ok_count,
        "order_ok": ok_order,
        "first_time": samples[0].timestamp.isoformat(),
        "last_time": samples[-1].timestamp.isoformat(),
    }


@app.get("/api/status")
def status_endpoint():
    """返回当前数据库状态，用于页面初始化判断。"""
    init_schema()
    count = row_count()
    current = get_current_environment()
    return {
        "db_exists": DB_PATH.exists(),
        "sample_count": count,
        "has_data": count > 0,
        "current": {
            "t": current.timestamp.isoformat(),
            "temp": current.temperature,
            "humid": current.humidity,
            "lux": current.lux,
            "scenario": current.sim_scenario,
        } if current else None,
    }


if __name__ == "__main__":
    import uvicorn
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    uvicorn.run(app, host="127.0.0.1", port=8001)
