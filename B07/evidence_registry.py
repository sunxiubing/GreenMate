"""B07 S4-0 —— 统一证据身份层（统一"来源 / 时间 / 状态 / 结果 / 错误"五项最小语义）

这一层只做一件事：**让每一种信息拥有自己的身份**。

每条证据至少能回答五个问题：

  我是谁       → source_type + source
  我从哪里来   → source（以及 ext 里的 provider / model_version / citation）
  我什么时候取得 → time
  我现在是否可用 → status（ok / unavailable / error）
  我提供了什么结果 → result

设计原则（来自 S4 文档）：

  - **统一的是最小语义，不是业务字段**。status / source / time / result / error
    五个字段各来源一致；业务字段（天气的 provider、知识的 citation、
    模型的 model_version）原样放进 ext，**不强行压平成同一批字段**。
  - **失败必须显式**。status != ok 时 result 必须是 None 且 error 必须非空，
    不允许出现"失败但没有原因"的记录。
  - **调用过 ≠ 采用过**。工具被调用不代表它的结果进入了本次判断。
    ToolLedger 用 adopted 标记区分这两件事。

硬约束（本轮只新增能力，不接入任何既有流程）：

  - 不改 B01 / B02 / B03 / B04 / B05 / B06 任何文件
  - 不改 B07/judgement.py / explain.py / runs.py / weather.py / scenarios.py
  - 不改 SYSTEM_PROMPT、不改固定问题、不改判据阈值、不重训模型
  - 本模块**本身不联网、不调 LLM**：它只做"识别 + 包装 + 校验"。
    自检入口为了拿到真实取值，会去调既有接口（B03 天气 / B04 检索 /
    B06 推理 / B02 状态），这些调用都走既有代码路径，本模块不新增任何
    数据来源、不新增网络调用、不调 LLM。

用法：

    from B07.evidence_registry import (
        make_tool_result, make_evidence, ToolLedger, EvidenceSet,
        from_b03_weather, from_b04_knowledge, from_b06_model, from_b02_state,
    )

    tr = make_tool_result(source="B03/Open-Meteo", status="ok", data=payload)
    ev = from_b03_weather(tr)          # 已包装成证据记录
    ledger = ToolLedger()
    ledger.record("get_weather_forecast", {"location": "河北固安"},
                  status="ok", result_digest=...)
    ledger.adopt("get_weather_forecast", result_digest=...)

自检：

    python -m B07.evidence_registry
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# --------------------------------------------------------------------------
# 1. 常量与状态校验
# --------------------------------------------------------------------------

EVIDENCE_STATUS_OK = "ok"
EVIDENCE_STATUS_UNAVAILABLE = "unavailable"
EVIDENCE_STATUS_ERROR = "error"

#: status 的全部合法取值，出现第四种就是错的
EVIDENCE_STATUSES = (
    EVIDENCE_STATUS_OK,
    EVIDENCE_STATUS_UNAVAILABLE,
    EVIDENCE_STATUS_ERROR,
)

#: 证据记录必须至少保留的字段
EVIDENCE_REQUIRED_FIELDS = ("source_type", "source", "time", "status", "result", "error")

#: source_type 取值
SOURCE_TYPE_B03_WEATHER = "b03_weather"
SOURCE_TYPE_B04_KNOWLEDGE = "b04_knowledge"
SOURCE_TYPE_B06_MODEL = "b06_model"
SOURCE_TYPE_SIMULATED = "simulated"       # B02 模拟数据


class EvidenceValidationError(ValueError):
    """证据结构非法。直接抛出，不静默修正。"""


def validate_status(status: str) -> str:
    """status 只能是 ok / unavailable / error 三选一，出现第四种就抛异常。"""
    if status not in EVIDENCE_STATUSES:
        raise EvidenceValidationError(
            f"status 只能是 {list(EVIDENCE_STATUSES)} 三选一，收到 {status!r}"
        )
    return status


def _now_iso() -> str:
    """当前 UTC 时间（isoformat）。"""
    return datetime.now(timezone.utc).isoformat()


def _digest_of(payload) -> str:
    """给工具结果算一个稳定摘要，用于台账里核对"采用的那份是不是这一份"。"""
    try:
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        blob = repr(payload)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# 2. 工具结果（最小公共字段）
# --------------------------------------------------------------------------

def make_tool_result(source: str, status: str, data, error=None, time=None) -> dict:
    """构造工具结果：{status, source, time, data, error}。

    Args:
        source: 来源标识（如 "B03/Open-Meteo"、"B02/environment_samples"）。
        status: ok / unavailable / error 三选一。
        data:   成功时的结果载荷；status != ok 时必须是 None。
        error:  失败原因；status == "ok" 时必须是 None；status != ok 时必须非空。
        time:   取得时间（isoformat）；缺省用当前 UTC 时间。显式传入便于复现。

    Returns:
        dict，键固定为 status / source / time / data / error。

    Raises:
        EvidenceValidationError: status 非法，或 ok 与非 ok 的字段约束被破坏。
    """
    validate_status(status)

    if not source or not str(source).strip():
        raise EvidenceValidationError("source 不能为空 —— 证据必须能回答'我来自哪里'")

    if status == EVIDENCE_STATUS_OK:
        if error is not None:
            raise EvidenceValidationError(
                f"status=ok 时 error 必须为 None，收到 {error!r}"
            )
    else:
        if data is not None:
            raise EvidenceValidationError(
                f"status={status} 时 data 必须为 None，收到 {data!r}"
                "（失败记录不许带结果）"
            )
        if not error or not str(error).strip():
            raise EvidenceValidationError(
                f"status={status} 时 error 必须非空 —— 不允许'失败但没有原因'"
            )

    return {
        "status": status,
        "source": str(source),
        "time": time if time else _now_iso(),
        "data": data,
        "error": error,
    }


# --------------------------------------------------------------------------
# 3. 证据记录
# --------------------------------------------------------------------------

def make_evidence(tool_result: dict, source_type: str, ext=None) -> dict:
    """由工具结果构造证据记录。

    Args:
        tool_result: make_tool_result() 的产物。
        source_type: 证据种类（b03_weather / b04_knowledge / b06_model / simulated ...）。
        ext:        各源扩展信息，原样保留，不压平成统一业务字段。

    Returns:
        dict，键固定为 source_type / source / time / status / result / error / ext。

    Raises:
        EvidenceValidationError: 输入结构不合法。
    """
    if not isinstance(tool_result, dict):
        raise EvidenceValidationError(
            f"tool_result 必须是 dict，收到 {type(tool_result).__name__}"
        )
    missing = [k for k in ("status", "source", "time", "data", "error")
               if k not in tool_result]
    if missing:
        raise EvidenceValidationError(
            f"tool_result 缺少字段：{missing}（必须由 make_tool_result 生成）"
        )

    status = validate_status(tool_result["status"])

    if not source_type or not str(source_type).strip():
        raise EvidenceValidationError("source_type 不能为空 —— 证据必须能回答'我是什么'")

    # 扩展信息原样保留：只做浅拷贝，不改内容、不做字段统一
    ext_copy = dict(ext) if isinstance(ext, dict) else ({} if ext is None else ext)

    return {
        "source_type": str(source_type),
        "source": tool_result["source"],
        "time": tool_result["time"],
        "status": status,
        "result": tool_result["data"],
        "error": tool_result["error"],
        "ext": ext_copy,
    }


# --------------------------------------------------------------------------
# 4. 工具调用台账
# --------------------------------------------------------------------------

class ToolLedger:
    """工具调用台账：记录每一次工具调用，并区分"调用过"与"被本次判断采用"。

    adopted 的意义：S4 第四步明确要求能区分这两件事。
    一个工具可能被调用但结果并未进入判断（例如取到了天气，但判据用的是
    相对基线差分，那份基线天气就只是"取到过"）。台账把两者分开记。
    """

    def __init__(self) -> None:
        self._records: list[dict] = []

    # ---- 写入 ----

    def record(self, tool_name: str, arguments=None, status: str = EVIDENCE_STATUS_OK,
               started_at=None, finished_at=None, duration_ms=None,
               result_digest=None, error=None) -> dict:
        """记一条工具调用。

        Args:
            tool_name:    工具名。
            arguments:    调用参数，原样保留。
            status:       ok / unavailable / error 三选一。
            started_at:   开始时间（isoformat）。
            finished_at:  结束时间（isoformat）。
            duration_ms:  耗时（毫秒）；不给就用 started/finished 推算。
            result_digest: 结果摘要（一般用 digest_of() 算），用于 adopt 时核对。
            error:        失败原因（S5-1：status != ok 时必须写明，
                          不许"失败但没有原因"）。

        Returns:
            记入的那条记录（dict）。
        """
        validate_status(status)
        if not tool_name or not str(tool_name).strip():
            raise EvidenceValidationError("tool_name 不能为空")

        if status != EVIDENCE_STATUS_OK and not (error and str(error).strip()):
            raise EvidenceValidationError(
                f"status={status} 时 error 必须非空 —— 不允许'失败但没有原因'"
            )

        if duration_ms is None and started_at and finished_at:
            try:
                t0 = datetime.fromisoformat(str(started_at))
                t1 = datetime.fromisoformat(str(finished_at))
                duration_ms = round((t1 - t0).total_seconds() * 1000.0, 3)
            except (TypeError, ValueError):
                duration_ms = None

        rec = {
            "tool_name": str(tool_name),
            "arguments": arguments,
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
            "adopted": False,
            "result_digest": result_digest,
            "error": error,
        }
        self._records.append(rec)
        return rec

    def adopt(self, tool_name: str, result_digest=None) -> dict:
        """把某次工具调用标记为"被本次判断采用"。

        标记的是该工具名下**最近一条尚未被采用**的记录。

        Args:
            tool_name:    工具名。
            result_digest: 若给出，必须与该记录登记的 result_digest 一致，
                           否则说明"采用的"和"记录的"不是同一份结果 —— 直接报错。

        Returns:
            被标记的记录（dict）。

        Raises:
            EvidenceValidationError: 找不到可采用的记录，或摘要对不上。
        """
        candidates = [r for r in self._records
                      if r["tool_name"] == tool_name and not r["adopted"]]
        if not candidates:
            raise EvidenceValidationError(
                f"台账里没有可采用的 {tool_name!r} 调用记录"
                f"（共 {len(self._records)} 条记录）"
            )
        rec = candidates[-1]
        if result_digest is not None and rec.get("result_digest") is not None:
            if result_digest != rec.get("result_digest"):
                raise EvidenceValidationError(
                    f"adopt 的摘要 {result_digest!r} 与台账登记的 "
                    f"{rec.get('result_digest')!r} 不一致 —— 用的不是同一份结果"
                )
        rec["adopted"] = True
        if result_digest is not None:
            rec["result_digest"] = result_digest
        return rec

    # ---- 查询 ----

    def called_but_not_adopted(self) -> list[dict]:
        """调用过、但结果没被本次判断采用的记录。"""
        return [r for r in self._records if not r["adopted"]]

    def adopted_only(self) -> list[dict]:
        """结果被本次判断采用的记录。"""
        return [r for r in self._records if r["adopted"]]

    def to_dict(self) -> dict:
        """整个台账的可序列化视图。"""
        return {
            "records": [dict(r) for r in self._records],
            "counts": {
                "total": len(self._records),
                "adopted": len(self.adopted_only()),
                "called_but_not_adopted": len(self.called_but_not_adopted()),
            },
        }


# --------------------------------------------------------------------------
# 5. 证据集合
# --------------------------------------------------------------------------

class EvidenceSet:
    """本次分析用到的全部证据。

    提供按种类分组、可用/不可用分列，以及可序列化视图。
    """

    def __init__(self, evidences=None) -> None:
        self._evidences: list[dict] = []
        for ev in evidences or []:
            self.add(ev)

    def add(self, evidence: dict) -> dict:
        """加入一条证据。"""
        if not isinstance(evidence, dict):
            raise EvidenceValidationError(
                f"evidence 必须是 dict，收到 {type(evidence).__name__}"
            )
        missing = [k for k in EVIDENCE_REQUIRED_FIELDS if k not in evidence]
        if missing:
            raise EvidenceValidationError(
                f"evidence 缺少必需字段：{missing}"
            )
        validate_status(evidence["status"])
        self._evidences.append(evidence)
        return evidence

    def extend(self, evidences) -> None:
        for ev in evidences or []:
            self.add(ev)

    # ---- 查询 ----

    def all(self) -> list[dict]:
        return list(self._evidences)

    def by_source_type(self) -> dict:
        """{source_type: [evidence, ...]}"""
        out: dict[str, list[dict]] = {}
        for ev in self._evidences:
            out.setdefault(ev["source_type"], []).append(ev)
        return out

    def available_sources(self) -> list[dict]:
        """status == ok 的证据。"""
        return [ev for ev in self._evidences if ev["status"] == EVIDENCE_STATUS_OK]

    def unavailable_sources(self) -> list[dict]:
        """status != ok 的证据（unavailable / error）。"""
        return [ev for ev in self._evidences if ev["status"] != EVIDENCE_STATUS_OK]

    def to_dict(self) -> dict:
        """可序列化视图。"""
        return {
            "evidences": [dict(ev) for ev in self._evidences],
            "counts": {
                "total": len(self._evidences),
                "available": len(self.available_sources()),
                "unavailable": len(self.unavailable_sources()),
            },
            "by_source_type": {
                k: len(v) for k, v in self.by_source_type().items()
            },
        }


# --------------------------------------------------------------------------
# 6. 适配器：把既有返回值包装成证据（不改被包装的一方）
# --------------------------------------------------------------------------
#
# 适配器只做"识别 + 包装"：不改写上游返回值里的任何业务字段，
# 也不在上游取不到时编造替代数据 —— 取不到就如实标 unavailable / error。

def _tool_result_from_status(source: str, status, data, error, time=None) -> dict:
    """按上游给的 status 组装工具结果，缺省按 ok/error 兜底，但不编造数据。"""
    if status is None:
        status = EVIDENCE_STATUS_OK if data is not None else EVIDENCE_STATUS_ERROR
    if status == EVIDENCE_STATUS_OK:
        if error:
            return make_tool_result(source, EVIDENCE_STATUS_ERROR, None, error, time)
        return make_tool_result(source, EVIDENCE_STATUS_OK, data, None, time)
    # 非 ok：data 必须丢弃（失败记录不带结果），error 必须有内容
    err = error if error and str(error).strip() else f"{source} 不可用（上游未给出原因）"
    return make_tool_result(source, status, None, err, time)


def from_b03_weather(raw_weather: dict) -> dict:
    """B03 天气 → 证据。保留 provider / forecast_time。

    支持两种输入：
      1. B05.get_weather_forecast() 的返回：
         {"status","provider","retrieved_at","data":{...},"error"}
      2. 裸天气载荷：{"location","forecast_window","timestamp",...}

    ext 保留：provider / forecast_time（= data.timestamp）/ forecast_window /
    location / retrieved_at。
    """
    if not isinstance(raw_weather, dict) or not raw_weather:
        return make_evidence(
            make_tool_result("B03/Open-Meteo", EVIDENCE_STATUS_UNAVAILABLE, None,
                             "天气返回值为空"),
            SOURCE_TYPE_B03_WEATHER,
        )

    if "data" in raw_weather:
        status = raw_weather.get("status")
        data = raw_weather.get("data")
        error = raw_weather.get("error")
        provider = raw_weather.get("provider") or "Open-Meteo"
        retrieved_at = raw_weather.get("retrieved_at")
    else:
        status = EVIDENCE_STATUS_OK
        data = raw_weather
        error = None
        provider = raw_weather.get("provider") or "Open-Meteo"
        retrieved_at = None

    tr = _tool_result_from_status("B03/Open-Meteo", status, data, error, retrieved_at)

    payload = tr["data"] if isinstance(tr["data"], dict) else {}
    ext = {
        "provider": provider,
        "forecast_time": payload.get("timestamp"),
        "forecast_window": payload.get("forecast_window"),
        "location": payload.get("location"),
        "retrieved_at": retrieved_at,
    }
    return make_evidence(tr, SOURCE_TYPE_B03_WEATHER, ext)


def from_b04_knowledge(raw_kb) -> dict:
    """B04 知识检索 → 证据。保留 citation（chunk_id / source_id / score）。

    支持输入：
      - 单个 chunk：{"chunk_id","source_id","score",...}
      - 包装形式：{"results":[...]} 或 {"chunks":[...]}
      - 列表：[chunk, ...]

    ext 保留：citation（主命中 chunk 的 chunk_id/source_id/score）、
    retrieved（全部命中的 id 三元组列表）、question。
    """
    source = "B04/knowledge_base"

    # ---- 归一成列表 ----
    chunks = None
    question = None
    if isinstance(raw_kb, list):
        chunks = raw_kb
    elif isinstance(raw_kb, dict):
        question = raw_kb.get("question")
        if isinstance(raw_kb.get("results"), list):
            chunks = raw_kb["results"]
        elif isinstance(raw_kb.get("chunks"), list):
            chunks = raw_kb["chunks"]
        elif raw_kb.get("chunk_id"):
            chunks = [raw_kb]
        else:
            chunks = []
    else:
        chunks = []

    chunks = [c for c in chunks if isinstance(c, dict)]

    if not chunks:
        return make_evidence(
            make_tool_result(source, EVIDENCE_STATUS_UNAVAILABLE, None,
                             "知识检索没有召回任何切片"),
            SOURCE_TYPE_B04_KNOWLEDGE,
            {"citation": None, "retrieved": [], "question": question},
        )

    top = chunks[0]
    citation = {
        "chunk_id": top.get("chunk_id"),
        "source_id": top.get("source_id"),
        "score": top.get("score"),
    }
    retrieved = [
        {
            "chunk_id": c.get("chunk_id"),
            "source_id": c.get("source_id"),
            "score": c.get("score"),
        }
        for c in chunks
    ]

    tr = make_tool_result(source, EVIDENCE_STATUS_OK, chunks, None)
    return make_evidence(tr, SOURCE_TYPE_B04_KNOWLEDGE, {
        "citation": citation,
        "retrieved": retrieved,
        "question": question,
    })


def from_b06_model(model_output: dict) -> dict:
    """B06 模型输出 → 证据。保留 model_version。

    ext 保留：model_version / predicted_attention / confidence /
    window_id / feature_count / errors。

    取不到模型输出 → unavailable；模型输出里没有 model_version → error
    （无法回答"我是谁"）。
    """
    source = "B06/inference"

    if not isinstance(model_output, dict) or not model_output:
        return make_evidence(
            make_tool_result(source, EVIDENCE_STATUS_UNAVAILABLE, None,
                             "模型输出为空"),
            SOURCE_TYPE_B06_MODEL,
        )

    model_version = model_output.get("model_version")
    if not model_version or not str(model_version).strip():
        return make_evidence(
            make_tool_result(source, EVIDENCE_STATUS_ERROR, None,
                             "模型输出缺少 model_version，无法确认证据身份"),
            SOURCE_TYPE_B06_MODEL,
        )

    tr = make_tool_result(source, EVIDENCE_STATUS_OK, model_output, None)
    return make_evidence(tr, SOURCE_TYPE_B06_MODEL, {
        "model_version": model_version,
        "predicted_attention": model_output.get("predicted_attention"),
        "confidence": model_output.get("confidence"),
        "window_id": model_output.get("window_id"),
        "feature_count": model_output.get("feature_count"),
        "errors": list(model_output.get("errors") or []),
    })


def from_b02_state(state: dict) -> dict:
    """B02 当前状态（模拟数据）→ 证据。source_type = "simulated"。

    ext 保留：sim_scenario / sample_source_type（B02 样本自带的 source_type）。
    取不到 → unavailable。
    """
    source = "B02/environment_samples"

    if not isinstance(state, dict) or not state:
        return make_evidence(
            make_tool_result(source, EVIDENCE_STATUS_UNAVAILABLE, None,
                             "当前状态取不到"),
            SOURCE_TYPE_SIMULATED,
        )

    # 兼容两种形态：裸样本 dict，或 B05.get_current_environment() 的包装
    if "data" in state and isinstance(state.get("data"), dict):
        st = state["data"]
        status = state.get("status")
        error = state.get("error")
    else:
        st = state
        status = EVIDENCE_STATUS_OK
        error = None

    # time = 取得时间（从库里取出来的时刻），不是样本自身的观测时刻；
    # 观测时刻留在 result.timestamp / ext.observed_at，两者不混。
    tr = _tool_result_from_status(source, status, st, error)

    payload = tr["data"] if isinstance(tr["data"], dict) else {}
    ext = {
        "sim_scenario": payload.get("sim_scenario"),
        "sample_source_type": payload.get("source_type"),
        "observed_at": payload.get("timestamp"),
    }
    return make_evidence(tr, SOURCE_TYPE_SIMULATED, ext)


# --------------------------------------------------------------------------
# 自检入口
# --------------------------------------------------------------------------

def _fmt(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _real_b03_weather() -> tuple[dict, str]:
    """取一份真实的 B03 天气返回值。

    先走真实 API；网络不通时退回存档里已记录的实测值（仍是真实取值，
    但会在说明里标出来源）。
    """
    try:
        from B05.test_functions import get_weather_forecast
        raw = get_weather_forecast("河北固安", forecast_window="6h")
        if raw.get("status") == "ok":
            return raw, "实时调用 B05.get_weather_forecast（Open-Meteo 真实 API）"
        return raw, f"实时调用失败（status={raw.get('status')}），使用原样返回"
    except Exception as e:  # noqa: BLE001
        raw = {"status": "error", "provider": "Open-Meteo", "retrieved_at": "",
               "data": None, "error": f"{type(e).__name__}: {e}"}
        return raw, "实时调用抛异常，使用异常原样记录"


def _archived_b03_weather() -> tuple[dict, str]:
    """存档里已记录的 B03 实测天气（run_017 / run_035 的 external_conditions）。"""
    runs = _PROJECT_ROOT / "B07" / "runs"
    for run_id in ("run_035", "run_017"):
        p = runs / run_id / "run.json"
        if not p.exists():
            continue
        arch = json.loads(p.read_text(encoding="utf-8"))
        ext = arch.get("external_conditions") or {}
        fp = ext.get("forecast_point")
        if not fp:
            continue
        return {
            "status": "ok",
            "provider": "Open-Meteo",
            "retrieved_at": (arch.get("created_at") or ""),
            "data": {
                "location": ext.get("location"),
                "forecast_window": ext.get("forecast_window"),
                "timestamp": fp.get("timestamp"),
                "temperature": fp.get("temperature"),
                "humidity": fp.get("humidity"),
                "precipitation_probability": fp.get("precipitation_probability"),
                "cloud_cover": fp.get("cloud_cover"),
                "solar_radiation": fp.get("solar_radiation"),
                "weather_code": fp.get("weather_code"),
                "weather_description": fp.get("weather_description"),
            },
            "error": None,
        }, f"取自存档 {run_id}（已记录的实测天气）"
    return ({"status": "unavailable", "provider": "Open-Meteo", "retrieved_at": "",
             "data": None, "error": "存档里没有可用的天气记录"},
            "存档里没有可用的天气记录")


class _QuietStdout:
    """B04.retrieve 在 import 时会 sys.stdout.reconfigure，需要一个带该方法的替身。"""

    def __init__(self, sink) -> None:
        self._sink = sink

    def reconfigure(self, **kwargs):
        return None

    def write(self, s):
        return self._sink.write(s)

    def flush(self):
        return self._sink.flush()


def _real_b04_knowledge() -> tuple[list | None, str]:
    """取一份真实的 B04 检索结果。"""
    import contextlib
    import io

    try:
        buf = io.StringIO()
        quiet = _QuietStdout(buf)
        # B04/retrieve.py 在 import 时会 sys.stdout.reconfigure(...)，并顺带把
        # KB_QUESTIONS 全跑一遍打印 —— 这里全程静音，不污染自检输出。
        with contextlib.redirect_stdout(quiet):
            from B04.retrieve import retrieve
            results = retrieve("什么是 SIMULATED 数据", top_k=3)
        if results:
            return results, "实时调用 B04.retrieve.retrieve（本地 TF-IDF 检索）"
        return None, "B04 检索没有召回结果"
    except Exception as e:  # noqa: BLE001
        return None, f"B04 检索失败：{type(e).__name__}: {e}"


def _real_b02_state() -> tuple[dict | None, str]:
    """取一份真实的 B02 当前状态。"""
    try:
        from B02.api.history import get_current_environment
        from B07.evidence import _sample_to_dict
        s = get_current_environment()
        if s is None:
            return None, "B02 取不到当前状态"
        return _sample_to_dict(s), "实时调用 B02.api.history.get_current_environment"
    except Exception as e:  # noqa: BLE001
        return None, f"B02 取状态失败：{type(e).__name__}: {e}"


def _real_b06_model_output() -> tuple[dict | None, str]:
    """取一份真实的 B06 模型输出。"""
    try:
        from datetime import datetime as _dt
        from B07.evidence import build_evidence
        from B07.predict import predict_for_run
        ev = build_evidence(_dt(2026, 10, 21, 16, 0))
        bundle = predict_for_run(ev)
        mo = bundle.get("model_output")
        if mo:
            return mo, "实时推理 B07.predict.predict_for_run（B06 模型 b06-20261020-v1）"
    except Exception as e:  # noqa: BLE001
        return None, f"实时推理失败：{type(e).__name__}: {e}"
    return None, "实时推理未产出 model_output"


def _archived_b06_model_output() -> tuple[dict | None, str]:
    runs = _PROJECT_ROOT / "B07" / "runs"
    for run_id in ("run_035", "run_017", "run_001"):
        p = runs / run_id / "run.json"
        if not p.exists():
            continue
        arch = json.loads(p.read_text(encoding="utf-8"))
        mo = arch.get("model_output")
        if mo:
            return mo, f"取自存档 {run_id}（已记录的模型输出）"
    return None, "存档里没有 model_output"


def self_check() -> int:
    """自检：真实取值造证据 + 不可用语义 + 台账区分。"""
    print("=" * 78)
    print("B07 S4-0 统一证据身份层 —— 自检")
    print("=" * 78)

    failures: list[str] = []

    # ---------------- 1. 真实 B03 天气 ----------------
    print("\n[1] 真实 B03 天气 → 证据")
    raw_w, note_w = _real_b03_weather()
    if raw_w.get("status") != "ok":
        raw_w, note_w = _archived_b03_weather()
    print(f"  取值来源：{note_w}")
    ev_weather = from_b03_weather(raw_w)
    print(_fmt(ev_weather))
    if ev_weather["status"] != "ok":
        failures.append("真实天气证据没有拿到 ok 状态")
    if not ev_weather["ext"].get("provider"):
        failures.append("天气证据丢了 provider")
    if not ev_weather["ext"].get("forecast_time"):
        failures.append("天气证据丢了 forecast_time")

    # ---------------- 2. 真实 B06 模型输出 ----------------
    print("\n[2] 真实 B06 模型输出 → 证据")
    mo, note_m = _real_b06_model_output()
    if not mo:
        mo, note_m = _archived_b06_model_output()
    print(f"  取值来源：{note_m}")
    ev_model = from_b06_model(mo or {})
    print(_fmt(ev_model))
    if ev_model["status"] != "ok":
        failures.append("模型证据没有拿到 ok 状态")
    if not ev_model["ext"].get("model_version"):
        failures.append("模型证据丢了 model_version")

    # ---------------- 3. 天气不可用 ----------------
    print("\n[3] 天气不可用 → 证据（status=unavailable / error 非空 / result 为 None）")
    tr_bad = make_tool_result(
        "B03/Open-Meteo", EVIDENCE_STATUS_UNAVAILABLE, None,
        "weather API timeout after 10s",
        time="2026-10-26T11:00:00+00:00",
    )
    ev_bad = from_b03_weather(tr_bad)
    print(_fmt(ev_bad))
    if ev_bad["status"] != EVIDENCE_STATUS_UNAVAILABLE:
        failures.append("不可用天气的 status 不对")
    if ev_bad["error"] in (None, ""):
        failures.append("不可用天气的 error 为空")
    if ev_bad["result"] is not None:
        failures.append("不可用天气的 result 不为 None")

    # 约束自检：ok 带 error / 非 ok 不带 error / 第四种 status
    for bad_call, why in (
        (lambda: make_tool_result("x", EVIDENCE_STATUS_OK, {"a": 1}, "有错误"),
         "status=ok 却带 error"),
        (lambda: make_tool_result("x", EVIDENCE_STATUS_UNAVAILABLE, {"a": 1}, "原因"),
         "status!=ok 却带 data"),
        (lambda: make_tool_result("x", EVIDENCE_STATUS_UNAVAILABLE, None, ""),
         "status!=ok 却没给原因"),
        (lambda: make_tool_result("x", "partial", {"a": 1}),
         "出现了第四种 status"),
    ):
        try:
            bad_call()
            failures.append(f"约束没拦住：{why}")
        except EvidenceValidationError:
            print(f"  ✅ 约束拦住：{why}")

    # ---------------- 4. 真实 B04 知识检索 ----------------
    print("\n[4] 真实 B04 知识检索 → 证据（保留 citation）")
    kb, note_kb = _real_b04_knowledge()
    print(f"  取值来源：{note_kb}")
    ev_kb = from_b04_knowledge(kb or [])
    print(_fmt(ev_kb))
    if ev_kb["status"] != "ok":
        failures.append("知识证据没有拿到 ok 状态")
    elif not (ev_kb["ext"].get("citation") or {}).get("chunk_id"):
        failures.append("知识证据丢了 citation.chunk_id")

    # ---------------- 5. 真实 B02 当前状态 ----------------
    print("\n[5] 真实 B02 当前状态 → 证据（source_type=simulated）")
    st, note_st = _real_b02_state()
    print(f"  取值来源：{note_st}")
    ev_state = from_b02_state(st or {})
    print(_fmt(ev_state))
    if ev_state["source_type"] != SOURCE_TYPE_SIMULATED:
        failures.append("B02 状态的 source_type 不是 simulated")
    if st and ev_state["status"] != "ok":
        failures.append("B02 状态证据没有拿到 ok 状态")

    # ---------------- 6. ToolLedger ----------------
    print("\n[6] ToolLedger —— 调用过 ≠ 采用过")
    ledger = ToolLedger()
    w_digest = _digest_of(ev_weather["result"])
    m_digest = _digest_of(ev_model["result"])
    k_digest = _digest_of(ev_kb["result"])
    s_digest = _digest_of(ev_state["result"])
    ledger.record("get_weather_forecast",
                  {"location": "河北固安", "forecast_window": "6h"},
                  status=EVIDENCE_STATUS_OK,
                  started_at="2026-10-26T11:00:00+00:00",
                  finished_at="2026-10-26T11:00:01+00:00",
                  result_digest=w_digest)
    ledger.record("retrieve_knowledge", {"question": "什么是 SIMULATED"},
                  status=EVIDENCE_STATUS_OK,
                  started_at="2026-10-26T11:00:02+00:00",
                  finished_at="2026-10-26T11:00:02+00:00",
                  result_digest=k_digest)
    ledger.record("run_model", {"window_id": "s1-live-001"},
                  status=EVIDENCE_STATUS_OK,
                  started_at="2026-10-26T11:00:03+00:00",
                  finished_at="2026-10-26T11:00:04+00:00",
                  result_digest=m_digest)
    ledger.record("get_current_environment", {},
                  status=EVIDENCE_STATUS_OK,
                  started_at="2026-10-26T11:00:05+00:00",
                  finished_at="2026-10-26T11:00:05+00:00",
                  result_digest=s_digest)
    # 本次判断采用了天气、模型、当前状态；知识检索调了但没进入判断
    ledger.adopt("get_weather_forecast", result_digest=w_digest)
    ledger.adopt("run_model", result_digest=m_digest)
    ledger.adopt("get_current_environment", result_digest=s_digest)

    print("  调用过但未被采用：")
    for r in ledger.called_but_not_adopted():
        print(f"    - {r['tool_name']}（status={r['status']}，digest={r['result_digest']}）")
    print("  被本次判断采用：")
    for r in ledger.adopted_only():
        print(f"    - {r['tool_name']}（status={r['status']}，digest={r['result_digest']}）")
    print("  台账汇总：")
    print(_fmt(ledger.to_dict()))

    if len(ledger.called_but_not_adopted()) != 1:
        failures.append("台账没能区分出'调用过但未采用'")
    if len(ledger.adopted_only()) != 3:
        failures.append("台账的采用计数不对")

    # 摘要对不上必须报错
    try:
        ledger.adopt("retrieve_knowledge", result_digest="完全不匹配的摘要")
        failures.append("adopt 摘要对不上却没报错")
    except EvidenceValidationError as e:
        print(f"  ✅ adopt 摘要对不上被拦住：{e}")

    # ---------------- 7. EvidenceSet ----------------
    print("\n[7] EvidenceSet —— 本次分析的证据集合")
    es = EvidenceSet([ev_weather, ev_model, ev_kb, ev_state, ev_bad])
    print("  按 source_type 分组：")
    for k, v in es.by_source_type().items():
        print(f"    {k}: {len(v)} 条")
    print(f"  可用：{len(es.available_sources())} 条；"
          f"不可用：{len(es.unavailable_sources())} 条")
    print(_fmt(es.to_dict()))
    if len(es.available_sources()) != 4:
        failures.append("EvidenceSet 可用计数不对")
    if len(es.unavailable_sources()) != 1:
        failures.append("EvidenceSet 不可用计数不对")

    # ---------------- 汇总 ----------------
    print("\n" + "=" * 78)
    if failures:
        print(f"自检结果：❌ {len(failures)} 项未通过")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("自检结果：✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(self_check())
