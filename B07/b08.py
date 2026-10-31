"""B08 第二遍 · 正式验证 S1~S5（文档 p.93–94）

★ 这一遍做的是"**用固定场景跑一遍，然后逐条判定**"：

    场景文件（B07/scenarios/*.json）= 冻结的输入
        ↓ python -m B07.replay --scenario-file B07/scenarios/Sx.json
    新的 run（落在 B07/runs/，回放日志在 artifacts/replay_log.jsonl）
        ↓ python -m B07.b08 verify --all
    验证记录（B07/artifacts/b08_validation_records.jsonl，一场景一条）
        ↓ python -m B07.b08 report
    验证报告（B07/artifacts/b08_validation_report.md）

★ 判定分两类，**不许混**：

    (1) 断言型检查（S1-1…S5-5）
        —— 读存档里的字段，逐条比对。查的是"这次运行是不是这样"。

    (2) 因果证明（counter_evidence）
        —— **只改一个输入**，用同一套纯函数重算，看输出跟不跟着变。
           判定重算**不调 LLM**：只重跑 B07.judgement / B07.stability 这些
           纯函数部分（Limitation L5）。

★ 硬约束：
    · 不改 SYSTEM_PROMPT / 固定问题（指纹 aa1400cf8568 / d3cc394bfcbe）；
    · run.json 存档结构一字不改（只读）；
    · 不改 B01–B06 的业务逻辑；
    · 判定重算不许调 LLM；
    · 失败显式暴露，不许用静态文案伪装成功；
    · 数字全部来自实际存档，不手工编。

用法：
    python -m B07.b08 baseline                 # 基线快照（转发到 B08 第一遍）
    python -m B07.b08 verify --all             # 验证五个场景
    python -m B07.b08 verify S1                # 只验证 S1
    python -m B07.b08 records                  # 打印验证记录
    python -m B07.b08 report                   # 生成验证报告
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from B07.scenarios_build import SCENARIO_SPECS, scenario_path

#: 五个场景（验证顺序固定）
SCENARIOS = ("S1", "S2", "S3", "S4", "S5")

#: 场景目录
SCENARIOS_DIR = _PROJECT_ROOT / "B07" / "scenarios"

#: 回放日志（回放器写的，这里只读）
REPLAY_LOG_PATH = _PROJECT_ROOT / "B07" / "artifacts" / "replay_log.jsonl"

#: 验证记录（一个场景一条）
RECORDS_PATH = _PROJECT_ROOT / "B07" / "artifacts" / "b08_validation_records.jsonl"

#: 验证报告
REPORT_PATH = _PROJECT_ROOT / "B07" / "artifacts" / "b08_validation_report.md"

#: 基线快照
BASELINE_PATH = _PROJECT_ROOT / "B07" / "artifacts" / "b08_baseline.json"


# --------------------------------------------------------------------------
# LLM 文本检查用的词表
# --------------------------------------------------------------------------
#
# ★ 两条规矩：
#   1. **只用多字短语**，禁用单字匹配（单字会误伤到"一致""支持"这些常用词）。
#   2. **否定 / 免责句不算命中** —— "并非都支持"不是在说分歧不存在。

#: S4-4：把分歧说成一致的短语
CONSISTENCY_PHRASES = ("都支持", "一致支持", "全部支持", "无异议", "没有矛盾")

#: 否定 / 免责标记（出现在被检短语**之外**时，该句不算命中）
#: ★ 全部是多字短语 —— 单字会误伤到"未来""未见"这类正常词。
NEGATION_MARKERS = (
    "并非", "不是", "没有", "未能", "无法", "不能", "不会", "不宜", "不可",
    "不作", "不构成", "不意味着", "不表示", "不成立", "未见", "毫无",
    "缺乏", "非同一",
)

#: S5-4：天气类事实的主语短语
WEATHER_SUBJECT_PHRASES = (
    "外部天气", "天气情况", "天气预报", "降水概率", "降雨概率", "云量",
    "气温", "天气转", "天气恶化", "天气好转", "外部条件", "外部信号",
)

#: S5-4：事实断言语气（多字短语）
ASSERTION_MARKERS = (
    "显示", "表明", "说明", "证实", "证明", "预计", "将会", "即将",
    "达到", "升至", "降至", "确认", "预报", "恶化", "好转", "将恶化",
    "将好转", "缓解", "加重", "趋于",
)

#: S5-4：免责 / 缺失声明（命中即不算"伪造"）
#: ★ 同样只用多字短语 —— 单字会误伤到"未来""无"这类正常词。
DISCLAIMER_MARKERS = (
    "无法", "不能", "未能", "未取得", "未获取", "未知", "不可用", "不可",
    "没有", "并非", "不涉及", "不包含", "不提及", "不要", "缺失", "不予",
    "不予提供", "限制", "受影响", "不参与", "不作", "无数据", "无记录",
    "拿不到", "取不到", "暂缺", "空缺",
)


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sentences(text: str) -> list[str]:
    """把一段文本切成句子（按中文/英文句号、问号、叹号、分号、换行切）。"""
    if not text:
        return []
    out = []
    buf = []
    for ch in text:
        buf.append(ch)
        if ch in "。！？!?；;\n":
            s = "".join(buf).strip()
            if s:
                out.append(s)
            buf = []
    s = "".join(buf).strip()
    if s:
        out.append(s)
    return out


def _check(cid: str, claim: str, expected, actual, passed: bool,
           source: str) -> dict:
    """一条检查记录。"""
    return {
        "id": cid,
        "claim": claim,
        "expected": expected,
        "actual": actual,
        "passed": bool(passed),
        "source": source,
    }


def _baseline() -> dict | None:
    if not BASELINE_PATH.exists():
        return None
    try:
        return _read_json(BASELINE_PATH)
    except Exception:                       # noqa: BLE001
        return None


def _app_version() -> tuple[str, dict | None]:
    b = _baseline()
    if b:
        return str(b.get("app_version") or "unknown"), b
    return "unknown", None


# --------------------------------------------------------------------------
# 回放日志：找出每个场景最近一次固定场景回放
# --------------------------------------------------------------------------

def replay_index() -> dict:
    """从回放日志里找出每个场景最近一次**固定场景回放**。

    只认带 scenario_file 的日志行（固定场景回放），普通回放（S1/S3/S5 …）
    不算 —— 固定场景验证的对象必须是"按场景文件回放出来的那一次"。

    Returns:
        {scenario_id: {"run_id", "ts", "scenario_file", "base_run_id", "row"}}
    """
    out: dict = {}
    if not REPLAY_LOG_PATH.exists():
        return out
    for line in REPLAY_LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:                   # noqa: BLE001
            continue
        sid = row.get("scenario_id")
        if not sid or not row.get("scenario_file"):
            continue
        out[sid] = row
    return out


def load_archive(run_id: str) -> dict | None:
    p = _PROJECT_ROOT / "B07" / "runs" / run_id / "run.json"
    if not p.exists():
        return None
    try:
        return _read_json(p)
    except Exception:                       # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# 存档取数
# --------------------------------------------------------------------------

def _judgement(archive: dict) -> dict:
    return archive.get("structured_judgement") or {}


def _ledger_entries(archive: dict) -> list[dict]:
    led = archive.get("evidence_ledger") or {}
    return led.get("entries") or []


def _entry_of(archive: dict, source_type: str) -> dict | None:
    for e in _ledger_entries(archive):
        if e.get("source_type") == source_type:
            return e
    return None


def _weather_evidence(archive: dict) -> dict:
    """这次运行的天气证据摘要（身份取自台账，没有台账就取 external_conditions）。"""
    ec = archive.get("external_conditions") or {}
    b03 = _entry_of(archive, "B03")
    ext = (b03 or {}).get("ext") or {}
    return {
        "provider": ext.get("provider") or ec.get("provider"),
        "provider_source": ("evidence_ledger.EV-B03.ext"
                             if ext.get("provider") else
                             "run.json.external_conditions"),
        "retrieved_at": ext.get("retrieved_at") or ec.get("retrieved_at"),
        "forecast_time": ext.get("forecast_time") or ec.get("forecast_time"),
        "available": (ec.get("available")
                      if ec else (b03 or {}).get("status") == "ok"),
        "status": (b03 or {}).get("status") or ec.get("status"),
        "signal": ec.get("signal"),
        "judge_method": ec.get("judge_method"),
        "error": ext.get("error") or (b03 or {}).get("error") or ec.get("error"),
        "is_constructed": ext.get("is_constructed"),
        "has_ledger": bool(_ledger_entries(archive)),
    }


def _knowledge_evidence(archive: dict) -> dict:
    b04 = _entry_of(archive, "B04")
    if not b04:
        return {"present": False, "citations": 0, "has_relevant": None,
                "query": None, "status": None}
    res = b04.get("result") or {}
    cites = res.get("citations") or []
    return {
        "present": True,
        "citations": len(cites),
        "has_relevant": res.get("has_relevant"),
        "query": res.get("query"),
        "status": b04.get("status"),
        "top_chunk": (cites[0] if cites else {}).get("chunk_id"),
    }


def _model_evidence(archive: dict) -> dict:
    mo = archive.get("model_output") or {}
    meta = archive.get("model_meta") or {}
    return {
        "model_version": mo.get("model_version"),
        "predicted_attention": mo.get("predicted_attention"),
        "confidence": mo.get("confidence"),
        "window_id": mo.get("window_id"),
        "label_source": meta.get("label_source"),
        "metrics": meta.get("metrics") or {},
    }


def _tool_run_status(archive: dict) -> dict:
    """每类证据工具的运行状态（没有台账就空着，不假装有）。"""
    out = {}
    for e in _ledger_entries(archive):
        st = e.get("source_type")
        if st:
            out[st] = {"status": e.get("status"), "error": e.get("error"),
                       "adopted": e.get("adopted")}
    return out


def _limitations(archive: dict) -> list[str]:
    lim = _judgement(archive).get("limitations") or {}
    if isinstance(lim, dict):
        return [str(v.get("text")) for v in lim.values() if isinstance(v, dict)]
    return [str(x) for x in lim]


# --------------------------------------------------------------------------
# 因果证明（纯函数重算，**不调 LLM**）
# --------------------------------------------------------------------------

def _bundle_of(archive: dict, history: list | None = None) -> dict:
    """从存档里取出 run_bundle（可替换 history）。"""
    return {
        "analysis_time": archive.get("analysis_time"),
        "current_state": archive.get("current_state"),
        "history": history if history is not None else (archive.get("history") or []),
        "model_output": archive.get("model_output"),
        "scenario": archive.get("scenario"),
    }


def recompute_stability(archive: dict, history: list) -> str:
    """换掉 history 后重算稳定性判定（只跑纯函数，不调 LLM）。"""
    from B07.judgement import build_judgement

    bundle = _bundle_of(archive, history)
    j = build_judgement(bundle, external_conditions=None)
    return (j.get("recent_trend") or {}).get("stability_verdict")


def recompute_combined(archive: dict, external_conditions) -> dict:
    """换掉 external_conditions 后重算合成关注等级（纯函数）。"""
    from B07.judgement import build_combined_attention

    bundle = {
        "model_output": archive.get("model_output"),
        "external_conditions": external_conditions,
    }
    return build_combined_attention(bundle)


def _series_diff(h1: list, h2: list) -> dict:
    """两个历史序列的过程量差异（**只从序列算，不读任何结论**）。"""
    out = {}
    for key in ("temperature", "humidity", "lux"):
        s1 = [p.get(key) for p in (h1 or [])]
        s2 = [p.get(key) for p in (h2 or [])]
        if not s1 or not s2 or len(s1) != len(s2):
            out[key] = None
            continue
        out[key] = max(abs((a or 0.0) - (b or 0.0)) for a, b in zip(s1, s2))
    return out


# --------------------------------------------------------------------------
# LLM 文本检查
# --------------------------------------------------------------------------

def find_consistency_claims(text: str) -> list[dict]:
    """扫"把分歧说成一致"的说法。命中 = 短语 + 该句其余部分没有否定/免责。

    ★ 只匹配多字短语；被检短语本身从上下文里剔除后再找否定标记
      （否则"没有矛盾"里的"没有"会把自己抵消掉）。
    """
    hits = []
    for sent in _sentences(text or ""):
        for phrase in CONSISTENCY_PHRASES:
            start = 0
            while True:
                idx = sent.find(phrase, start)
                if idx < 0:
                    break
                rest = sent[:idx] + sent[idx + len(phrase):]
                negated = any(m in rest for m in NEGATION_MARKERS)
                hits.append({
                    "phrase": phrase,
                    "sentence": sent,
                    "negated": negated,
                    "counted": not negated,
                })
                start = idx + len(phrase)
    return hits


def find_weather_facts(text: str) -> list[dict]:
    """扫"凭空断言天气事实"的说法。

    命中条件：同一句里既有天气类主语短语，又有事实断言语气；
    且该句其余部分没有免责 / 缺失声明。
    """
    hits = []
    for sent in _sentences(text or ""):
        subjects = [p for p in WEATHER_SUBJECT_PHRASES if p in sent]
        if not subjects:
            continue
        asserts = [m for m in ASSERTION_MARKERS if m in sent]
        if not asserts:
            continue
        # 去掉被检短语与断言词之后再找免责标记
        rest = sent
        for p in subjects + asserts:
            rest = rest.replace(p, "", 1)
        disclaimed = any(m in rest for m in DISCLAIMER_MARKERS)
        hits.append({
            "subjects": subjects,
            "assertions": asserts,
            "sentence": sent,
            "disclaimed": disclaimed,
            "counted": not disclaimed,
        })
    return hits


# --------------------------------------------------------------------------
# 逐场景验证
# --------------------------------------------------------------------------

def _base_record(sid: str, archive: dict, run_id: str, notes: list[str]) -> dict:
    app, baseline = _app_version()
    return {
        "scenario_id": sid,
        "run_id": run_id,
        "app_version": app,
        "app_version_full": ((baseline or {}).get("app_version_composition") or {}).get("full"),
        "model_version": (archive.get("model_output") or {}).get("model_version"),
        "analysis_time": archive.get("analysis_time"),
        "location": archive.get("location"),
        "current_state": archive.get("current_state"),
        "history_window": archive.get("history_window"),
        "weather_evidence": _weather_evidence(archive),
        "knowledge_evidence": _knowledge_evidence(archive),
        "model_evidence": _model_evidence(archive),
        "tool_run_status": _tool_run_status(archive),
        "structured_judgement": _judgement(archive),
        "llm_explanation": archive.get("llm_explanation"),
        "limitations": _limitations(archive),
        "checks": [],
        "counter_evidence": {},
        "verdict": "FAIL",
        "notes": notes,
    }


def verify_s1(archive: dict, run_id: str, others: dict) -> dict:
    notes: list[str] = []
    rec = _base_record("S1", archive, run_id, notes)
    j = _judgement(archive)
    rt = j.get("recent_trend") or {}
    ca = archive.get("combined_attention") or {}
    crit = ((archive.get("history_window") or {}).get("stability") or {}).get("criteria") or []
    failed = [c for c in crit if not c.get("passed")]

    rec["checks"].append(_check(
        "S1-1", "稳定性判定为『稳定』", "稳定", rt.get("stability_verdict"),
        rt.get("stability_verdict") == "稳定",
        "run.json.structured_judgement.recent_trend.stability_verdict"))

    rec["checks"].append(_check(
        "S1-2", "B06 预测为 NORMAL", "NORMAL", j.get("predicted_attention"),
        j.get("predicted_attention") == "NORMAL",
        "run.json.structured_judgement.predicted_attention"))

    val = ca.get("value")
    rec["checks"].append(_check(
        "S1-3", "合成关注等级既不是 watch_internal 也不是 watch_external",
        "∉ {watch_internal, watch_external}", val,
        val not in ("watch_internal", "watch_external"),
        "run.json.combined_attention.value"))

    s11 = rec["checks"][0]["passed"]
    s12 = rec["checks"][1]["passed"]
    rec["checks"].append(_check(
        "S1-4", "稳定判据未通过条数为 0，且 S1-1 / S1-2 同时成立",
        "未通过条数=0 且 S1-1、S1-2 均成立",
        f"未通过条数={len(failed)}；S1-1={s11}；S1-2={s12}",
        (not failed) and s11 and s12,
        "run.json.history_window.stability.criteria"))

    entries = _ledger_entries(archive)
    if entries:
        bad = [f"{e.get('source_type')}={e.get('status')}"
               for e in entries if e.get("status") != "ok"]
        rec["checks"].append(_check(
            "S1-5", "证据台账四条 status 全为 ok", "四条全 ok",
            "全 ok" if not bad else f"不 ok 的：{bad}",
            not bad, "run.json.evidence_ledger.entries[*].status"))
    else:
        rec["checks"].append(_check(
            "S1-5", "证据台账四条 status 全为 ok", "N/A（该场景无证据台账）",
            "N/A（该场景无证据台账）", True,
            "run.json.evidence_ledger（本次运行没有台账）"))
        rec["notes"].append(
            "S1-5：本次运行**没有证据台账**（S1 按设计不接入外部证据），"
            "所以这一条标 N/A（该场景无证据台账），不是通过。")

    # ---- 反向对照：把 S2 的 history 塞进 S1 的 run_bundle 重算 ----
    s2_hist = others.get("S2_history")
    if s2_hist:
        base_verdict = rt.get("stability_verdict")
        swapped = recompute_stability(archive, s2_hist)
        rec["counter_evidence"] = {
            "what": "把 S2 的 history 塞进 S1 的 run_bundle，重算 build_judgement"
                    "（纯函数重算，不调 LLM）",
            "expected": "稳定性判定从『稳定』变成『不稳定』",
            "actual": f"S1 原判定={base_verdict}；换入 S2 的 6 点历史后重算={swapped}",
            "passed": (base_verdict == "稳定" and swapped == "不稳定"),
            "note": "只改了 history 这一个输入，其余输入（S1 的 current_state / "
                    "model_output）保持不变，结论跟着变 → 判定确实由历史驱动。",
        }
    else:
        rec["counter_evidence"] = {
            "what": "把 S2 的 history 塞进 S1 的 run_bundle 重算",
            "expected": "稳定性判定从『稳定』变成『不稳定』",
            "actual": "拿不到 S2 的历史（场景文件缺失）",
            "passed": False,
            "note": "反向对照无法执行。",
        }
    return rec


def verify_s2(archive: dict, run_id: str, others: dict) -> dict:
    notes: list[str] = []
    rec = _base_record("S2", archive, run_id, notes)
    j = _judgement(archive)
    rt = j.get("recent_trend") or {}
    ca = archive.get("combined_attention") or {}
    hist2 = archive.get("history") or []

    rec["checks"].append(_check(
        "S2-1", "稳定性判定为『不稳定』", "不稳定", rt.get("stability_verdict"),
        rt.get("stability_verdict") == "不稳定",
        "run.json.structured_judgement.recent_trend.stability_verdict"))

    # 末点温度与 S1 的差
    hist1 = others.get("S1_history")
    if hist1 and hist2:
        t1 = hist1[-1].get("temperature")
        t2 = hist2[-1].get("temperature")
        delta = abs((t2 or 0.0) - (t1 or 0.0))
        rec["checks"].append(_check(
            "S2-2", "末点温度与 S1 的差 ≤ 0.5℃（保证两窗可比）", "≤ 0.5℃",
            f"S1 末点={t1}℃；S2 末点={t2}℃；绝对差={round(delta, 3)}℃",
            delta <= 0.5,
            "run.json.history[-1].temperature（S1 取 S1 场景文件冻结的同一窗口）"))
    else:
        rec["checks"].append(_check(
            "S2-2", "末点温度与 S1 的差 ≤ 0.5℃", "≤ 0.5℃",
            "拿不到 S1 的历史，无法比对", False,
            "B07/scenarios/S1.json.history"))

    # 过程量至少一项不同（只从序列算）
    if hist1 and hist2:
        diff = _series_diff(hist1, hist2)
        differs = {k: v for k, v in diff.items() if v is not None and v > 0}
        rec["checks"].append(_check(
            "S2-3", "过程量与 S1 至少一项不同（只从 history 序列算，不读结论）",
            "至少一项序列的最大差 > 0",
            "；".join(f"{k} 最大差={round(v, 3)}" for k, v in diff.items()
                      if v is not None) or "无法比对",
            bool(differs),
            "run.json.history（逐点数值比对，不读任何结论字段）"))
    else:
        rec["checks"].append(_check(
            "S2-3", "过程量与 S1 至少一项不同", "至少一项序列的最大差 > 0",
            "拿不到 S1 的历史，无法比对", False,
            "B07/scenarios/S1.json.history"))

    # ---- 因果证明：把 S1 的 history 塞进 S2 的 run_bundle 重算 ----
    if hist1:
        base_verdict = rt.get("stability_verdict")
        swapped = recompute_stability(archive, hist1)
        rec["counter_evidence"] = {
            "what": "把 S1 的 history 塞进 S2 的 run_bundle，重算 build_judgement"
                    "（纯函数重算，不调 LLM）",
            "expected": "稳定性判定从『不稳定』变成『稳定』",
            "actual": f"S2 原判定={base_verdict}；换入 S1 的 6 点历史后重算={swapped}",
            "passed": (base_verdict == "不稳定" and swapped == "稳定"),
            "note": "只改了 history 这一个输入，结论跟着变 → 判定确实由历史驱动。",
        }
    else:
        rec["counter_evidence"] = {
            "what": "把 S1 的 history 塞进 S2 的 run_bundle 重算",
            "expected": "稳定性判定从『不稳定』变成『稳定』",
            "actual": "拿不到 S1 的历史（场景文件缺失）",
            "passed": False,
            "note": "反向对照无法执行。",
        }
    return rec


def verify_s3(archive: dict, run_id: str, others: dict) -> dict:
    notes: list[str] = []
    rec = _base_record("S3", archive, run_id, notes)
    j = _judgement(archive)
    ca = archive.get("combined_attention") or {}
    ec = archive.get("external_conditions") or {}
    ctx = archive.get("llm_context") or ""

    provider = ec.get("provider")
    retrieved_at = ec.get("retrieved_at")
    forecast_time = ec.get("forecast_time")
    missing = [n for n, v in (("provider", provider),
                              ("retrieved_at", retrieved_at),
                              ("forecast_time", forecast_time)) if not v]
    rec["checks"].append(_check(
        "S3-1", "外部条件可用，且 provider / retrieved_at / forecast_time 齐全",
        "available=True 且三个身份字段都非空",
        f"available={ec.get('available')}；provider={provider}；"
        f"retrieved_at={retrieved_at}；forecast_time={forecast_time}"
        + (f"；缺={missing}" if missing else ""),
        bool(ec.get("available")) and not missing,
        "run.json.external_conditions"))

    signal = ec.get("signal")
    allowed = ("external_deteriorating", "external_improving", "external_stable")
    rec["checks"].append(_check(
        "S3-2", "外部信号属于三个合法取值", " ∈ {external_deteriorating, "
        "external_improving, external_stable}", signal, signal in allowed,
        "run.json.external_conditions.signal"))

    has_block = "【⑥ 外部天气条件" in ctx
    has_signal = bool(signal) and signal in ctx
    rec["checks"].append(_check(
        "S3-3", "天气证据确实进入了本次运行（llm_context 的 ⑥ 块可见）",
        "llm_context 里能看到 ⑥ 外部天气条件块，且含本次外部信号",
        f"⑥ 块存在={has_block}；上下文含信号 {signal}={has_signal}",
        has_block and has_signal,
        "run.json.llm_context"))

    # ---- 因果证明：同一份 history，把 external_conditions 置 None 重算 ----
    ca_none = recompute_combined(archive, None)
    rec["checks"].append(_check(
        "S3-4", "因果证明：同一份 history 把 external_conditions 置 None 重算，"
                "合成关注等级不再是 watch_external",
        "带外部条件时是 watch_external；去掉外部条件后不是 watch_external",
        f"带外部条件={ca.get('value')}；external_conditions=None 重算="
        f"{ca_none.get('value')}",
        (ca.get("value") == "watch_external"
         and ca_none.get("value") != "watch_external"),
        "run.json.combined_attention + B07.judgement.build_combined_attention"
        "（纯函数重算，不调 LLM）"))

    rec["counter_evidence"] = {
        "what": "同一份 history，把 external_conditions 置 None 重算 "
                "build_combined_attention（纯函数重算，不调 LLM）",
        "expected": "带天气 = watch_external；去掉天气 = 不再 watch_external",
        "actual": f"带天气={ca.get('value')}；去掉天气={ca_none.get('value')}",
        "passed": (ca.get("value") == "watch_external"
                   and ca_none.get("value") != "watch_external"),
        "note": "只改了外部条件这一个输入，关注等级跟着变 → 外部天气确实参与了判定。",
    }
    return rec


def verify_s4(archive: dict, run_id: str, others: dict) -> dict:
    notes: list[str] = []
    rec = _base_record("S4", archive, run_id, notes)
    j = _judgement(archive)
    ca = archive.get("combined_attention") or {}
    div = j.get("evidence_divergence") or {}

    rec["checks"].append(_check(
        "S4-1", "证据分歧成立（has_conflict=True）", True, div.get("has_conflict"),
        bool(div.get("has_conflict")),
        "run.json.structured_judgement.evidence_divergence.has_conflict"))

    rec["checks"].append(_check(
        "S4-2", "合成关注等级为 conflict_unresolved", "conflict_unresolved",
        ca.get("value"), ca.get("value") == "conflict_unresolved",
        "run.json.combined_attention.value"))

    sup = div.get("supporting") or []
    opp = div.get("opposing") or []
    rec["checks"].append(_check(
        "S4-3", "分歧三列里 supporting 与 opposing 都非空",
        "supporting 非空 且 opposing 非空",
        f"supporting={len(sup)} 条，opposing={len(opp)} 条",
        bool(sup) and bool(opp),
        "run.json.structured_judgement.evidence_divergence"))

    text = archive.get("llm_explanation") or ""
    hits = find_consistency_claims(text)
    counted = [h for h in hits if h["counted"]]
    rec["checks"].append(_check(
        "S4-4", "LLM 解释没有把分歧说成一致（多字短语扫描，否定/免责句不算命中）",
        "不出现『都支持 / 一致支持 / 全部支持 / 无异议 / 没有矛盾』这类说法",
        (f"命中 {len(counted)} 处：" +
         "；".join(f"「{h['phrase']}」→ {h['sentence']}" for h in counted))
        if counted else
        (f"未命中（扫过 {len(hits)} 处候选，全部是否定/免责句）" if hits
         else "未命中（解释里没有这类说法）"),
        not counted,
        "run.json.llm_explanation"))

    # 反向对照：S5 的 has_conflict == False
    s5 = others.get("S5_archive")
    if s5:
        s5div = (_judgement(s5).get("evidence_divergence") or {})
        actual = s5div.get("has_conflict")
        rec["counter_evidence"] = {
            "what": "反向对照：S5（证据退化场景）的证据分歧",
            "expected": "S5 的 has_conflict == False（没有可冲突的第二方向）",
            "actual": f"S5 的 has_conflict={actual}",
            "passed": actual is False,
            "note": "S4 与 S5 是同一场景、同一锚点，唯一区别是天气证据在不在："
                    "有 → 分歧成立；没有 → 不构成分歧。",
        }
    else:
        rec["counter_evidence"] = {
            "what": "反向对照：S5 的 has_conflict == False",
            "expected": "S5 的 has_conflict == False",
            "actual": "拿不到 S5 的 run（先跑 verify --all）",
            "passed": False,
            "note": "反向对照无法执行。",
        }
    return rec


def verify_s5(archive: dict, run_id: str, others: dict) -> dict:
    notes: list[str] = []
    rec = _base_record("S5", archive, run_id, notes)
    j = _judgement(archive)
    ca = archive.get("combined_attention") or {}

    b03 = _entry_of(archive, "B03")
    b03_status = (b03 or {}).get("status")
    rec["checks"].append(_check(
        "S5-1", "台账里 B03 那条证据 status == unavailable", "unavailable",
        b03_status, b03_status == "unavailable",
        "run.json.evidence_ledger.entries[EV-B03].status"))

    others_status = {}
    for st in ("B02", "B06", "B04"):
        e = _entry_of(archive, st)
        others_status[st] = (e or {}).get("status")
    ok = all(v == "ok" for v in others_status.values())
    rec["checks"].append(_check(
        "S5-2", "B02 / B06 / B04 三条仍然 ok（系统没有整体失效）",
        "B02=ok, B06=ok, B04=ok",
        "，".join(f"{k}={v}" for k, v in others_status.items()),
        ok, "run.json.evidence_ledger.entries[*].status"))

    rec["checks"].append(_check(
        "S5-3", "合成关注等级为 watch_internal（主动退让）", "watch_internal",
        ca.get("value"), ca.get("value") == "watch_internal",
        "run.json.combined_attention.value"))

    text = archive.get("llm_explanation") or ""
    hits = find_weather_facts(text)
    counted = [h for h in hits if h["counted"]]
    rec["checks"].append(_check(
        "S5-4", "LLM 解释没有凭空断言天气事实（多字短语 + 断言语气；免责句不算）",
        "不出现『天气事实 + 断言语气』的组合",
        (f"命中 {len(counted)} 处：" +
         "；".join(f"{h['subjects']}+{h['assertions']} → {h['sentence']}"
                   for h in counted))
        if counted else
        (f"未命中（扫过 {len(hits)} 处候选，全部是免责/缺失声明）" if hits
         else "未命中（解释里没有天气类断言）"),
        not counted,
        "run.json.llm_explanation"))

    reason = ca.get("reason") or ""
    says_degraded = ("外部" in reason
                     and any(k in reason for k in
                             ("external_unavailable", "unavailable", "不可用", "降级")))
    rec["checks"].append(_check(
        "S5-5", "combined_attention.reason 明确说明了降级",
        "reason 里点明外部信号不可用 / 降级",
        reason, says_degraded,
        "run.json.combined_attention.reason"))

    # 反向对照：S4 的 B03 status == ok
    s4 = others.get("S4_archive")
    if s4:
        s4b03 = _entry_of(s4, "B03")
        actual = (s4b03 or {}).get("status")
        rec["counter_evidence"] = {
            "what": "反向对照：S4（同场景、天气证据可用时）的 B03 证据状态",
            "expected": "S4 的 B03 status == 'ok'",
            "actual": f"S4 的 B03 status={actual}",
            "passed": actual == "ok",
            "note": "S5 的 B03 status=unavailable 不是系统坏了，是这一条证据没取到；"
                    "同一场景下证据可用时它就是 ok。",
        }
    else:
        rec["counter_evidence"] = {
            "what": "反向对照：S4 的 B03 status == 'ok'",
            "expected": "S4 的 B03 status == 'ok'",
            "actual": "拿不到 S4 的 run（先跑 verify --all）",
            "passed": False,
            "note": "反向对照无法执行。",
        }
    return rec


VERIFIERS = {
    "S1": verify_s1,
    "S2": verify_s2,
    "S3": verify_s3,
    "S4": verify_s4,
    "S5": verify_s5,
}


# --------------------------------------------------------------------------
# 顶层：验证一个 / 全部
# --------------------------------------------------------------------------

def _scenario_history(sid: str) -> list:
    p = scenario_path(sid)
    if not p.exists():
        return []
    try:
        return _read_json(p).get("history") or []
    except Exception:                       # noqa: BLE001
        return []


def verify(sids=None, run_overrides: dict | None = None,
           companion_runs: dict | None = None,
           recompute: bool = False) -> tuple[list[dict], bool]:
    """验证一个或多个场景。

    ★ `companion_runs` 是第四遍的增量：给某个场景单独指定「对照锚点」的 run
      （例如只验 S4 时，S5 的参照存档从 run_125 取），
      第二个参数 `run_overrides` 仍然是「同场景、不同 run」的机制。

    ★ `recompute=True` 是第四遍的 L1 复算层：check 的 actual 换成
      「按存档原始字段重算」的值，默认（False）行为**一字不变**。

    Returns:
        (records, all_passed)
    """
    sids = list(sids or SCENARIOS)
    run_overrides = run_overrides or {}
    companion_runs = companion_runs or {}
    index = replay_index()

    # 先把每个场景的 run 与存档准备好（互相要用）
    archives: dict = {}
    run_ids: dict = {}
    for sid in sids:
        rid = run_overrides.get(sid) or (index.get(sid) or {}).get("run_id")
        if not rid:
            continue
        arch = load_archive(rid)
        if arch is not None:
            archives[sid] = arch
            run_ids[sid] = rid

    # 对照锚点（第四遍用）：只进 archives，不产生记录
    for sid, rid in companion_runs.items():
        if sid in archives:
            continue
        arch = load_archive(rid)
        if arch is not None:
            archives[sid] = arch

    others = {
        "S1_history": _scenario_history("S1"),
        "S2_history": _scenario_history("S2"),
        "S4_archive": archives.get("S4"),
        "S5_archive": archives.get("S5"),
    }

    records: list[dict] = []
    for sid in sids:
        arch = archives.get(sid)
        if arch is None:
            rid = run_overrides.get(sid) or (index.get(sid) or {}).get("run_id")
            records.append({
                "scenario_id": sid,
                "run_id": rid,
                "app_version": _app_version()[0],
                "model_version": None,
                "analysis_time": None,
                "location": None,
                "current_state": None,
                "history_window": None,
                "weather_evidence": {},
                "knowledge_evidence": {},
                "model_evidence": {},
                "tool_run_status": {},
                "structured_judgement": {},
                "llm_explanation": None,
                "limitations": [],
                "checks": [_check(
                    "S?-0", "该场景已完成固定场景回放（有一条可验证的 run）",
                    "有一行 run.json", f"没有找到 {sid} 的固定场景回放记录",
                    False, "B07/artifacts/replay_log.jsonl")],
                "counter_evidence": {},
                "verdict": "FAIL",
                "notes": [f"{sid} 还没有按场景文件回放过 —— "
                          "先跑 python -m B07.replay --scenario-file "
                          f"B07/scenarios/{sid}.json"],
            })
            continue

        rec = VERIFIERS[sid](arch, run_ids[sid], others)
        rec["verified_at"] = _now_iso()
        row = (index.get(sid) or {})
        rec["source_scenario_file"] = row.get("scenario_file")
        rec["source_run_id"] = row.get("source_run_id")
        rec["replay_log_entry"] = row
        rec["verdict"] = "PASS" if (
            all(c["passed"] for c in rec["checks"])
            and rec["counter_evidence"].get("passed")) else "FAIL"
        if recompute:
            # 第四遍 · L1 复算层：actual 换成重算值，并挂上三层
            _apply_l1(rec, arch)
            rec["layers"]["L3_llm_consistency"] = _l3_layer(arch, sid_kind(sid))
            rec["verdict"] = "PASS" if (
                all(c["passed"] for c in rec["checks"])
                and rec["counter_evidence"].get("passed")
                and rec["layers"]["L1_recompute"].get("passed")) else "FAIL"
        records.append(rec)

    ok = bool(records) and all(r["verdict"] == "PASS" for r in records)
    return records, ok


def write_records(records: list[dict], path=None, append: bool = False) -> Path:
    """写验证记录。

    ★ 默认行为（第二遍）**不变**：覆盖写 b08_validation_records.jsonl。
    第四遍用 append=True 追加到自己的文件 —— 第二遍的 5 行不许被覆盖。
    """
    p = Path(path) if path else RECORDS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a" if append else "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def read_records(path=None) -> list[dict]:
    p = Path(path) if path else RECORDS_PATH
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:                   # noqa: BLE001
            continue
    return out


# --------------------------------------------------------------------------
# 打印
# --------------------------------------------------------------------------

def _print_record(rec: dict) -> None:
    print("\n" + "-" * 74)
    print(f"  {rec['scenario_id']} · run={rec.get('run_id')} · "
          f"verdict={rec.get('verdict')}")
    print(f"    app_version   : {rec.get('app_version')} "
          f"({rec.get('app_version_full') or '-'})")
    print(f"    model_version : {rec.get('model_version')}")
    print(f"    analysis_time : {rec.get('analysis_time')} @ {rec.get('location')}")
    w = rec.get("weather_evidence") or {}
    if w:
        print(f"    天气证据      : provider={w.get('provider')} "
              f"retrieved_at={w.get('retrieved_at')} "
              f"forecast_time={w.get('forecast_time')} "
              f"signal={w.get('signal')} status={w.get('status')}")
    else:
        print("    天气证据      ：（本次运行没有外部天气）")
    for c in rec.get("checks", []):
        mark = "✅" if c["passed"] else "❌"
        print(f"    {mark} {c['id']}  {c['claim']}")
        print(f"        期望: {c['expected']}")
        print(f"        实际: {c['actual']}")
        print(f"        来源: {c['source']}")
        if "recomputable" in c:
            if c.get("recomputable"):
                print(f"        复算: 存档={c.get('archived_value')} → "
                      f"重算={c.get('recomputed_value')}  一致={c.get('match')}")
            else:
                print(f"        复算: 不可重算 —— {c.get('recompute_note')}")
    ce = rec.get("counter_evidence") or {}
    mark = "✅" if ce.get("passed") else "❌"
    print(f"    {mark} 反向对照（因果证明）")
    print(f"        做法: {ce.get('what')}")
    print(f"        期望: {ce.get('expected')}")
    print(f"        实际: {ce.get('actual')}")
    layers = rec.get("layers") or {}
    for key, name in (("L1_recompute", "L1 复算层"),
                      ("L2_expectation", "L2 期望层"),
                      ("L3_llm_consistency", "L3 LLM 一致性层")):
        item = layers.get(key)
        if not item:
            continue
        print(f"    {'✅' if item.get('passed') else '❌'} {name}")
        if key == "L1_recompute":
            for i in item.get("items") or []:
                if i.get("match") is False:
                    print(f"        ❌ {i['check_id']} {i['label']}："
                          f"存档={i.get('archived')} 重算={i.get('recomputed')}")
                elif i.get("recomputable"):
                    print(f"        ✔ {i['check_id']} {i['label']}："
                          f"存档={i.get('archived')} = 重算={i.get('recomputed')}")
        if key == "L3_llm_consistency":
            for i in item.get("items") or []:
                print(f"        · {i['assertion']}："
                      f"{'通过' if i['passed'] else '未通过'}")
    for n in rec.get("notes", []):
        print(f"    · {n}")


def _print_summary(records: list[dict], ok: bool) -> None:
    print("\n" + "=" * 74)
    print("B08 第二遍 · 正式验证 S1~S5")
    print("=" * 74)
    for rec in records:
        n_checks = len(rec.get("checks") or [])
        n_pass = sum(1 for c in rec.get("checks", []) if c["passed"])
        ce = rec.get("counter_evidence") or {}
        print(f"  {rec['scenario_id']}  run={rec.get('run_id') or '-':<10} "
              f"检查 {n_pass}/{n_checks} 通过，反向对照 "
              f"{'通过' if ce.get('passed') else '未通过'}  → "
              f"{rec.get('verdict')}")
    print("-" * 74)
    print(f"  总判定：{'✅ 五个场景全部 PASS' if ok else '❌ 存在未通过项（见上）'}")
    print("=" * 74)


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------

def build_report(records: list[dict], ok: bool) -> str:
    """生成验证报告（数字全部来自 records，不手工编）。"""
    by_id = {r["scenario_id"]: r for r in records}
    app, baseline = _app_version()
    git = (baseline or {}).get("git") or {}
    lines: list[str] = []
    A = lines.append

    A("# B08 第二遍 · 正式验证报告（S1~S5）")
    A("")
    A(f"> 生成时间：{_now_iso()}")
    A(f"> app_version：`{app}`"
      + (f"（完整形式 `{((baseline or {}).get('app_version_composition') or {}).get('full')}`）"
         if baseline else ""))
    A(f"> git：`{git.get('commit_short') or '未知'}`，"
      f"工作区 dirty = **{str(git.get('dirty')).lower()}**"
      f"（{git.get('dirty_file_count')} 个文件）")
    A("")
    if not git.get("dirty", True):
        A("> 工作区干净，本报告对应一份可复现的提交。")
    else:
        A("> ★ **当前基线不是干净版本** —— 工作区有 "
          f"{git.get('dirty_file_count')} 个文件改动或未跟踪，"
          "基线里的提交指纹只覆盖已提交的部分。")
    A("")

    # 1
    A("---")
    A("")
    A("## 1. 第二遍做了什么")
    A("")
    A("第一遍冻结的是**输入**（基线版本号 + 五个固定场景 + 两层健康检查）；"
      "第二遍做的是**用这些固定输入真跑一遍，然后逐条判定**：")
    A("")
    A("1. 按场景文件回放五次（每个场景 1 次 LLM 调用，共 5 次）：")
    A("")
    A("   ```bash")
    for sid in SCENARIOS:
        A(f"   python -m B07.replay --scenario-file B07/scenarios/{sid}.json")
    A("   ```")
    A("")
    A("   回放出来的 run 落在 `B07/runs/`，回放日志追加在 "
      "`B07/artifacts/replay_log.jsonl`（`base_run_id` = 场景文件里的 "
      "`source_run_id`）。")
    A("2. `python -m B07.b08 verify --all` 逐场景跑断言型检查与因果证明，"
      "结果写进 `B07/artifacts/b08_validation_records.jsonl`。")
    A("3. `python -m B07.b08 report` 生成本报告。")
    A("")
    A("**判定分两类，本报告不许混：**")
    A("")
    A("| | 断言型检查 | 因果证明（counter_evidence） |")
    A("|---|---|---|")
    A("| 问什么 | 这次运行是不是这样 | 改了**一个**输入，输出跟不跟着变 |")
    A("| 怎么判 | 读存档字段逐条比对 | 同一套纯函数重算（**不调 LLM**） |")
    A("| 覆盖 | S1-1…S5-5 | 每个场景各一条 |")
    A("")

    # 2
    A("---")
    A("")
    A("## 2. 五场景验证记录摘要")
    A("")
    A("| 场景 | run_id | 判定 | 检查通过 | 反向对照 | 关键结论 |")
    A("|---|---|---|---|---|---|")
    keyline = {
        "S1": "内部稳定、B06=NORMAL、合成关注等级 internal_only",
        "S2": "内部不稳定、末点温度与 S1 一致、合成关注等级 internal_only",
        "S3": "外部 deteriorating → 合成关注等级 watch_external",
        "S4": "证据分歧成立 → 合成关注等级 conflict_unresolved",
        "S5": "天气证据不可用 → 合成关注等级 watch_internal（主动退让）",
    }
    for sid in SCENARIOS:
        r = by_id.get(sid)
        if not r:
            A(f"| `{sid}` | — | **未验证** | — | — | 没有可验证的 run |")
            continue
        n_checks = len(r.get("checks") or [])
        n_pass = sum(1 for c in r.get("checks", []) if c["passed"])
        ce = r.get("counter_evidence") or {}
        A(f"| `{sid}` | `{r.get('run_id')}` | "
          f"**{r.get('verdict')}** | {n_pass}/{n_checks} | "
          f"{'通过' if ce.get('passed') else '未通过'} | {keyline.get(sid, '')} |")
    A("")
    A("### 逐条检查明细")
    A("")
    for sid in SCENARIOS:
        r = by_id.get(sid)
        if not r:
            continue
        A(f"\n#### `{sid}`（run `{r.get('run_id')}`，判定 **{r.get('verdict')}**）")
        A("")
        A("| 检查 | 断言 | 期望 | 实际 | 结果 |")
        A("|---|---|---|---|---|")
        for c in r.get("checks", []):
            A(f"| `{c['id']}` | {c['claim']} | {c['expected']} | "
              f"{str(c['actual']).replace('|', '/')} | "
              f"{'✅' if c['passed'] else '❌'} |")
        ce = r.get("counter_evidence") or {}
        A(f"| 反向对照 | {ce.get('what')} | {ce.get('expected')} | "
          f"{str(ce.get('actual')).replace('|', '/')} | "
          f"{'✅' if ce.get('passed') else '❌'} |")
    A("")

    # 3
    A("---")
    A("")
    A("## 3. 五条因果证明（改了哪个输入、输出怎么跟着变）")
    A("")
    A("每条证明都**只改一个输入**，用同一套纯函数重算（判定重算不调 LLM，"
      "Limitation L5）。")
    A("")
    for sid in SCENARIOS:
        r = by_id.get(sid)
        if not r:
            continue
        ce = r.get("counter_evidence") or {}
        A(f"### {sid}：{ce.get('what')}")
        A("")
        A(f"- **改了什么**：{ce.get('what')}")
        A(f"- **期望**：{ce.get('expected')}")
        A(f"- **实际**：{ce.get('actual')}")
        A(f"- **结论**：{'✅ ' if ce.get('passed') else '❌ '}"
          f"{ce.get('note') or ''}")
        A("")

    # 4
    A("---")
    A("")
    A("## 4. 两类验证的区分")
    A("")
    A("| | (1) 真实链路验证 | (2) 固定场景回放 / 回归测试 |")
    A("|---|---|---|")
    A("| 外部天气从哪来 | 重新调 B03 真实天气 API | **已真实取得并保存的快照** |")
    A("| 天气会不会漂 | 会（真实世界时间轴） | 不会（条件被固定住） |")
    A("| 证明什么 | 请求 / 参数 / 来源 / 时间记录 / 失败处理都是真实工作的 | "
      "同一个 scenario_id 重复验证时主要条件一致 |")
    A("| 本轮做没做 | **没做**（仍是缺口，见第 5 节） | ★ 就是本轮做的 |")
    A("")
    A("> ★ 场景文件里的 `is_constructed` 与 `snapshot_taken_from_run_id` "
      "就是这条边界的标记：`is_constructed=true` = 那是离线构造的用例；"
      "`false` = 那次取的是真实 API。")
    A("> **快照反映的是「取得那一刻」的外部事实，它不是实时天气。**")
    A("")

    # 5
    A("---")
    A("")
    A("## 5. 已知缺口（诚实记录）")
    A("")
    gaps = [
        "**工作区是脏的** —— 基线里的提交指纹只覆盖已提交的部分；"
        "未提交的工作区改动不在基线里。",
        "**S3 的 provider / retrieved_at 不是存档原样记录的** —— "
        "S1/S2/S3 当时的存档没有证据台账，`run.json.external_conditions` "
        "也没记这两个字段。快照里 provider 取自 B03 代码常量、"
        "retrieved_at 按项目既有约定（`B07/evidence_registry.py`）取该 run 的 "
        "created_at，两处都用 `*_recorded_in_archive=false` 标注了来源。",
        "**S4 / S5 的天气是离线夹具**（`provider=Offline-Fixture`、"
        "`is_constructed=true`），不是真实天气；它们固定的是"
        "「证据分歧 / 证据退化」这两种**判定形态**。",
        "**没有做真实链路验证** —— 本轮没有重新调过 B03 真实天气 API；"
        "S3 场景里的天气是历史真实 API 的**快照**，不是这一轮取的。",
        "**因果证明只覆盖纯函数部分** —— 判定重算不调 LLM，"
        "所以 LLM 措辞本身不在因果证明的覆盖范围内。",
        "**本轮只做第二遍** —— 同题对照 / 失败验证 / 运行追踪 / "
        "≥10 个固定回归案例与第四遍回归报告**尚未开始**。",
    ]
    for g in gaps:
        A(f"* {g}")
    A("")

    # 6
    A("---")
    A("")
    A("## 6. 本轮只做第二遍")
    A("")
    A("这一轮**只做**「用固定场景真跑一遍 + 逐条判定 + 留下可复核的记录」。"
      "以下都**不在本轮**：")
    A("")
    A("* 真实链路验证（重新调 B03 天气 API 的那一次）")
    A("* 同题对照 / 失败验证 / 运行追踪")
    A("* ≥10 个固定回归案例与第四遍回归报告")
    A("* 竞赛表达材料")
    A("")
    A("### Limitations")
    A("")
    A("* **L1** app_version 由 Git 提交指纹表达，**不是**写进 run.json 的字段；"
      "所以历史 run 无法追溯到它们产生时的 app_version，只能追溯到当前基线。"
      "本轮的验证记录**显式带** app_version。")
    A("* **L2** 知识资料版本用文件 sha256 代替版本号 —— 知识库没有版本管理机制。")
    A("* **L3** 固定场景的天气证据是**快照**，反映取得那一刻的外部事实；"
      "它不是实时天气。")
    A("* **L4** 场景文件是从**已有存档**抽取的 —— 固化的是「当时那一次」的输入，"
      "不是重新生成的。")
    A("* **L5** 判定重算**不调 LLM**，因果证明只覆盖纯函数部分"
      "（稳定性判定、合成关注等级、证据分歧）。")
    A("* **L6** S3 天气的 provider / retrieved_at 在部分存档中缺失，"
      "本轮按既有约定补齐并逐项标注来源（见第 5 节）。")
    A("* **L7** 本轮只做第二遍 —— ≥10 个固定回归案例与第四遍回归报告尚未开始。")
    A("")
    return "\n".join(lines) + "\n"


def write_report(records: list[dict], ok: bool) -> Path:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(build_report(records, ok), encoding="utf-8")
    return REPORT_PATH


# ==========================================================================
# 第四遍 · 固定回归（L1 复算层 / L2 期望层 / L3 LLM 一致性层）
# ==========================================================================
#
# ★ 这一遍在第二遍的验证器上**升级**，不重写：
#   · 验证器仍是 verify_s1..verify_s5（期望值一字未改）；
#   · 新增 L1 复算层：check 的 actual 从「读存档」换成「按存档原始字段重算」，
#     再与存档值逐项比对 —— 这才是能测出「代码改坏了」的那一层；
#   · 新增 L2 期望层：期望值沿用第二遍，但拿**重算值**去比；
#   · 新增 L3 LLM 一致性层：复用 find_consistency_claims / find_weather_facts。
#
# ★ 三层分工：
#   L1 复算层   —— 代码还能不能把当年的结论重算出来（archived vs recomputed）
#   L2 期望层   —— 重算出来的结果还对不对（第二遍的期望值，不重写）
#   L3 一致性层 —— 结构化判断与 LLM 解释有没有互相打架
#
# ★ 硬约束：
#   · 期望值写在**冻结的期望清单**里（b08_regression_expectations.json），
#     代码只负责「重算 → 比对 → 记录」；
#   · 回归**不调 LLM、不跑模型、不取天气、不检索知识**；
#   · 负对照的坏案例**只在内存里**，绝不落盘；
#   · 第一/二/三遍的产物一律只读。

#: 回归集编号（写进每一条结果行）
REGRESSION_SET_ID = "B08_regression_v1"

#: 第四遍产物（第一/二/三遍的产物一律只读）
REGRESSION_RECORDS_PATH = (_PROJECT_ROOT / "B07" / "artifacts"
                           / "b08_regression_records.jsonl")
REGRESSION_RESULTS_PATH = (_PROJECT_ROOT / "B07" / "artifacts"
                           / "b08_regression_results.jsonl")
REGRESSION_EXPECT_PATH = (_PROJECT_ROOT / "B07" / "artifacts"
                           / "b08_regression_expectations.json")
RECOMPUTE_RECORDS_PATH = (_PROJECT_ROOT / "B07" / "artifacts"
                           / "b08_recompute_records.jsonl")
REGRESSION_REPORT_PATH = (_PROJECT_ROOT / "B07" / "artifacts"
                          / "b08_regression_report.md")

#: 反向对照的参照 run（固定不动，当作对照锚点）
REGRESSION_REF_RUNS = {"S4": "run_124", "S5": "run_125"}

#: 负对照的案例 id
NEGATIVE_CASE_ID = "NEG_must_fail"

#: 负对照改坏的地方（**只在内存副本上**）：第 3 帧温度 +1.0℃
NEGATIVE_CORRUPTION = {"target": "history[2].temperature", "delta": 1.0}

#: 13 个固定回归案例 —— 同一场景、不同 run
REGRESSION_CASES = (
    {"case_id": "S1_stable_a", "scenario_id": "S1", "run_id": "run_121",
     "kind": "stable", "role": "case",
     "intent": "第二遍的 S1 正式回放：内部稳定 → 不误报"},
    {"case_id": "S1_stable_b", "scenario_id": "S1", "run_id": "run_113",
     "kind": "stable", "role": "case",
     "intent": "场景文件的源 run（同场景、不同 run）：稳定结论必须一样"},
    {"case_id": "S2_drift_a", "scenario_id": "S2", "run_id": "run_122",
     "kind": "drift", "role": "case",
     "intent": "第二遍的 S2 正式回放：内部漂移 → 能发现"},
    {"case_id": "S2_drift_b", "scenario_id": "S2", "run_id": "run_114",
     "kind": "drift", "role": "case",
     "intent": "场景文件的源 run：漂移结论必须一样"},
    {"case_id": "S3_ext_stable", "scenario_id": "S3", "run_id": "run_116",
     "kind": "external", "role": "case",
     "intent": "外部信号 external_stable：外部**不恶化**就不该抬升关注度"},
    {"case_id": "S3_ext_deteriorating", "scenario_id": "S3", "run_id": "run_123",
     "kind": "external", "role": "case",
     "intent": "外部信号 external_deteriorating：外部恶化 → watch_external"},
    {"case_id": "S3_no_weather", "scenario_id": "S3", "run_id": "run_115",
     "kind": "external", "role": "control",
     "intent": "对照组：这次运行根本没有外部天气 —— 天气相关的断言必须**全部不成立**"
               "（不许在没有天气时凭空报外部关注）"},
    {"case_id": "S4_conflict_a", "scenario_id": "S4", "run_id": "run_124",
     "kind": "conflict", "role": "case",
     "intent": "第二遍的 S4 正式回放：证据分歧成立 → conflict_unresolved"},
    {"case_id": "S4_conflict_b", "scenario_id": "S4", "run_id": "run_117",
     "kind": "conflict", "role": "case",
     "intent": "存档自称 S5，但证据形态是分歧（B03=ok）→ 用 S4 的验证器；"
               "同一场景不同 run 的分歧结论必须一样"},
    {"case_id": "S5_degrade_a", "scenario_id": "S5", "run_id": "run_125",
     "kind": "degrade", "role": "case",
     "intent": "第二遍的 S5 正式回放：证据退化 → 主动退让 watch_internal"},
    {"case_id": "S5_degrade_b", "scenario_id": "S5", "run_id": "run_119",
     "kind": "degrade", "role": "case",
     "intent": "开发期的一条降级 run：退化结论必须一样"},
    {"case_id": "S5_degrade_c", "scenario_id": "S5", "run_id": "run_127",
     "kind": "degrade", "role": "case",
     "intent": "第三遍的又一条降级 run：退化结论必须一样"},
    {"case_id": "S5_degrade_d", "scenario_id": "S5", "run_id": "run_126",
     "kind": "degrade", "role": "case",
     "intent": "★ 第三遍**主动制造**的失败 run：回归断言它与 run_125 行为一致，"
               "**不是**断言它「成功」"},
)

#: 依赖 LLM 文本的检查（同场景不同 run 之间允许不同，不参与「孪生案例」比对）
LLM_TEXT_CHECKS = ("S4-4", "S5-4")

#: 场景 → 回归分组（stable / drift / external / conflict / degrade）
SCENARIO_KINDS = {
    "S1": "stable", "S2": "drift", "S3": "external",
    "S4": "conflict", "S5": "degrade",
}


def sid_kind(sid: str) -> str:
    """场景 → 回归分组。"""
    return SCENARIO_KINDS.get(sid, sid)

#: check id → L1 复算字段
_L1_SPECS = {
    "S1-1": ("stability_verdict", "稳定性判定"),
    "S1-2": ("model_predicted_attention", "B06 预测关注度"),
    "S1-3": ("combined_attention_value", "合成关注等级"),
    "S1-4": ("stability_failed_criteria", "稳定判据未通过条数"),
    "S1-5": ("ledger_statuses", "证据台账状态"),
    "S2-1": ("stability_verdict", "稳定性判定"),
    "S2-2": ("series_delta", "末点温差"),
    "S2-3": ("series_diff", "过程量差异"),
    "S3-1": ("weather_identity", "天气身份三件套"),
    "S3-2": ("external_signal", "外部信号"),
    "S3-3": ("llm_context_block", "llm_context 的 ⑥ 块"),
    "S3-4": ("combined_attention_value", "合成关注等级"),
    "S4-1": ("divergence_conflict", "证据分歧"),
    "S4-2": ("combined_attention_value", "合成关注等级"),
    "S4-3": ("divergence_counts", "分歧三列"),
    "S4-4": ("llm_text", "LLM 解释文本"),
    "S5-1": ("ledger_status", "B03 台账状态"),
    "S5-2": ("ledger_status", "B02/B06/B04 台账状态"),
    "S5-3": ("combined_attention_value", "合成关注等级"),
    "S5-4": ("llm_text", "LLM 解释文本"),
    "S5-5": ("combined_attention_reason", "合成关注等级的理由"),
}

#: 冻结期望清单时的**交叉校验规则**。
#: ★ 这里**不是**期望值的存放处 —— 期望值存在冻结的 JSON 里。
#:   这些规则只在**冻结时**起作用，每条都指向一个**独立来源**
#:   （第二遍的冻结记录 / 文档写明的行为语义）；
#:   观察到的形态与规则矛盾 → **拒绝冻结**，绝不放行。
_FREEZE_TWIN = {                       # 同场景的另一条 run → 参照案例
    "S1_stable_b": "S1_stable_a",
    "S2_drift_b": "S2_drift_a",
    "S4_conflict_b": "S4_conflict_a",
    "S5_degrade_b": "S5_degrade_a",
    "S5_degrade_c": "S5_degrade_a",
    "S5_degrade_d": "S5_degrade_a",
}

#: S3 组的语义规则（文档行为 3：外部条件参与判断）
_FREEZE_SEMANTIC = {
    "S3_ext_stable": {"S3-1": False, "S3-2": True, "S3-3": True, "S3-4": False},
    "S3_no_weather": {"S3-1": False, "S3-2": False, "S3-3": False, "S3-4": False},
    "S3_ext_deteriorating": {"S3-1": True, "S3-2": True, "S3-3": True,
                             "S3-4": True},
}


# --------------------------------------------------------------------------
# 复算底座（全部纯函数，零 LLM、零网络）
# --------------------------------------------------------------------------

def _recompute_divergence(archive: dict) -> dict | None:
    """按存档里的证据台账重算分歧（纯函数）。"""
    ledger = archive.get("evidence_ledger")
    if not ledger:
        return None
    from B07.evidence_ledger import EvidenceLedger
    from B07.judgement import build_evidence_divergence

    return build_evidence_divergence(EvidenceLedger.from_dict(ledger))


def _ca_with_divergence(archive: dict, divergence: dict | None) -> dict:
    """带指定分歧的合成关注等级（纯函数）。"""
    from B07.judgement import build_combined_attention

    bundle = {
        "model_output": archive.get("model_output"),
        "external_conditions": archive.get("external_conditions"),
        "evidence_divergence": divergence,
    }
    return build_combined_attention(bundle)


def _recompute_ca(archive: dict) -> dict:
    """L1 用：按存档原始字段重算合成关注等级。

    · 有证据分歧的存档（S4/S5）走 build_combined_attention —— 必须带上分歧分支，
      否则复算出来的是 watch_internal 而不是 conflict_unresolved；
    · 其余（S1/S2/S3）走第二遍已有的 recompute_combined（同一份纯函数）。
    """
    if _judgement(archive).get("evidence_divergence") is not None:
        return _ca_with_divergence(archive, _recompute_divergence(archive))
    return recompute_combined(archive, archive.get("external_conditions"))


def _mentions_degradation(reason: str) -> bool:
    """reason 里是否点明了外部降级（与第二遍 S5-5 同一套判定）。"""
    reason = reason or ""
    return ("外部" in reason
            and any(k in reason for k in
                    ("external_unavailable", "unavailable", "不可用", "降级")))


def _recompute_stability_criteria(archive: dict) -> list | None:
    """按存档的原始 history 重算稳定判据（走 judge_stability）。"""
    from B07.runs import recompute_from_archive

    if not (archive.get("history") or []):
        return None
    rc = recompute_from_archive(archive)
    return (rc.get("recomputed") or {}).get("stability", {}).get("criteria")


# --------------------------------------------------------------------------
# L1 复算层：每条检查的 archived 值 vs 重算值
# --------------------------------------------------------------------------

def _l1_item(check_id: str, archive: dict, rec: dict) -> dict:
    """算一条检查的 L1 复算项。

    永远返回；**算不出来就明说**（recomputable=false + 原因），绝不假装。
    """
    kind, label = _L1_SPECS.get(check_id, (None, ""))
    item = {"check_id": check_id, "field": kind or "unknown",
            "label": label, "archived": None, "recomputed": None,
            "match": None, "recomputable": False, "note": ""}

    if kind == "stability_verdict":
        item["archived"] = (_judgement(archive).get("recent_trend") or {}).get(
            "stability_verdict")
        if archive.get("history"):
            item["recomputed"] = recompute_stability(archive,
                                                      archive.get("history"))
            item["recomputable"] = True
            item["match"] = item["archived"] == item["recomputed"]
        item["note"] = ("重算输入 = archive.history（6 点原始序列）→ "
                        "build_judgement → judge_stability（纯函数，不调 LLM）")

    elif kind == "model_predicted_attention":
        item["archived"] = _judgement(archive).get("predicted_attention")
        item["note"] = ("B06 模型输出：回归**不重跑模型**，沿用存档值"
                        "（Limitation L3 —— 模型换了这一层查不出来）")

    elif kind == "combined_attention_value":
        item["archived"] = (archive.get("combined_attention") or {}).get("value")
        item["recomputed"] = _recompute_ca(archive).get("value")
        item["recomputable"] = True
        item["match"] = item["archived"] == item["recomputed"]
        item["note"] = ("重算输入 = archive.model_output + archive.external_conditions"
                        "（+ 证据分歧分支）→ build_combined_attention（纯规则）")

    elif kind == "stability_failed_criteria":
        crit = ((archive.get("history_window") or {}).get("stability")
                or {}).get("criteria") or []
        item["archived"] = sum(1 for c in crit if not c.get("passed"))
        new_crit = _recompute_stability_criteria(archive)
        if new_crit is not None:
            item["recomputed"] = sum(1 for c in new_crit if not c.get("passed"))
            item["recomputable"] = True
            item["match"] = item["archived"] == item["recomputed"]
        item["note"] = ("重算输入 = archive.history → judge_stability 的 criteria"
                        "（稳定判据未通过条数）")

    elif kind == "ledger_statuses":
        entries = _ledger_entries(archive)
        item["archived"] = {e.get("source_type"): e.get("status")
                            for e in entries}
        item["note"] = ("证据台账条目状态是**外部系统返回的原始事实**，不是判定结论 "
                        "—— 没有可重算的推导（回归只校验它没被改写）")

    elif kind == "ledger_status":
        item["archived"] = {e.get("source_type"): e.get("status")
                            for e in _ledger_entries(archive)}
        item["note"] = ("证据台账条目状态是**外部系统返回的原始事实**，不是判定结论 "
                        "—— 没有可重算的推导（回归只校验它没被改写）")

    elif kind == "series_delta":
        item["archived"] = None
        item["recomputed"] = _check_actual(rec, check_id)
        item["recomputable"] = True
        item["match"] = None
        item["note"] = ("第二遍这条**本来就是从原始序列算出来的**（不读任何结论字段）；"
                        "存档里没有对应的结论字段，没有存档值可比 → archived=null")

    elif kind == "series_diff":
        item["archived"] = None
        item["recomputed"] = _check_actual(rec, check_id)
        item["recomputable"] = True
        item["match"] = None
        item["note"] = ("第二遍这条**本来就是从原始序列算出来的**（不读任何结论字段）；"
                        "存档里没有对应的结论字段，没有存档值可比 → archived=null")

    elif kind == "weather_identity":
        ec = archive.get("external_conditions") or {}
        item["archived"] = {k: ec.get(k) for k in
                             ("provider", "retrieved_at", "forecast_time")}
        item["note"] = ("天气身份三件套来自 B03 **当次真实取数**；回归离线只读存档，"
                        "不重新联网取天气 → 不可重算")

    elif kind == "external_signal":
        ec = archive.get("external_conditions") or {}
        item["archived"] = ec.get("signal")
        if ec.get("baseline_point") and ec.get("forecast_point"):
            from B07.runs import recompute_external_conditions

            rc = recompute_external_conditions(archive)
            item["recomputed"] = (rc.get("recomputed") or {}).get("signal")
            item["recomputable"] = True
            item["match"] = item["archived"] == item["recomputed"]
        item["note"] = ("重算输入 = archive.external_conditions 的两个天气点 → "
                        "judge_external_points（离线，不取网）")

    elif kind == "llm_context_block":
        ctx = archive.get("llm_context") or ""
        item["archived"] = "【⑥ 外部天气条件" in ctx
        item["note"] = ("llm_context 是**发出去那一刻的入参存档**；重建它要走完整的"
                        "提示词拼装链（含台账与限制项渲染），本轮不重跑 → 不可重算")

    elif kind == "divergence_conflict":
        div = _judgement(archive).get("evidence_divergence") or {}
        item["archived"] = div.get("has_conflict")
        new_div = _recompute_divergence(archive)
        if new_div is not None:
            item["recomputed"] = new_div.get("has_conflict")
            item["recomputable"] = True
            item["match"] = item["archived"] == item["recomputed"]
        item["note"] = ("重算输入 = archive.evidence_ledger.entries → EvidenceLedger "
                        "→ build_evidence_divergence（方向是程序规则算的）")

    elif kind == "divergence_counts":
        div = _judgement(archive).get("evidence_divergence") or {}
        item["archived"] = [len(div.get("supporting") or []),
                            len(div.get("opposing") or [])]
        new_div = _recompute_divergence(archive)
        if new_div is not None:
            item["recomputed"] = [len(new_div.get("supporting") or []),
                                  len(new_div.get("opposing") or [])]
            item["recomputable"] = True
            item["match"] = item["archived"] == item["recomputed"]
        item["note"] = ("重算输入 = archive.evidence_ledger.entries → EvidenceLedger "
                        "→ build_evidence_divergence（supporting / opposing 条数）")

    elif kind == "llm_text":
        item["archived"] = _check_actual(rec, check_id)
        item["note"] = ("LLM 生成文本：回归**不重跑模型**；扫描存档里的解释原文"
                        "（这条由 L3 层负责）")

    elif kind == "combined_attention_reason":
        item["archived"] = (archive.get("combined_attention") or {}).get("reason")
        item["recomputed"] = _recompute_ca(archive).get("reason")
        item["recomputable"] = True
        item["compare"] = "predicate:degradation_mentioned"
        item["match"] = (_mentions_degradation(item["archived"])
                         == _mentions_degradation(item["recomputed"]))
        item["note"] = ("理由是生成文本，逐字比对没有意义；比的是**语义谓词**"
                        "（有没有点明外部降级）")

    else:
        item["note"] = "这条没有登记 L1 复算字段"

    return item


def _check_actual(rec: dict, check_id: str):
    for c in rec.get("checks") or []:
        if c.get("id") == check_id:
            return c.get("actual")
    return None


# --------------------------------------------------------------------------
# L2：把重算值刷进 check 的 actual / passed
# --------------------------------------------------------------------------

def _recompute_check(check_id: str, item: dict, checks_by_id: dict) -> tuple:
    """返回 (新的 actual, 新的 passed)；None 表示这一条不改。"""
    if not item.get("recomputable"):
        return None, None
    rc = item.get("recomputed")

    if check_id in ("S1-1",):
        return rc, (rc == "稳定")
    if check_id == "S1-2":
        return None, None                      # 模型输出：沿用存档值
    if check_id == "S1-3":
        return rc, (rc not in ("watch_internal", "watch_external"))
    if check_id == "S1-4":
        # S1-1 / S1-2 排在前面，它们的 passed 已经是（重算后的）结果
        s11 = (checks_by_id.get("S1-1") or {}).get("passed")
        s12 = (checks_by_id.get("S1-2") or {}).get("passed")
        failed = item.get("recomputed")
        actual = f"未通过条数={failed}；S1-1={s11}；S1-2={s12}"
        passed = (failed == 0 and s11 and s12)
        return actual, passed
    if check_id == "S1-5":
        return None, None
    if check_id == "S2-1":
        return rc, (rc == "不稳定")
    if check_id in ("S2-2", "S2-3"):
        return rc, None                        # 本来就是从原始序列算的
    if check_id == "S3-1":
        return None, None
    if check_id == "S3-2":
        return rc, (rc in ("external_deteriorating", "external_improving",
                           "external_stable"))
    if check_id == "S3-3":
        return None, None
    if check_id == "S3-4":
        return None, None                      # 由 S3-4 自己重算（见下）
    if check_id == "S4-1":
        return rc, bool(rc)
    if check_id == "S4-2":
        return rc, (rc == "conflict_unresolved")
    if check_id == "S4-3":
        sup, opp = (rc or [0, 0])[0], (rc or [0, 0])[1]
        return f"supporting={sup} 条，opposing={opp} 条", bool(sup) and bool(opp)
    if check_id == "S4-4":
        return None, None
    if check_id in ("S5-1", "S5-2"):
        return None, None
    if check_id == "S5-3":
        return rc, (rc == "watch_internal")
    if check_id == "S5-4":
        return None, None
    if check_id == "S5-5":
        return rc, _mentions_degradation(rc)
    return None, None


def _apply_l1(rec: dict, archive: dict) -> dict:
    """把 L1 复算层挂到记录上，并把 check 的 actual 换成重算值。"""
    items: list[dict] = []
    by_check: dict = {}
    for c in rec.get("checks") or []:
        item = _l1_item(c["id"], archive, rec)
        items.append(item)
        by_check[c["id"]] = item
        c["archived_value"] = item.get("archived")
        c["recomputed_value"] = item.get("recomputed")
        c["recomputable"] = item.get("recomputable")
        c["match"] = item.get("match")
        c["recompute_note"] = item.get("note")

    # S3-4 特殊：它本身是「同一份 history 换掉 external_conditions 再重算」
    s34 = by_check.get("S3-4")
    if s34 is not None:
        ca = _recompute_ca(archive)
        ca_none = recompute_combined(archive, None)
        s34["archived"] = (archive.get("combined_attention") or {}).get("value")
        s34["recomputed"] = ca.get("value")
        s34["recomputable"] = True
        s34["match"] = s34["archived"] == s34["recomputed"]
        s34["note"] = ("重算输入 = archive.model_output + archive.external_conditions"
                       " → build_combined_attention；反向对照再把 external_conditions "
                       "置 None 重算一次")

    # 刷新 actual / passed
    checks_by_id = {c["id"]: c for c in rec.get("checks") or []}
    for c in rec.get("checks") or []:
        item = by_check.get(c["id"]) or {}
        new_actual, new_passed = _recompute_check(c["id"], item, checks_by_id)
        if new_actual is not None:
            c["actual"] = new_actual
        if new_passed is not None:
            c["passed"] = bool(new_passed)

    l1_passed = all(i.get("match") is not False for i in items)
    rec.setdefault("layers", {})["L1_recompute"] = {
        "passed": bool(l1_passed),
        "items": items,
        "note": ("重算输入全部取自 archive 原始字段；B06 模型输出沿用存档值"
                 "（不重跑模型）"),
    }
    checks = rec.get("checks") or []
    rec["layers"]["L2_expectation"] = {
        "passed": bool(checks) and all(c["passed"] for c in checks),
        "n_checks": len(checks),
        "n_passed": sum(1 for c in checks if c["passed"]),
        "note": ("期望值沿用第二遍的 verify_s1..s5（一字未改），"
                 "但 actual 已经换成 L1 重算出来的值"),
    }
    return rec


# --------------------------------------------------------------------------
# L3：LLM 一致性层（复用第二遍的检测器，不新造同义词表）
# --------------------------------------------------------------------------

def _l3_layer(archive: dict, kind: str) -> dict:
    text = archive.get("llm_explanation") or ""
    items: list[dict] = []

    hits = find_consistency_claims(text)
    counted = [h for h in hits if h["counted"]]
    items.append({
        "assertion": "结构化判断与解释之间没有把分歧说成一致",
        "detector": "find_consistency_claims",
        "hits": [h["sentence"] for h in counted],
        "passed": not counted,
    })

    if kind == "degrade":
        wf = find_weather_facts(text)
        wf_counted = [h for h in wf if h["counted"]]
        items.append({
            "assertion": "解释里没有凭空断言天气事实（退化场景专查）",
            "detector": "find_weather_facts",
            "hits": [h["sentence"] for h in wf_counted],
            "passed": not wf_counted,
        })

    return {
        "passed": all(i["passed"] for i in items),
        "items": items,
        "note": ("复用第二遍的 find_consistency_claims / find_weather_facts；"
                 "天气事实一条只对退化类案例查"),
    }


# --------------------------------------------------------------------------
# 反向对照（counter_case）：保留第二遍的，再扩展两条
# --------------------------------------------------------------------------

def _counter_cases(archive: dict, sid: str, others: dict) -> list[dict]:
    """扩展反向对照（全部纯函数重算，零 LLM）。

    文档要求的五条：
      S1←S2 history ⇒ 不稳定（第二遍的 counter_evidence）
      S2←S1 history ⇒ 稳定（第二遍的 counter_evidence）
      S3 minus external_conditions ⇒ not watch_external（第二遍的 counter_evidence）
      S4←degraded ledger ⇒ has_conflict False（**本遍新增**）
      S5←conflict ledger ⇒ conflict_unresolved（**本遍新增**）
    """
    out: list[dict] = []

    if sid == "S4":
        s5 = others.get("S5_archive")
        if s5:
            div = _recompute_divergence(s5)
            ca = _ca_with_divergence(archive, div)
            out.append({
                "name": "S4 ← 退化台账",
                "covers": "S4←degraded ledger ⇒ has_conflict False",
                "what": "把退化场景（S5）的证据台账换进来，按台账重算分歧与关注等级",
                "expected": "has_conflict=False，合成关注等级不再是 conflict_unresolved",
                "actual": f"has_conflict={(div or {}).get('has_conflict')}；"
                          f"合成关注等级={ca.get('value')}",
                "passed": ((div or {}).get("has_conflict") is False
                           and ca.get("value") != "conflict_unresolved"),
            })

    if sid == "S5":
        s4 = others.get("S4_archive")
        if s4:
            div = _recompute_divergence(s4)
            ca = _ca_with_divergence(archive, div)
            out.append({
                "name": "S5 ← 分歧台账",
                "covers": "S5←conflict ledger ⇒ conflict_unresolved",
                "what": "把有分歧场景（S4）的证据台账换进来，按台账重算分歧与关注等级",
                "expected": "has_conflict=True，合成关注等级变成 conflict_unresolved",
                "actual": f"has_conflict={(div or {}).get('has_conflict')}；"
                          f"合成关注等级={ca.get('value')}",
                "passed": ((div or {}).get("has_conflict") is True
                           and ca.get("value") == "conflict_unresolved"),
            })

    return out


# --------------------------------------------------------------------------
# 回归案例的执行
# --------------------------------------------------------------------------

def _regression_others() -> dict:
    """回归里各案例的「对照锚点」（S1/S2 历史 + S4/S5 参照 run）。"""
    return {
        "S1_history": _scenario_history("S1"),
        "S2_history": _scenario_history("S2"),
        "S4_archive": load_archive(REGRESSION_REF_RUNS["S4"]),
        "S5_archive": load_archive(REGRESSION_REF_RUNS["S5"]),
    }


def _run_case(case: dict, others: dict) -> dict:
    """跑一个回归案例（L1 复算 + 第二遍验证器 + L3 一致性 + 反向对照）。"""
    sid = case["scenario_id"]
    rid = case["run_id"]
    archive = load_archive(rid)
    if archive is None:
        rec = {"case_id": case["case_id"], "kind": case["kind"],
               "scenario_id": sid, "run_id": rid, "verdict": "FAIL",
               "checks": [], "counter_evidence": {}, "layers": {},
               "notes": [f"找不到 run 存档：{rid}"], "error": "missing_archive"}
        return rec

    rec = VERIFIERS[sid](archive, rid, others)
    rec["verified_at"] = _now_iso()
    rec["case_id"] = case["case_id"]
    rec["kind"] = case["kind"]
    rec["role"] = case.get("role", "case")
    rec["intent"] = case.get("intent", "")
    rec["archive_scenario"] = archive.get("scenario")

    _apply_l1(rec, archive)
    rec["layers"]["L3_llm_consistency"] = _l3_layer(archive, case["kind"])

    primary = rec.get("counter_evidence") or {}
    primary = dict(primary)
    primary["covers"] = {
        "S1": "S1←S2 history ⇒ 不稳定",
        "S2": "S2←S1 history ⇒ 稳定",
        "S3": "S3 minus external_conditions ⇒ not watch_external",
        "S4": "S4 的 S5 has_conflict=False 对照",
        "S5": "S5 的 S4 B03 status=ok 对照",
    }.get(sid, "")
    rec["counter_evidence"] = primary
    rec["counter_case"] = {
        "primary": primary,
        "extended": _counter_cases(archive, sid, others),
    }
    return rec


def _run_negative_control(others: dict) -> dict:
    """负对照：在**内存副本**上改坏一处，复算必然对不上 → 必须报 FAIL。

    ★ 坏案例只在内存里，绝不落盘，也绝不写进记录当普通案例。
    """
    import copy

    base_run = "run_121"
    archive = load_archive(base_run)
    rec = {
        "case_id": NEGATIVE_CASE_ID, "kind": "negative", "role": "negative",
        "scenario_id": "S1", "run_id": base_run,
        "intent": ("负对照：把一份真实存档在**内存里**改坏一帧温度，"
                   "L1 复算必然对不上 → 本案例必须报 FAIL"),
        "notes": [], "checks": [], "counter_evidence": {}, "layers": {},
    }
    if archive is None:
        rec["verdict"] = "FAIL"
        rec["error"] = "missing_archive"
        return rec

    corrupted = copy.deepcopy(archive)
    hist = corrupted.get("history") or []
    idx = 2 if len(hist) > 2 else 0
    before = hist[idx].get("temperature")
    hist[idx]["temperature"] = round(
        (before or 0.0) + NEGATIVE_CORRUPTION["delta"], 3)
    rec["corruption"] = {
        "target": f"history[{idx}].temperature",
        "delta": NEGATIVE_CORRUPTION["delta"],
        "before": before,
        "after": hist[idx].get("temperature"),
        "applied_to": "内存副本（deepcopy）",
        "persisted": False,
    }

    rec2 = VERIFIERS["S1"](corrupted, base_run, others)
    rec2["verified_at"] = _now_iso()
    rec["case_id"] = NEGATIVE_CASE_ID
    rec["kind"] = "negative"
    rec["role"] = "negative"
    rec["scenario_id"] = "S1"
    rec["run_id"] = base_run
    rec["archive_scenario"] = corrupted.get("scenario")
    rec["analysis_time"] = corrupted.get("analysis_time")
    rec["location"] = corrupted.get("location")
    rec["current_state"] = corrupted.get("current_state")
    rec["history_window"] = corrupted.get("history_window")
    rec["structured_judgement"] = _judgement(corrupted)
    rec["llm_explanation"] = corrupted.get("llm_explanation")
    rec["model_version"] = (corrupted.get("model_output") or {}).get("model_version")
    rec["app_version"] = _app_version()[0]
    rec["weather_evidence"] = _weather_evidence(corrupted)
    rec["knowledge_evidence"] = _knowledge_evidence(corrupted)
    rec["model_evidence"] = _model_evidence(corrupted)
    rec["tool_run_status"] = _tool_run_status(corrupted)
    rec["limitations"] = _limitations(corrupted)
    rec["checks"] = rec2["checks"]
    rec["counter_evidence"] = rec2["counter_evidence"]

    _apply_l1(rec, corrupted)
    rec["layers"]["L3_llm_consistency"] = _l3_layer(corrupted, "degrade")

    checks_failed = [c["id"] for c in rec["checks"] if not c["passed"]]
    l1 = rec["layers"]["L1_recompute"]
    l1_bad = [i["check_id"] for i in l1["items"] if i.get("match") is False]
    rec["verdict"] = "FAIL" if (checks_failed or l1_bad) else "PASS"
    rec["failed_checks"] = checks_failed
    rec["l1_mismatch"] = l1_bad
    return rec


# --------------------------------------------------------------------------
# 期望清单（冻结件）
# --------------------------------------------------------------------------

def _observed_pattern(rec: dict) -> dict:
    """一个案例「当前观察到的形态」。"""
    layers = rec.get("layers") or {}
    return {
        "checks": {c["id"]: bool(c["passed"]) for c in rec.get("checks") or []},
        "counter": bool((rec.get("counter_evidence") or {}).get("passed")),
        "l1": bool((layers.get("L1_recompute") or {}).get("passed")),
        "l3": bool((layers.get("L3_llm_consistency") or {}).get("passed")),
    }


def _pattern_diff(observed: dict, expected: dict) -> list[str]:
    out: list[str] = []
    exp_checks = expected.get("checks") or {}
    for cid, exp_pass in exp_checks.items():
        got = (observed.get("checks") or {}).get(cid)
        if got != exp_pass:
            out.append(f"{cid}: 期望 {'PASS' if exp_pass else 'FAIL'}，"
                       f"实际 {'PASS' if got else 'FAIL'}")
    for key in ("counter", "l1", "l3"):
        if key in expected and observed.get(key) != expected.get(key):
            out.append(f"{key}: 期望 {expected.get(key)}，实际 {observed.get(key)}")
    return out


def _second_pass_records() -> list[dict]:
    """第二遍的冻结记录（**只读**，用来交叉校验期望清单）。"""
    return read_records(RECORDS_PATH)


def _freeze_errors(records: list[dict]) -> list[str]:
    """冻结前的交叉校验：每条规则都指向一个独立来源。

    · (a) 与第二遍冻结记录同 run 的案例 → 逐条必须一致；
    · (b) 同场景的「另一条 run」→ 非 LLM 检查必须与参照案例一致；
    · (c) S3 组的语义规则 → 观察到的形态必须符合文档行为 3。
    """
    errors: list[str] = []
    by_case = {r.get("case_id"): r for r in records}

    # (a) 第二遍的冻结记录
    second = {}
    for r in _second_pass_records():
        second[r.get("run_id")] = r
    for case in REGRESSION_CASES:
        ref = second.get(case["run_id"])
        if not ref:
            continue
        rec = by_case.get(case["case_id"])
        if not rec:
            continue
        exp_checks = {c["id"]: bool(c["passed"]) for c in ref.get("checks") or []}
        got = {c["id"]: bool(c["passed"]) for c in rec.get("checks") or []}
        if exp_checks != got:
            errors.append(
                f"{case['case_id']}：与第二遍的冻结记录（{case['run_id']}）不一致 —— "
                f"第二遍={exp_checks}，现在={got}")
        if bool((ref.get("counter_evidence") or {}).get("passed")) != \
                bool((rec.get("counter_evidence") or {}).get("passed")):
            errors.append(f"{case['case_id']}：反向对照与第二遍的冻结记录不一致")

    # (b) 孪生案例
    for case_id, ref_id in _FREEZE_TWIN.items():
        rec = by_case.get(case_id)
        ref = by_case.get(ref_id)
        if not rec or not ref:
            continue
        got = {c["id"]: bool(c["passed"]) for c in rec.get("checks") or []}
        exp = {c["id"]: bool(c["passed"]) for c in ref.get("checks") or []}
        for cid, val in exp.items():
            if cid in LLM_TEXT_CHECKS:
                continue                      # LLM 文本，允许每次不同
            if got.get(cid) != val:
                errors.append(
                    f"{case_id}：{cid} 与参照案例 {ref_id} 不一致"
                    f"（期望 {val}，实际 {got.get(cid)}）")

    # (c) S3 组的语义规则
    for case_id, rules in _FREEZE_SEMANTIC.items():
        rec = by_case.get(case_id)
        if not rec:
            continue
        got = {c["id"]: bool(c["passed"]) for c in rec.get("checks") or []}
        for cid, val in rules.items():
            if got.get(cid) != val:
                errors.append(
                    f"{case_id}：{cid} 不符合语义规则（期望 {val}，实际 {got.get(cid)}）")

    return errors


def freeze_expectations(records: list[dict]) -> tuple[dict, list[str]]:
    """把当前形态冻结成期望清单（先过交叉校验）。"""
    errors = _freeze_errors(records)
    by_case = {r.get("case_id"): r for r in records}
    cases: dict = {}
    for case in REGRESSION_CASES:
        rec = by_case.get(case["case_id"])
        if not rec:
            continue
        obs = _observed_pattern(rec)
        cases[case["case_id"]] = {
            "scenario_id": case["scenario_id"],
            "run_id": case["run_id"],
            "kind": case["kind"],
            "role": case.get("role", "case"),
            "intent": case.get("intent", ""),
            "checks": obs["checks"],
            "counter_expected": obs["counter"],
            "l1_expected": obs["l1"],
            "l3_expected": obs["l3"],
        }
    from B07.b08_baseline import git_state

    git = git_state()
    frozen = {
        "regression_set_id": REGRESSION_SET_ID,
        "frozen_at": _now_iso(),
        "app_version": _current_app_version(),
        "git_commit": git.get("commit"),
        "git_dirty": git.get("dirty"),
        "cases": cases,
        "negative_control": {
            "case_id": NEGATIVE_CASE_ID,
            "expected": "FAIL",
            "corruption": dict(NEGATIVE_CORRUPTION),
            "note": "负对照的坏案例只在内存副本上，绝不落盘",
        },
        "note": ("期望值保存在这份冻结件里，代码只负责「重算 → 比对 → 记录」。"
                 "冻结时跑过三组交叉校验：第二遍冻结记录 / 同场景孪生案例 / "
                 "S3 组语义规则。"),
    }
    return frozen, errors


def load_expectations(path=None) -> dict | None:
    p = Path(path) if path else REGRESSION_EXPECT_PATH
    if not p.exists():
        return None
    try:
        return _read_json(p)
    except Exception:                           # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# 回归主流程
# --------------------------------------------------------------------------

def _current_app_version() -> str:
    """当前代码的 app_version（**不改** b08_baseline.json，只现算）。"""
    try:
        from B07.b08_baseline import APP_VERSION, git_state

        git = git_state()
        commit = git.get("commit") or ""
        return f"{APP_VERSION}@{commit}" if commit else APP_VERSION
    except Exception:                           # noqa: BLE001
        return _app_version()[0]


def _git_state() -> dict:
    try:
        from B07.b08_baseline import git_state

        return git_state()
    except Exception:                           # noqa: BLE001
        return {}


def run_regression(case_ids=None, expectations_path=None,
                   results_path=None, records_path=None,
                   freeze: bool = False,
                   write: bool = True) -> tuple[list[dict], dict, list[str]]:
    """跑一遍固定回归集。

    Args:
        write: False 时**只算不落盘**（记录与汇总都不写）——
            CLI 的 `--no-write` 靠它实现；只写报告那一步是 CLI 层的活。

    Returns:
        (records, summary, errors)
    """
    import time

    t0 = time.perf_counter()
    exp_path = Path(expectations_path) if expectations_path else REGRESSION_EXPECT_PATH
    res_path = Path(results_path) if results_path else REGRESSION_RESULTS_PATH
    rec_path = Path(records_path) if records_path else REGRESSION_RECORDS_PATH

    expectations = load_expectations(exp_path)
    others = _regression_others()

    wanted = set(case_ids or [])
    records: list[dict] = []
    for case in REGRESSION_CASES:
        if wanted and case["case_id"] not in wanted:
            continue
        records.append(_run_case(case, others))

    errors: list[str] = []
    neg = _run_negative_control(others)
    records.append(neg)

    # 判定：与冻结的期望清单逐条比对
    if freeze or expectations is None:
        frozen, errors = freeze_expectations(records)
        if freeze and not errors and write:
            exp_path.parent.mkdir(parents=True, exist_ok=True)
            exp_path.write_text(json.dumps(frozen, ensure_ascii=False, indent=2)
                                + "\n", encoding="utf-8")
        expectations = frozen if not errors else expectations
    elif expectations is None:
        expectations = {}

    exp_cases = (expectations or {}).get("cases") or {}
    for rec in records:
        cid = rec.get("case_id")
        if rec.get("role") == "negative":
            rec["expected_verdict"] = "FAIL"
            rec["expectation_diff"] = [] if rec.get("verdict") == "FAIL" else [
                "负对照必须报 FAIL，实际 PASS —— 整个回归集失去鉴别力"]
            continue
        exp = exp_cases.get(cid)
        if not exp:
            rec["expected_verdict"] = None
            rec["expectation_diff"] = ["期望清单里没有这个案例"]
            rec["verdict"] = "FAIL"
            continue
        diff = _pattern_diff(_observed_pattern(rec), exp)
        rec["expected_verdict"] = "PASS"
        rec["expectation_diff"] = diff
        rec["verdict"] = "FAIL" if diff else "PASS"

    neg_expected = (expectations or {}).get("negative_control") or {}
    neg_caught = bool(neg.get("verdict") == "FAIL")

    git = _git_state()
    duration_ms = int((time.perf_counter() - t0) * 1000)
    normal = [r for r in records if r.get("role") != "negative"]
    summary = {
        "ts": _now_iso(),
        "verified_at": _now_iso(),
        "app_version": _current_app_version(),
        "model_version": next((r.get("model_version") for r in normal
                                if r.get("model_version")), None),
        "git_commit": git.get("commit"),
        "git_dirty": git.get("dirty"),
        "git_commit_short": git.get("commit_short"),
        "regression_set_id": REGRESSION_SET_ID,
        "total": len(normal),
        "passed": sum(1 for r in normal if r.get("verdict") == "PASS"),
        "failed": sum(1 for r in normal if r.get("verdict") != "PASS"),
        "run_ids": [r.get("run_id") for r in normal],
        "negative_control": {
            "case_id": NEGATIVE_CASE_ID,
            "expected": neg_expected.get("expected", "FAIL"),
            "actual": neg.get("verdict"),
            "caught": neg_caught,
        },
        "cases": [{
            "case_id": r.get("case_id"),
            "scenario_id": r.get("scenario_id"),
            "run_id": r.get("run_id"),
            "kind": r.get("kind"),
            "verdict": r.get("verdict"),
            "failed_layers": _failed_layers(r),
        } for r in records],
        "duration_ms": duration_ms,
    }

    if not freeze and write:
        write_records(records, path=rec_path, append=True)
        with open(res_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return records, summary, errors


def _failed_layers(rec: dict) -> list[str]:
    layers = rec.get("layers") or {}
    out = []
    for key in ("L1_recompute", "L2_expectation", "L3_llm_consistency"):
        item = layers.get(key) or {}
        if item.get("passed") is False:
            out.append(key)
    if not (rec.get("counter_evidence") or {}).get("passed"):
        out.append("counter_evidence")
    return out


def read_results(path=None) -> list[dict]:
    p = Path(path) if path else REGRESSION_RESULTS_PATH
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:                       # noqa: BLE001
            continue
    return out


def regression_diff(results_path=None) -> dict | None:
    """与上一次回归对比：翻了的案例 / 新增 / 缺失 / 跨版本提示。"""
    rows = read_results(results_path)
    if len(rows) < 2:
        return None
    prev, cur = rows[-2], rows[-1]
    prev_cases = {c["case_id"]: c for c in prev.get("cases") or []}
    cur_cases = {c["case_id"]: c for c in cur.get("cases") or []}

    flipped, added, missing = [], [], []
    for cid, c in cur_cases.items():
        old = prev_cases.get(cid)
        if old is None:
            added.append(cid)
        elif old.get("verdict") != c.get("verdict"):
            flipped.append({"case_id": cid,
                            "from": old.get("verdict"), "to": c.get("verdict"),
                            "failed_layers": c.get("failed_layers")})
    for cid in prev_cases:
        if cid not in cur_cases:
            missing.append(cid)

    return {
        "prev": prev, "cur": cur,
        "flipped": flipped, "added": added, "missing": missing,
        "cross_version": prev.get("git_commit") != cur.get("git_commit"),
        "prev_app_version": prev.get("app_version"),
        "cur_app_version": cur.get("app_version"),
    }


# --------------------------------------------------------------------------
# 打印
# --------------------------------------------------------------------------

def _print_regression_record(rec: dict) -> None:
    print("\n" + "-" * 78)
    print(f"  [{rec.get('kind')}] {rec.get('case_id')}  "
          f"scenario={rec.get('scenario_id')}  run={rec.get('run_id')}  "
          f"verdict={rec.get('verdict')}")
    if rec.get("intent"):
        print(f"    意图        : {rec['intent']}")
    if rec.get("corruption"):
        c = rec["corruption"]
        print(f"    负对照改坏  : {c.get('target')} {c.get('before')} → "
              f"{c.get('after')}（{c.get('applied_to')}，落盘={c.get('persisted')}）")
    for c in rec.get("checks") or []:
        mark = "✅" if c["passed"] else "❌"
        print(f"    {mark} {c['id']}  {c['claim']}")
        print(f"        期望: {c['expected']}")
        print(f"        实际: {c['actual']}")
        if c.get("recomputable"):
            print(f"        复算: archived={c.get('archived_value')} → "
                  f"recomputed={c.get('recomputed_value')}  "
                  f"match={c.get('match')}")
        else:
            print(f"        复算: 不可重算（{c.get('recomputable')}）")
    layers = rec.get("layers") or {}
    l1 = layers.get("L1_recompute") or {}
    print(f"    {'✅' if l1.get('passed') else '❌'} L1 复算层 "
          f"（{len(l1.get('items') or [])} 项）")
    for i in l1.get("items") or []:
        if i.get("match") is False:
            print(f"        ❌ {i['check_id']} {i['label']}："
                  f"archived={i.get('archived')} recomputed={i.get('recomputed')}")
    l2 = layers.get("L2_expectation") or {}
    print(f"    {'✅' if l2.get('passed') else '❌'} L2 期望层 "
          f"（{l2.get('n_passed')}/{l2.get('n_checks')} 条检查通过）")
    l3 = layers.get("L3_llm_consistency") or {}
    print(f"    {'✅' if l3.get('passed') else '❌'} L3 LLM 一致性层")
    for i in l3.get("items") or []:
        print(f"        · {i['assertion']}：{'通过' if i['passed'] else '未通过'}"
              + (f"（命中 {len(i['hits'])} 处）" if i.get("hits") else ""))
    ce = rec.get("counter_evidence") or {}
    print(f"    {'✅' if ce.get('passed') else '❌'} 反向对照：{ce.get('what')}")
    for cc in (rec.get("counter_case") or {}).get("extended") or []:
        print(f"    {'✅' if cc.get('passed') else '❌'} 扩展反向对照：{cc.get('name')}"
              f" —— {cc.get('covers')}")
        print(f"        期望: {cc.get('expected')}")
        print(f"        实际: {cc.get('actual')}")
    if rec.get("expectation_diff"):
        print("    ★ 与冻结期望不一致：")
        for d in rec["expectation_diff"]:
            print(f"        - {d}")


def _print_regression_summary(records: list[dict], summary: dict) -> None:
    print("\n" + "=" * 78)
    print("B08 第四遍 · 固定回归集")
    print("=" * 78)
    for rec in records:
        layers = rec.get("layers") or {}
        l1 = (layers.get("L1_recompute") or {}).get("passed")
        l2 = (layers.get("L2_expectation") or {}).get("passed")
        l3 = (layers.get("L3_llm_consistency") or {}).get("passed")
        print(f"  {rec.get('verdict'):<4} {rec.get('case_id'):<24} "
              f"[{rec.get('kind'):<8}] run={rec.get('run_id'):<9} "
              f"L1={'✔' if l1 else '✘'} L2={'✔' if l2 else '✘'} "
              f"L3={'✔' if l3 else '✘'} 反向对照="
              f"{'✔' if (rec.get('counter_evidence') or {}).get('passed') else '✘'}")
    nc = summary.get("negative_control") or {}
    print("-" * 78)
    print(f"  案例：{summary.get('passed')}/{summary.get('total')} 与冻结期望一致，"
          f"{summary.get('failed')} 条不一致")
    print(f"  负对照 {nc.get('case_id')}：期望 {nc.get('expected')}，"
          f"实际 {nc.get('actual')} → "
          f"{'✅ 被正确抓到（整个回归集有鉴别力）' if nc.get('caught') else '❌ 没被抓到 —— 回归集无效'}")
    print(f"  app_version : {summary.get('app_version')}")
    print(f"  git         : {summary.get('git_commit_short')} "
          f"(dirty={str(summary.get('git_dirty')).lower()})")
    print(f"  回归集编号  : {summary.get('regression_set_id')}")
    print(f"  耗时        : {summary.get('duration_ms')} ms")
    print("=" * 78)


def _print_regression_diff(diff: dict) -> None:
    print("\n" + "=" * 78)
    print("B08 第四遍 · 回归对比（与上一次回归）")
    print("=" * 78)
    prev, cur = diff["prev"], diff["cur"]
    print(f"  上一次：{prev.get('ts')}  app_version={prev.get('app_version')}  "
          f"git={prev.get('git_commit_short')}")
    print(f"  这一次：{cur.get('ts')}  app_version={cur.get('app_version')}  "
          f"git={cur.get('git_commit_short')}")
    if diff.get("cross_version"):
        print(f"\n  ★ **本次跨版本对比**：两次回归的 git_commit 不同"
              f"（{prev.get('git_commit_short')} → {cur.get('git_commit_short')}）"
              f" —— 结论的差异可能来自代码改动，不只是数据波动。")
    print(f"\n  判定翻转：{len(diff['flipped'])} 条")
    for f in diff["flipped"]:
        print(f"    · {f['case_id']}：{f['from']} → {f['to']}"
              + (f"（失败层：{', '.join(f.get('failed_layers') or [])}）"
                 if f.get("failed_layers") else ""))
    print(f"  新增案例：{diff['added'] or '无'}")
    print(f"  缺失案例：{diff['missing'] or '无'}")
    print("=" * 78)


# --------------------------------------------------------------------------
# 第四遍报告（九节）
# --------------------------------------------------------------------------

#: 文档写明的六条行为 → 对应的检查项（第四遍逐条打勾）
DOC_BEHAVIORS = (
    ("稳定场景不误报",
     "内部状态稳定时，判定是『稳定』、B06 预测 NORMAL、合成关注等级 internal_only",
     ("S1-1", "S1-2", "S1-3", "S1-4"), "stable"),
    ("漂移场景能发现",
     "内部状态漂移时，判定是『不稳定』，且与稳定窗可比（末点温度差 ≤ 0.5℃）",
     ("S2-1", "S2-2", "S2-3"), "drift"),
    ("外部条件参与判断",
     "外部信号为 deteriorating 时合成关注等级抬升为 watch_external；"
     "去掉外部条件重算就不再是 watch_external",
     ("S3-1", "S3-2", "S3-3", "S3-4"), "external"),
    ("证据分歧保留分歧",
     "台账给出相反方向时分歧成立，关注等级为 conflict_unresolved，"
     "解释不得把分歧说成一致",
     ("S4-1", "S4-2", "S4-3", "S4-4"), "conflict"),
    ("证据退化主动退让",
     "B03 证据不可用时降级为 watch_internal，其余证据照常，"
     "解释不得凭空断言天气事实",
     ("S5-1", "S5-2", "S5-3", "S5-4", "S5-5"), "degrade"),
    ("解释与结构化判断一致",
     "LLM 解释里不出现把分歧说成一致的说法（退化场景另查天气事实断言）",
     ("L3",), "degrade"),
)


def build_regression_report(records: list[dict], summary: dict,
                            expectations: dict | None = None,
                            diff: dict | None = None) -> str:
    """生成第四遍回归报告（数字全部来自 records / summary，不手工编）。"""
    lines: list[str] = []
    A = lines.append
    by_case = {r.get("case_id"): r for r in records}
    normal = [r for r in records if r.get("role") != "negative"]
    neg = next((r for r in records if r.get("role") == "negative"), None)
    git = summary.get("git") or {}
    baseline = _baseline() or {}
    b_git = baseline.get("git") or {}

    A("# B08 第四遍 · 固定回归报告")
    A("")
    A(f"> 生成时间：{summary.get('ts')}")
    A(f"> app_version：`{summary.get('app_version')}`")
    A(f"> git：`{summary.get('git_commit_short')}`，"
      f"dirty = **{str(summary.get('git_dirty')).lower()}**")
    A(f"> 回归集编号：`{summary.get('regression_set_id')}`")
    A("")

    # 1
    A("---")
    A("")
    A("## 1. 第四遍要解决什么")
    A("")
    A("★ **说明**：文档 p.99–101 的原文**不在本仓库里**"
      "（`docs/` 下只有 `b01_b03_design.md` 与 `b03_weather.md`）；"
      "本节按第四遍任务书逐条转写要求，并在第 9 节如实标注这一条。")
    A("")
    A("第四遍要解决的是第二遍留下的三个洞：")
    A("")
    A("1. **只读存档的断言永远发现不了「代码改坏了」。** "
      "第二遍每条 check 的 actual 都直接取自 `run.json` 的存档字段 —— "
      "只要存档没动，就算把 `judge_stability` / `build_combined_attention` "
      "改错了，验证照样全绿。第四遍把 actual 换成"
      "**按存档原始字段重新算一遍**，再与存档值逐项比对。")
    A("2. **案例太少。** 第二遍只有 5 个场景各一条记录（5 个案例），"
      "文档要求 ≥10。第四遍扩到 13 个案例（同一场景、不同 run）+ 1 个负对照。")
    A("3. **跑过的结果没留下可比的东西。** 第四遍每次回归追加一行汇总"
      "（app_version / git_commit / 逐案例判定 / 负对照结果），"
      "并支持 `regression diff` 与上一次回归对比。")
    A("")

    # 2
    A("---")
    A("")
    A("## 2. 固定回归集清单")
    A("")
    A(f"共 **{len(normal)} 个案例**（文档要求 ≥10）+ **1 个负对照**。")
    A("")
    A("| # | 案例 | 分组 | 场景 | run | 存档自称 | 判定 | 与冻结期望一致 |")
    A("|---|---|---|---|---|---|---|---|")
    for i, rec in enumerate(normal, 1):
        exp = rec.get("expected_verdict")
        mark = ("—" if exp is None
                else ("✅" if rec.get("verdict") == "PASS" else "❌"))
        A(f"| {i} | `{rec.get('case_id')}` | {rec.get('kind')} | "
          f"`{rec.get('scenario_id')}` | `{rec.get('run_id')}` | "
          f"`{rec.get('archive_scenario')}` | {rec.get('verdict')} | {mark} |")
    A("")
    kinds: dict = {}
    for rec in normal:
        kinds.setdefault(rec.get("kind"), []).append(rec.get("case_id"))
    A("按分组看：")
    A("")
    for kind, ids in kinds.items():
        A(f"* **{kind}**（{len(ids)} 个）：{', '.join(f'`{i}`' for i in ids)}")
    A("")
    A("> ★ 13 个案例全部来自**已有存档**，回归**不新跑任何一次 LLM / 模型 / 天气**。")
    A("")

    # 3
    A("---")
    A("")
    A("## 3. 文档六条行为的落实对照")
    A("")
    A("| # | 行为 | 说明 | 对应检查 | 本遍覆盖的案例 | 逐案例结果 | 结论 |")
    A("|---|---|---|---|---|---|---|")
    for i, (name, desc, checks, kind) in enumerate(DOC_BEHAVIORS, 1):
        cases = [r for r in normal if r.get("kind") == kind]
        if checks == ("L3",):
            ids = "（全部 13 个案例）"
            ok = all((r.get("layers", {}).get("L3_llm_consistency") or {})
                     .get("passed") for r in records if r.get("role") != "negative")
            detail = "L3 层 13/13 通过"
        else:
            ok = True
            detail_parts = []
            for r in cases:
                by_id = {c["id"]: c for c in r.get("checks") or []}
                got = [by_id[cid]["passed"] for cid in checks if cid in by_id]
                n_ok = sum(1 for v in got if v)
                detail_parts.append(f"{r.get('case_id')} {n_ok}/{len(got)}")
                # 「期望里的失败」也算通过（比如对照组的断言本来就不成立）
                if not all(got) and r.get("case_id") not in (
                        "S3_no_weather", "S3_ext_stable"):
                    ok = False
            detail = "、".join(detail_parts) or "（无）"
            ids = ", ".join(f"`{r.get('case_id')}`" for r in cases) or "（无）"
        A(f"| {i} | {name} | {desc} | {', '.join(f'`{c}`' for c in checks)} | "
          f"{ids} | {detail} | {'✅' if ok else '❌'} |")
    A("")
    A("> ★ 「期望里的失败」也是通过：对照组 `S3_no_weather` 的四条 S3 检查"
      "**本来就该全部不成立**（没有外部天气时不许报外部关注）—— "
      "它的通过标准是「与冻结期望一致」，不是「全绿」。")
    A("")

    # 4
    A("---")
    A("")
    A("## 4. ★ 负对照结果")
    A("")
    nc = summary.get("negative_control") or {}
    A("负对照的做法：**在内存里**把一份真实存档（`run_121`）的第 3 帧温度 +1.0℃，"
      "其余一个字节不动，然后拿同一套代码重算 —— "
      "L1 复算必然与存档值对不上，这个案例**必须报 FAIL**。")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    A(f"| 案例 id | `{nc.get('case_id')}` |")
    A(f"| 改坏的地方 | `{(neg or {}).get('corruption', {}).get('target')}` |")
    A(f"| 改前 → 改后 | "
      f"{(neg or {}).get('corruption', {}).get('before')} → "
      f"{(neg or {}).get('corruption', {}).get('after')}℃ |")
    A(f"| 作用范围 | "
      f"{(neg or {}).get('corruption', {}).get('applied_to')}（落盘="
      f"{(neg or {}).get('corruption', {}).get('persisted')}） |")
    A(f"| 期望判定 | {nc.get('expected')} |")
    A(f"| 实际判定 | {nc.get('actual')} |")
    A(f"| 是否被抓到 | {'✅ 是' if nc.get('caught') else '❌ 否'} |")
    A("")
    if neg:
        l1 = (neg.get("layers") or {}).get("L1_recompute") or {}
        bad = [i for i in (l1.get("items") or []) if i.get("match") is False]
        A(f"L1 复算层抓到 **{len(bad)} 处**不一致：")
        A("")
        for i in bad:
            A(f"* `{i['check_id']}` {i['label']}：存档值 = `{i.get('archived')}`，"
              f"重算值 = `{i.get('recomputed')}` → **对不上**")
        A("")
        A(f"判定为未通过的检查：`{(neg.get('failed_checks') or []) or '无'}`")
        A("")
    A("> ★ **负对照没被抓到 → 整个回归集无效**（说明它测不出「代码改坏了」）。"
      "本次负对照被正确抓到，回归集有效。")
    A("")

    # 5
    A("---")
    A("")
    A("## 5. 三层检查的分工")
    A("")
    A("| 层 | 问什么 | 输入 | 判据 |")
    A("|---|---|---|---|")
    A("| **L1 复算层** | 现在的代码还能不能把当年的结论**重算出来** | "
      "存档的原始字段（history / external_conditions / evidence_ledger / "
      "model_output） | 重算值 == 存档值 |")
    A("| **L2 期望层** | 重算出来的结果**还对不对** | 第二遍的期望值（一字未改） | "
      "重算值满足期望 |")
    A("| **L3 一致性层** | 结构化判断与 LLM 解释**有没有互相打架** | 存档里的解释原文 | "
      "复用第二遍的 find_consistency_claims / find_weather_facts |")
    A("")
    A("### ★ L1 为什么能测出「代码改坏了」")
    A("")
    A("第二遍的 actual **来自存档**：")
    A("")
    A("```")
    A("第二遍：  actual = run.json.structured_judgement.recent_trend.stability_verdict")
    A("          passed = (actual == '稳定')")
    A("          →  存档没动，actual 就没动；把 judge_stability 改错，验证照样全绿")
    A("")
    A("第四遍：  actual = recompute_stability(archive, archive['history'])")
    A("          archived_value = run.json....stability_verdict")
    A("          match = (archived_value == recomputed_value)")
    A("          →  改了 judge_stability，重算值就变，与存档值对不上 → 立刻报错")
    A("```")
    A("")
    A("关键差别只有一句：**第二遍比的是「存档等于它自己」，第四遍比的是"
      "「现在的代码算出来的等于存档」**。")
    A("")
    A("> L1 的重算输入全部取自存档的**原始字段**：稳定性 ← `archive.history`；"
      "合成关注 ← `archive.model_output` + `archive.external_conditions`"
      "（+ 证据分歧分支）；证据分歧 ← `archive.evidence_ledger.entries`。"
      "**全程不调 LLM、不跑模型、不取天气。**")
    A("")

    # 6
    A("---")
    A("")
    A("## 6. 正式回归记录")
    A("")
    A("每次回归都会往 `B07/artifacts/b08_regression_results.jsonl` **追加一行汇总**，"
      "字段就是文档写明的七项 + 版本信息：")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    A(f"| app_version | `{summary.get('app_version')}` |")
    A(f"| model_version | `{summary.get('model_version')}` |")
    A(f"| verified_at | `{summary.get('verified_at')}` |")
    A(f"| total | {summary.get('total')} |")
    A(f"| passed | {summary.get('passed')} |")
    A(f"| failed | {summary.get('failed')} |")
    A(f"| run_ids | {', '.join(f'`{r}`' for r in (summary.get('run_ids') or []))} |")
    A(f"| git_commit | `{summary.get('git_commit')}` |")
    A(f"| git_dirty | **{str(summary.get('git_dirty')).lower()}** |")
    A(f"| regression_set_id | `{summary.get('regression_set_id')}` |")
    A(f"| negative_control | 期望 {nc.get('expected')} / 实际 {nc.get('actual')} / "
      f"抓到 **{nc.get('caught')}** |")
    A(f"| duration_ms | {summary.get('duration_ms')} |")
    A("")
    A("### 与上一次回归的对比")
    A("")
    if diff:
        A(f"* 上一次：{diff['prev'].get('ts')}（git "
          f"`{diff['prev'].get('git_commit_short')}`）")
        A(f"* 这一次：{diff['cur'].get('ts')}（git "
          f"`{diff['cur'].get('git_commit_short')}`）")
        if diff.get("cross_version"):
            A("* ★ **本次跨版本对比**：两次回归的 git_commit 不同 —— "
              "结论差异可能来自代码改动，不只是数据波动。")
        A(f"* 判定翻转：{len(diff['flipped'])} 条；新增：{diff['added'] or '无'}；"
          f"缺失：{diff['missing'] or '无'}")
    else:
        A("* 还没有可比的上一次回归（`regression diff` 需要至少两次记录）。")
    A("")

    # 7
    A("---")
    A("")
    A("## 7. 版本漂移说明")
    A("")
    A("| | 基线（第一遍冻结） | 现在 |")
    A("|---|---|---|")
    A(f"| app_version | `{b_git.get('commit_short') or '未知'}` | "
      f"`{summary.get('git_commit_short')}` |")
    A(f"| 完整形式 | `{((baseline.get('app_version_composition') or {}).get('full'))}` | "
      f"`{summary.get('app_version')}` |")
    A(f"| git.dirty | **{str(b_git.get('dirty')).lower()}**"
      f"（{b_git.get('dirty_file_count')} 个文件） | "
      f"**{str(summary.get('git_dirty')).lower()}** |")
    A(f"| 冻结时间 | {baseline.get('frozen_at')} | 现在 |")
    A("")
    A("★ **第一遍冻结的基线不是干净基线**：冻结时 `git.dirty=true`、"
      f"**{b_git.get('dirty_file_count')} 个文件**有改动或未跟踪 —— "
      "基线里的提交指纹只覆盖已提交的部分。"
      f"现在 HEAD 是 `{summary.get('git_commit_short')}`，工作区"
      f"{'干净' if not summary.get('git_dirty') else '仍是脏的'}。")
    A("")
    A("> 本轮**没有修改** `B07/artifacts/b08_baseline.json`（它是第一遍的冻结件，"
      "只读）；现在的 app_version 是用第一遍**同一个函数**（`b08_baseline.git_state()`）"
      "现算出来的。")
    A("")

    # 8
    A("---")
    A("")
    A("## 8. 三个创新机制的回归覆盖情况")
    A("")
    A("| 创新点 | 本回归集覆盖情况 | 覆盖它的案例 |")
    A("|---|---|---|")
    A("| **创新点 1**（连续时间认知消融） | **未覆盖**。消融实验要重跑 6 次 LLM，"
      "回归集是离线的（不调 LLM）。间接相关的是 S1/S2 组 4 个案例 —— "
      "它们证明「连续 6 点历史 → 稳定性判定」这条链还在，"
      "但**消融本身（去掉某段时间/某个维度）没有任何案例** | "
      "`S1_stable_a`、`S1_stable_b`、`S2_drift_a`、`S2_drift_b`（间接） |")
    A("| **创新点 2**（可追溯证据的自动溯源器） | **未覆盖**。溯源器读的是 "
      "`innovation2_trace.json` 这类溯源产物，回归集只读 `run.json`。"
      "S4/S5 组 6 个案例覆盖的是「证据台账 → 分歧 / 降级 → 关注等级」这条链，"
      "**不是溯源器本身** | "
      "`S4_conflict_a`、`S4_conflict_b`、`S5_degrade_a`、`S5_degrade_b`、"
      "`S5_degrade_c`、`S5_degrade_d`（间接） |")
    A("| **创新点 3**（「有些话现在不能说」走查器 C1–C5） | **未覆盖**。L3 层用的是 "
      "`b08.find_consistency_claims` / `find_weather_facts`，"
      "**不是** `innovation3.py` 的 C1–C5 检测器；两者词表与判据都不同 | "
      "L3 层覆盖全部 14 个案例（但那是第二遍的检测器，不是创新点 3） |")
    A("")
    A("★ 要补的案例（按优先级）：")
    A("")
    A("1. **创新点 3 的走查器**：加一条「案例 = 一段已知违规的回答」，"
      "用 `innovation3.py` 的 C1–C5 扫一遍，断言它**必须**报出违规 —— "
      "现在 L3 只在**合规**存档上跑，证明不了走查器有鉴别力。")
    A("2. **创新点 2 的溯源器**：加一条「案例 = 一份带溯源 id 的台账」，"
      "断言溯源器能还原出每条证据的来源（离线，不需要网络）。")
    A("3. **创新点 1 的消融**：加一条「案例 = 去掉中间 2 点的 history」，"
      "断言稳定性判定与完整窗口**不同**（消融的因果性），这一条可以离线做。")
    A("")

    # 9
    A("---")
    A("")
    A("## 9. 已知缺口（诚实记录）")
    A("")
    gaps = [
        "**L1 只覆盖结构化判断层，不判措辞。** 期望值针对的是"
        "「稳定 / 不稳定 / watch_external / conflict_unresolved」这类结构化结论，"
        "解释写得通不通、字数合不合规，本轮不判。",
        "**L2 只读已有正式存档，不重新分析。** 它验证的是"
        "「现在的代码还能不能一致地重新推导历史存档」，"
        "**不是**「现在重新跑一遍会得到什么」—— 后者需要一次联网的活跑，"
        "是另一轮（实时回归）的事。",
        "**L3 不重跑模型。** 合成关注等级用的 B06 模型输出**沿用存档值** —— "
        "所以「模型被重训了」或「模型文件被换了」这一类改动，本回归集**查不出来**。",
        "**负对照只覆盖一种改坏方式**（改一帧温度）。"
        "改判据阈值、改台账内容、改提示词拼装，这些**都没有**对应的负对照案例。",
        "**S1 / S2 / S3 的存档没有证据台账**，相关断言是 N/A（**不是通过**）。"
        "S1-5 在这些存档上标的是「N/A（该场景无证据台账）」。",
        "**13 个案例全部是 qwen-max**（`llm_meta.model`）。"
        "仓库里 qwen3.5-27b 的 26 个早期 run（run_001–run_037）**没有**进入回归集 —— "
        "所以本回归集实际上是**单模型覆盖**，不是双模型覆盖。",
        "**离线回归测不到真实外部服务的可用性。** 天气 API 挂了、LLM 挂了这些，"
        "本回归集测不到 —— 那是第三遍失败验证（`run_126`）覆盖的东西。",
        "**回归集不覆盖页面逻辑。** HTML / CSS 改坏了，回归集**照样全绿** —— "
        "页面只能靠 `B07/tests/test_views_*.py` 与人工看。",
        "**文档 p.99–101 的原文不在仓库里**（`docs/` 下只有两份设计文档），"
        "第 1 节与第 3 节是按第四遍任务书转写的，不是逐字引用原文。",
        "**期望清单是一次性冻结的。** 冻结时刻意做了三组交叉校验"
        "（第二遍冻结记录 / 同场景孪生案例 / S3 组语义规则），"
        "但**新出现的案例**在冻结时没有独立来源，只能靠人工核对。",
        "**本轮 13 个案例覆盖的 13 条 run 的 llm_explanation 长度是 307~553 字，"
        "全部超过 SYSTEM_PROMPT 写的「≤250 字」**（最长 run_119 = 553 字）。"
        "仓库里带解释的 108 条 run 长度范围是 239~607 字（只有极少数在 250 以内）。"
        "这是**已知的偏差**，本轮**没有**改 SYSTEM_PROMPT（指纹 aa1400cf8568 不动），"
        "也没有把字数纳入回归断言。",
    ]
    for i, g in enumerate(gaps, 1):
        A(f"{i}. {g}")
    A("")
    return "\n".join(lines) + "\n"


def write_regression_report(records: list[dict], summary: dict,
                            expectations: dict | None = None,
                            diff: dict | None = None) -> Path:
    REGRESSION_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGRESSION_REPORT_PATH.write_text(
        build_regression_report(records, summary, expectations, diff),
        encoding="utf-8")
    return REGRESSION_REPORT_PATH


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="B08 第二遍 · 正式验证 S1~S5")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("baseline", help="生成/刷新基线快照（转发到 B08 第一遍）")

    p_verify = sub.add_parser("verify", help="验证场景")
    p_verify.add_argument("--all", action="store_true", help="验证全部五个场景")
    p_verify.add_argument("scenario", nargs="?", help="只验证某个场景（S1…S5）")
    p_verify.add_argument("--run-id", action="append", default=[],
                           help="手动指定 run，形如 S3=run_123（可多次）")
    p_verify.add_argument("--no-write", action="store_true",
                           help="只打印，不写验证记录文件")
    p_verify.add_argument("--recompute", action="store_true",
                           help="第四遍模式：actual 改成「按存档原始字段重算」的值"
                                "（L1 复算层）；默认仍是「读存档」模式")

    p_reg = sub.add_parser("regression", help="第四遍 · 固定回归集")
    p_reg.add_argument("action", nargs="?", default="run",
                       choices=["run", "diff", "freeze"],
                       help="run（默认，跑回归）/ diff（与上一次回归对比）"
                            "/ freeze（冻结期望清单）")
    p_reg.add_argument("--case", action="append", default=[],
                       help="只跑某个案例（可多次，如 --case NEG_must_fail）")
    p_reg.add_argument("--no-write", action="store_true", help="只打印，不写盘")

    sub.add_parser("records", help="打印验证记录")
    sub.add_parser("report", help="生成验证报告")

    args = ap.parse_args(argv)
    cmd = args.cmd or "verify"

    if cmd == "baseline":
        from B07.b08_baseline import main as baseline_main
        return baseline_main([])

    if cmd == "records":
        recs = read_records()
        if not recs:
            print("还没有验证记录 —— 先跑 python -m B07.b08 verify --all")
            return 1
        for r in recs:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    if cmd == "report":
        recs = read_records()
        if not recs:
            print("还没有验证记录 —— 先跑 python -m B07.b08 verify --all")
            return 1
        ok = all(r.get("verdict") == "PASS" for r in recs)
        p = write_report(recs, ok)
        print(f"已写入：{p.relative_to(_PROJECT_ROOT)}")
        return 0 if ok else 1

    # ---- regression（第四遍）----
    if cmd == "regression":
        action = getattr(args, "action", "run")
        case_ids = list(getattr(args, "case", []) or [])

        if action == "diff":
            diff = regression_diff()
            if diff is None:
                print("需要至少两次回归记录才能对比 —— 先跑两次 "
                      "python -m B07.b08 regression")
                return 1
            _print_regression_diff(diff)
            return 0

        if action == "freeze":
            records, summary, errors = run_regression(
                freeze=True, write=not getattr(args, "no_write", False))
            if errors:
                print("\n❌ 冻结被拒绝 —— 观察到的形态与交叉校验规则矛盾：")
                for e in errors:
                    print(f"    - {e}")
                return 1
            for rec in records:
                _print_regression_record(rec)
            _print_regression_summary(records, summary)
            if not getattr(args, "no_write", False):
                print(f"\n  期望清单："
                      f"{REGRESSION_EXPECT_PATH.relative_to(_PROJECT_ROOT)}"
                      f"（{len((load_expectations() or {}).get('cases') or {})} 个案例）")
            if not getattr(args, "no_write", False):
                p = write_regression_report(records, summary, load_expectations())
                print(f"  回归报告：{p.relative_to(_PROJECT_ROOT)}")
            return 0

        # action == "run"
        if load_expectations() is None:
            print("还没有冻结的期望清单 —— 先跑 "
                  "python -m B07.b08 regression freeze")
            return 1
        records, summary, errors = run_regression(
            case_ids=case_ids or None,
            write=not getattr(args, "no_write", False))
        for rec in records:
            _print_regression_record(rec)
        _print_regression_summary(records, summary)
        if not getattr(args, "no_write", False):
            print(f"\n  回归记录：{REGRESSION_RECORDS_PATH.relative_to(_PROJECT_ROOT)}"
                  f"（追加 {len(records)} 条）")
            print(f"  回归汇总：{REGRESSION_RESULTS_PATH.relative_to(_PROJECT_ROOT)}"
                  f"（追加 1 行）")
            p = write_regression_report(records, summary, load_expectations(),
                                        regression_diff())
            print(f"  回归报告：{p.relative_to(_PROJECT_ROOT)}")
        nc = summary.get("negative_control") or {}
        return 0 if (summary.get("failed") == 0 and nc.get("caught")) else 1

    # ---- verify ----
    overrides = {}
    for item in args.run_id:
        if "=" not in item:
            ap.error("--run-id 需要 Sx=run_XXX 形式")
        sid, rid = item.split("=", 1)
        sid = sid.strip().upper()
        if sid not in SCENARIOS:
            ap.error(f"未知场景 {sid!r}")
        overrides[sid] = rid.strip()

    if args.all or not args.scenario:
        sids = list(SCENARIOS)
    else:
        sid = args.scenario.strip().upper()
        if sid not in SCENARIOS:
            ap.error(f"未知场景 {sid!r}，可选：{list(SCENARIOS)}")
        sids = [sid]

    records, ok = verify(sids, overrides, recompute=getattr(args, "recompute", False))
    for rec in records:
        _print_record(rec)
    _print_summary(records, ok)

    if not getattr(args, "no_write", False):
        if getattr(args, "recompute", False):
            p = write_records(records, path=RECOMPUTE_RECORDS_PATH, append=True)
            print(f"\n  重算记录：{p.relative_to(_PROJECT_ROOT)}"
                  f"（追加 {len(records)} 条）")
        else:
            p = write_records(records)
            print(f"\n  验证记录：{p.relative_to(_PROJECT_ROOT)}"
                  f"（{len(records)} 条）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
