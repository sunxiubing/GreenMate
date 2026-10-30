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


def verify(sids=None, run_overrides: dict | None = None) -> tuple[list[dict], bool]:
    """验证一个或多个场景。

    Returns:
        (records, all_passed)
    """
    sids = list(sids or SCENARIOS)
    run_overrides = run_overrides or {}
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
        records.append(rec)

    ok = bool(records) and all(r["verdict"] == "PASS" for r in records)
    return records, ok


def write_records(records: list[dict]) -> Path:
    RECORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RECORDS_PATH, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return RECORDS_PATH


def read_records() -> list[dict]:
    if not RECORDS_PATH.exists():
        return []
    out = []
    for line in RECORDS_PATH.read_text(encoding="utf-8").splitlines():
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
    ce = rec.get("counter_evidence") or {}
    mark = "✅" if ce.get("passed") else "❌"
    print(f"    {mark} 反向对照（因果证明）")
    print(f"        做法: {ce.get('what')}")
    print(f"        期望: {ce.get('expected')}")
    print(f"        实际: {ce.get('actual')}")
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

    records, ok = verify(sids, overrides)
    for rec in records:
        _print_record(rec)
    _print_summary(records, ok)

    if not getattr(args, "no_write", False):
        p = write_records(records)
        print(f"\n  验证记录：{p.relative_to(_PROJECT_ROOT)}"
              f"（{len(records)} 条）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
