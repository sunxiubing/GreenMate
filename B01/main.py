"""GreenMate B01 — 大棚监测最小可运行版本。

Step 1: 最小 FastAPI + HTML 页面，浏览器能打开。
Step 2: 生成并显示 temperature / humidity / light，标注 SIMULATED，可切换状态。
Step 4: 把【当前大棚状态 + 用户问题】一起发给 LLM，做同题 A/B 对照。

所有传感器数值均为模拟数据（SIMULATED），未接入任何真实硬件。

B01 完成标准：同一个问题，只改变当前状态，AI 的回答能合理变化，
并能指出是哪些状态变化导致了判断变化。
"""

import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

# 启动日志含中文。输出被重定向到文件或管道时 Windows 会退回 GBK，日志就不成人话了。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import llm

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    llm.log_config()
    yield


app = FastAPI(title="GreenMate B01", version="0.5.0", lifespan=lifespan)

# --------------------------------------------------------------------------
# 预设状态：A / B 两组，页面上的「加载预设 A/B」按钮用它填充输入框
#
# 数值只是预设，不是唯一可用的取值——页面上的输入框可以随便改。
# 预设本身刻意让差异足够大（温差 12°C、湿度差 30%、光照差 10000 lux），
# 这样默认情况下如果 A 和 B 的回答不一样，原因只可能是状态本身。
# --------------------------------------------------------------------------
PRESET_STATES = {
    "A": {"temperature": 24.0, "humidity": 65.0, "light": 12000},
    "B": {"temperature": 36.0, "humidity": 35.0, "light": 22000},
}

UNITS = {"temperature": "°C", "humidity": "%", "light": "lux"}

# 允许的数值范围。这份定义同时喂给后端校验和前端（GET /api/limits），
# 避免两边各写一套规则、改一处忘一处。
LIMITS = {
    "temperature": {"min": -50.0, "max": 80.0, "unit": "°C", "label": "温度"},
    "humidity": {"min": 0.0, "max": 100.0, "unit": "%", "label": "湿度"},
    "light": {"min": 0.0, "max": 200000.0, "unit": "lux", "label": "光照"},
}

_current = {"id": "A"}


def build_state(state_id: str, values: dict) -> dict:
    """把一组数值包装成带标注的完整状态。"""
    return {
        "source": "SIMULATED",
        "state_id": state_id,
        "label": f"State {state_id}",
        "temperature": values["temperature"],
        "humidity": values["humidity"],
        "light": values["light"],
        "units": dict(UNITS),
    }


def state_for(state_id: str) -> dict:
    return build_state(state_id, PRESET_STATES[state_id])


def all_states() -> list[dict]:
    return [state_for(key) for key in PRESET_STATES]


def current_state() -> dict:
    return state_for(_current["id"])


@app.get("/")
def index():
    """Step 1：返回页面。

    带 no-cache：页面改完后浏览器仍可能拿旧副本，表现出来就是"我明明改了页面却
    没变"。no-cache 只要求每次回源校验，没变照样走 304，不损失效率。
    """
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/state")
def get_state():
    """Step 2：返回当前状态。"""
    return current_state()


@app.get("/api/states")
def get_states():
    """Step 2：返回预设状态，页面「加载预设 A/B」用它填充输入框。"""
    return all_states()


@app.get("/api/limits")
def get_limits():
    """返回各字段的允许范围，页面用它做前端预校验并设置输入框 min/max。"""
    return LIMITS


@app.post("/api/state/switch")
def switch_state():
    """Step 2：在 A / B 之间切换当前状态。"""
    _current["id"] = "B" if _current["id"] == "A" else "A"
    return current_state()


# --------------------------------------------------------------------------
# Step 4：状态进入上下文
# --------------------------------------------------------------------------


class AskRequest(BaseModel):
    question: str


class PanelState(BaseModel):
    """页面上一个面板的数值。这里只做类型转换，范围校验见 check_state()。"""

    id: str
    temperature: float
    humidity: float
    light: float


def check_state(state: PanelState) -> None:
    """范围校验。刻意手写而不用 pydantic 的 ge/le——那些报错是英文列表，
    页面拿到只能显示成 [object Object]，说不出到底是哪个值错了。"""
    for key, limit in LIMITS.items():
        value = getattr(state, key)
        if not (limit["min"] <= value <= limit["max"]):
            raise HTTPException(
                status_code=422,
                detail=f"State {state.id} 的{limit['label']}（{key}）为 {value:g}，"
                       f"超出允许范围 {limit['min']:g} ~ {limit['max']:g} {limit['unit']}",
            )


class CompareRequest(BaseModel):
    question: str
    # 不传则用预设 A/B，方便脚本直接调用
    states: list[PanelState] | None = None


def _clean(question: str) -> str:
    question = question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")
    return question


@app.post("/api/ask")
def ask(req: AskRequest):
    """用【当前状态 + 用户问题】向 LLM 提问。"""
    question = _clean(req.question)
    state = current_state()
    try:
        result = llm.ask(question, state)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="LLM 调用失败：" + llm.describe_exception(exc))
    return {**result, "question": question, "state": state}


@app.post("/api/compare")
def compare(req: CompareRequest):
    """同题 A/B 对照：同一个问题，分别在 State A 和 State B 下各问一次。

    这是 B01 的核心验收手段。问题完全相同，系统提示词完全相同，
    唯一变量是注入的状态——所以两次回答的差异只可能来自状态。
    数值由页面传来（输入框可编辑），不传则回落到预设。
    两个请求并发发送，某一边失败不影响另一边的结果。
    """
    question = _clean(req.question)

    if req.states is None:
        states = all_states()
    else:
        if len(req.states) != 2:
            raise HTTPException(status_code=400, detail="A/B 对照需要恰好 2 组状态")
        ids = [s.id for s in req.states]
        if len(set(ids)) != len(ids):
            raise HTTPException(status_code=400, detail="两组状态的 id 不能重复")
        for s in req.states:
            check_state(s)
        states = [
            build_state(s.id, {"temperature": s.temperature, "humidity": s.humidity, "light": s.light})
            for s in req.states
        ]

    def run(state: dict) -> dict:
        try:
            result = llm.ask(question, state)
            return {"state": state, "answer": result["answer"], "context": result["context"],
                    "model": result["model"]}
        except Exception as exc:
            return {"state": state, "error": llm.describe_exception(exc)}

    with ThreadPoolExecutor(max_workers=len(states)) as pool:
        results = list(pool.map(run, states))

    payload = {"question": question, "results": results}

    # 两侧全部失败时，HTTP 状态码也必须失败。
    #
    # 只把 error 塞进 200 的 body 里是不够的：curl -f、监控探针、CI 断言全都只
    # 看状态码，200 会让它们认为一切正常——这就是 Step 5 要防的"伪装成功"。
    # 而只返回 502 不返回 results 又会丢掉"哪一侧、因为什么失败"，
    # 所以这里状态码和 body 都要，前端两条路径共用同一套渲染。
    if all("error" in r for r in results):
        return JSONResponse(status_code=502, content=payload)

    return payload


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
