"""B07 第三遍 · 视图一 | 状态总览 的 Web 版

★ 与终端版共用同一份数据层：**必须复用 `B07.views.view_data()`**，
  web 层**不许**重算任何东西 —— 不调 LLM、不跑 B06、不取天气。

  · `GET /`                     → 静态页面（B07/static/views.html，离线可开）
  · `GET /api/overview?run_id=` → 视图一的全部数据（直接来自 view_data）
  · `GET /api/evolution?run_id=` → 视图二的全部数据（来自 view_evolution_data）
  · `GET /api/evidence?run_id=` → 视图三（证据空间）的全部数据
  · `GET /api/evolution/compare?a=&b=` → 视图二的 S1↔S2 并排对比
  · `GET /api/compare?a=&b=`     → 视图四（场景回放与对比）的三层差异 + 归因
  · `GET /api/runs`             → 可选的 run_id 列表（只读 index.json）
  · `GET /health`              → B08 浅健康检查（只证明进程活着，绝不碰外部服务）
  · `GET /health/deep`         → B08 深健康检查（逐项体检 B02/B03/B04/B06）

页面顶部固定显示 run_id 与指纹，并提供切换 run_id 的入口。

跑法：
  python -m B07.views_web                # 默认 http://127.0.0.1:8010
  python -m B07.views_web --port 8020
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from B07.health import deep_health, shallow_health
from B07.views import (
    RUNS_DIR,
    compare_data,
    view_compare_data,
    view_data,
    view_evolution_data,
    view_evidence_data,
)

STATIC_DIR = _PROJECT_ROOT / "B07" / "static"
INDEX_PATH = RUNS_DIR / "index.json"

app = FastAPI(title="GreenMate · 视图 状态总览 / 状态演化 / 证据空间 / 场景对比",
              version="1.3")


# --------------------------------------------------------------------------
# B08 第一遍 · 两层健康检查（文档 p.92）
#
#   /health       浅：**只证明进程活着**，永远 200，绝不碰外部服务
#                 → 天气 / 知识挂了页面也不会无响应
#   /health/deep  深：逐项体检 B02 库 / B03 天气 / B04 知识 / B06 模型
#                 → B03 默认**不发真实请求**（?probe=true 才真调）
# --------------------------------------------------------------------------

@app.get("/health")
def health():
    """浅健康检查 —— 只证明进程活着。

    ★ 无论 B03 天气 / B04 知识 / B02 库 / B06 模型是死是活，这里都返回 200，
      而且**不访问任何外部服务**（连 sqlite 都不碰），所以永远秒回。
    """
    return shallow_health()


@app.get("/health/deep")
def health_deep(probe: bool = Query(False,
                                   description="B03 天气是否真调一次 API（默认不调）")):
    """深健康检查 —— 逐项体检外部依赖。

    ★ 外部服务挂掉时，本接口**仍然返回 200**，但 body 里的
      `status` 会是 degraded / error，每一项都有明确的原因 ——
      这样探针能区分"服务死了"和"某个依赖坏了"。
    """
    return deep_health(probe=probe)


@app.get("/")
def index():
    """视图一页面（静态，离线可开，不引任何 CDN 外链）。"""
    page = STATIC_DIR / "views.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail=f"缺少静态页面：{page}")
    return FileResponse(page)


@app.get("/api/runs")
def list_runs():
    """可切换的 run_id 列表（只读 index.json，不重算）。"""
    if not INDEX_PATH.exists():
        return {"runs": []}
    data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    runs = data.get("runs") or {}
    out = []
    for rid, meta in runs.items():
        out.append({
            "run_id": rid,
            "created_at": meta.get("created_at"),
            "status": meta.get("status"),
            "combined_attention": meta.get("combined_attention"),
        })
    out.sort(key=lambda r: r["run_id"])
    return {"runs": out}


@app.get("/api/overview")
def overview(run_id: str = Query(..., description="run_id，例如 run_082")):
    """视图一的全部数据 —— 直接来自 B07.views.view_data()（只读存档）。"""
    try:
        return view_data(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:              # 失败显式暴露，不伪装成功
        raise HTTPException(status_code=500,
                            detail=f"{type(exc).__name__}: {exc}")


@app.get("/api/evolution")
def evolution(run_id: str = Query(..., description="run_id，例如 run_015")):
    """视图二（状态演化）的全部数据 —— 直接来自 view_evolution_data()。

    曲线的数据源只有一个：run 存档里的 history 字段；本接口不取数、不重算。
    """
    try:
        return view_evolution_data(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:              # 失败显式暴露，不伪装成功
        raise HTTPException(status_code=500,
                            detail=f"{type(exc).__name__}: {exc}")


@app.get("/api/evidence")
def evidence(run_id: str = Query(..., description="run_id，例如 run_082")):
    """视图三（证据空间）的全部数据 —— 直接来自 B07.views.view_evidence_data()。

    只读 run 存档里的 evidence_ledger：不调 LLM、不跑 B06、不取天气、
    不检索知识库、不重查 B02、不重跑判据。
    """
    try:
        return view_evidence_data(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:              # 失败显式暴露，不伪装成功
        raise HTTPException(status_code=500,
                            detail=f"{type(exc).__name__}: {exc}")


@app.get("/api/evolution/compare")
def evolution_compare(a: str = Query(..., description="第一个 run_id"),
                      b: str = Query(..., description="第二个 run_id")):
    """视图二的 S1↔S2 并排对比（相对时间轴）。"""
    try:
        return compare_data(a, b)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500,
                            detail=f"{type(exc).__name__}: {exc}")


@app.get("/api/compare")
def compare(a: str = Query(..., description="第一个 run_id（对比 A）"),
            b: str = Query(..., description="第二个 run_id（对比 B）")):
    """视图四（场景回放与对比）的全部数据。

    直接来自 B07.views.view_compare_data() → B07.views_diff.diff_runs()：
    **只读存档**，不调 LLM、不跑 B06、不取天气、不检索知识库、不重跑判据。

    ★ 回放（发起新运行）**不在** Web 层里 —— 回放器是 B07/replay.py，
      本接口只做对比与归因。
    """
    try:
        return view_compare_data(a, b)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:              # 失败显式暴露，不伪装成功
        raise HTTPException(status_code=500,
                            detail=f"{type(exc).__name__}: {exc}")


def main(argv=None) -> int:
    import uvicorn

    ap = argparse.ArgumentParser(
        description="B07 视图 Web 版（视图一 + 视图二 + 视图三）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8010)
    args = ap.parse_args(argv)
    print(f"视图 Web：http://{args.host}:{args.port}/  "
          f"（只读 run 存档，不调 LLM / 不跑模型）")
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
