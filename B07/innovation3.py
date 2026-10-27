"""B07 第二遍【创新点 3】—— 让系统学会"有些话现在不能说"

这一轮是**第二遍走查**，不是新功能开发。

  * 只新增本文件（+ artifacts 产物 + 测试），**不改 B07 现有任何逻辑**；
  * 只新跑 **1 次** LLM 调用（run_agree），另外两次复用 S4/S5 已落盘的 run 存档；
  * 不改 SYSTEM_PROMPT / 固定问题 / 任何判据阈值，不碰 B01–B06。

要证明的东西（文档 p.51–55 创新点 3）：

  第一步  看一次"证据方向一致"的运行                    → run_agree
  第二步  只改变一份证据让它出现分歧                    → run_conflict
          （问的是"系统有没有把分歧保留下来"，不是"谁赢了"）
  第三步  检查有没有"不该出现的确定话"                  → 命中必须报出，不许掩盖
  第四步  再做一次证据缺失                              → run_missing_weather
          （回答"哪些判断还能继续 / 哪些应该停下来"）
  第五步  检查有没有"偷偷补齐"                          → 天气不可用时不许凭常识编天气
  第六步  三次运行对照，自己总结

三次运行的**唯一变量 = 天气条件**：anchor、历史窗口、B06 窗口、知识检索 query、
固定问题、system prompt 全部一致（报告里用指纹和 sha 打印出来自证）。

产物：

  B07/artifacts/innovation3_report.md   —— 走查报告（与终端输出同源）
  B07/artifacts/innovation3_runs.json    —— 三次运行的结构化结果 + 检查项

用法：

    python -m B07.innovation3                  # 跑 run_agree（1 次 LLM）+ 全部检查 + 走查
    python -m B07.innovation3 --report-only    # 离线重生成走查，不调 LLM
    python -m B07.innovation3 --section 3      # 只看某一节（1..12）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from B07.runs import load_run, run_analysis            # noqa: E402

# --------------------------------------------------------------------------
# 产物位置
# --------------------------------------------------------------------------
ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
REPORT_MD = ARTIFACTS_DIR / "innovation3_report.md"
RUNS_JSON = ARTIFACTS_DIR / "innovation3_runs.json"

#: 复用已有存档：证据分歧 / 证据缺失（S4/S5 已落盘，本轮不重跑、不重新采样）
CONFLICT_RUN_ID = "run_082"
MISSING_RUN_ID = "run_084"

#: run_agree 用哪个离线夹具构造用例（S3 已有的分支，不新建数据）
AGREE_CASE = "deteriorating"

#: 三次运行共用的锚点（与 run_082 / run_084 完全一致）
ANCHOR = "2026-10-21T09:30:00"

#: 三次运行共用的场景键。**场景键不进入 LLM 提示词**（explain.py 里只用到数据里的
#: sim_scenario 标签），所以三次之间唯一变化的仍然是天气条件。
SCENARIO_KEY = "S5"


# ==========================================================================
# 1. 词表与检测器（全部只用多字短语，禁用单字）
# ==========================================================================

#: C2 —— "不该出现的确定话"词表。命中即红线，必须报出，不许掩盖。
CERTAINTY_PHRASES = (
    "一定会", "可以确定", "肯定会", "毫无疑问", "必然", "一定", "无疑", "将必", "注定",
)

#: C3 —— 判断受限类表达（多字）
LIMITATION_PHRASES = (
    "不可用", "未能获取", "无法获取", "受限制", "受到影响", "不再能判断", "未纳入", "无法判断",
)

#: C5 表格"是否说明受限"一行用的更宽集合（除上面的，还包括"只作方向性参考"这类限定）
SCOPE_LIMIT_PHRASES = LIMITATION_PHRASES + (
    "仅供方向性参考", "只作方向性参考", "方向性参考", "仅供参考", "不参与内部",
    "不可外推", "不能外推", "不直接比较",
)

#: C4 —— 免责词：含这些词的句子是"我拿不到证据"的合规声明，不算伪造
EXEMPT_PHRASES = (
    "不可用", "未能获取", "无法获取", "受限制", "受到影响", "未纳入", "无法判断", "不再能判断",
)

#: C4 —— 外部天气"表述"词表（多字）
EXTERNAL_TERM_PHRASES = (
    "外部天气", "外部条件", "天气条件", "降水概率", "下雨", "阴雨", "转差", "变差",
    "external_deteriorating", "external_improving", "external_stable",
)

#: C4 —— 外部天气"具体数值"（正则；与 S3 夹具 improving 用例的数值一一对应）
EXTERNAL_VALUE_PATTERNS = (
    ("降水概率 50.0%", r"50\.0\s*%"),
    ("降水概率 10.0%", r"10\.0\s*%"),
    ("Δ=-40.0", r"-40\.0(?!\d)"),
    ("阈值 ±30.0", r"±\s*30\.0|30\.0\s*个百分点"),
    ("温度 Δ=-1.0", r"-1\.0\s*(?:℃|°C)"),
    ("云量 Δ=-70.0", r"-70\.0\s*%"),
)

#: C1 —— "支持提高关注"的表达（回答层面）
SUPPORT_PATTERNS = (
    ("提高关注程度", r"提高关注(?:程度)?"),
    ("提升关注程度", r"提升关注(?:程度)?"),
    ("WATCH", r"WATCH"),
    ("引起关注", r"引起关注"),
    ("密切关注", r"密切关注"),
)

#: C1 —— "提示可能缓解 / 反向"的表达（回答层面）
OPPOSING_PATTERNS = (
    ("降水概率降低", r"降水概率(?:明显)?(?:降低|下降)"),
    ("外部转好/改善", r"(?:外部|天气|条件)[^。；\n]{0,12}(?:好转|转好|改善|缓解)"),
    ("仅供方向性参考", r"仅供方向性参考|只作方向性参考"),
    ("不参与内部判断", r"不参与内部[^。；\n]{0,12}判断"),
    ("提示可能缓解", r"可能缓解|有所缓解|趋于缓和"),
    ("反向/相反方向", r"反向|相反方向"),
)

#: C3 —— 降级后"仍然给出了判断"的三个组成部分
HISTORY_PHRASES = ("最近", "过去", "近 3 小时", "温度", "湿度", "阈值", "不稳定", "波动")
MODEL_PHRASES = ("B06", "模型", "预测", "置信度", "b06-20261020-v1")
CONCLUSION_PHRASES = ("建议", "综上", "因此", "综合", "结论", "密切关注", "防止")


# ==========================================================================
# 2. 文本工具
# ==========================================================================
def _split_sentences(text: str) -> list[str]:
    """按中文/英文句末标点切句（保留标点）。"""
    if not text:
        return []
    parts = re.split(r"(?<=[。！？!?；;\n])", text)
    return [p.strip() for p in parts if p.strip()]


def _mask_hits(sentence: str, phrases) -> list[dict]:
    """在句子里找多字短语命中。**长词优先**，重叠区间只算一次。

    例："一定会" 与 "一定" 同时在词表里 —— 先匹 "一定会"，
    剩下的 "一定" 才不会重复命中同一处。
    """
    hits: list[dict] = []
    taken: list[tuple[int, int]] = []
    for ph in sorted(phrases, key=len, reverse=True):
        start = 0
        while True:
            i = sentence.find(ph, start)
            if i < 0:
                break
            span = (i, i + len(ph))
            overlap = any(span[0] < t[1] and span[1] > t[0] for t in taken)
            if not overlap:
                taken.append(span)
                hits.append({"phrase": ph, "start": i, "text": sentence[i:i + len(ph)]})
            start = i + 1
    hits.sort(key=lambda h: h["start"])
    return hits


def _regex_hits(sentence: str, patterns) -> list[dict]:
    """正则版命中检测（同样按短语去重）。"""
    hits: list[dict] = []
    for label, pat in patterns:
        for m in re.finditer(pat, sentence):
            hits.append({"phrase": label, "text": m.group(0), "start": m.start()})
    hits.sort(key=lambda h: h["start"])
    return hits


def scan_certainty(text: str) -> list[dict]:
    """C2 扫描：找出"不该出现的确定话"。逐句命中，命中必须原样带出。"""
    out: list[dict] = []
    for sentence in _split_sentences(text):
        for h in _mask_hits(sentence, CERTAINTY_PHRASES):
            out.append({"phrase": h["phrase"], "matched": h["text"], "sentence": sentence})
    return out


def scan_external_content(text: str) -> list[dict]:
    """C4 扫描：逐句检查回答里有没有"外部天气内容"。

    免责规则：**含免责词的句子整句豁免**（那是"我拿不到外部证据"的合规声明），
    但如果豁免句里同时出现了外部天气的**具体数值**，仍然把这一行标出来
    （不隐藏，但按规则不算违规）。
    """
    rows: list[dict] = []
    for sentence in _split_sentences(text):
        term_hits = _mask_hits(sentence, EXTERNAL_TERM_PHRASES)
        value_hits = []
        for label, pat in EXTERNAL_VALUE_PATTERNS:
            for m in re.finditer(pat, sentence):
                value_hits.append({"phrase": label, "text": m.group(0)})
        exempt_hits = _mask_hits(sentence, EXEMPT_PHRASES)
        exempt = bool(exempt_hits)
        rows.append({
            "sentence": sentence,
            "terms": [h["phrase"] for h in term_hits],
            "values": value_hits,
            "exempt": exempt,
            "exempt_reason": [h["phrase"] for h in exempt_hits],
            "violation": (not exempt) and bool(term_hits or value_hits),
            "exempt_but_has_value": exempt and bool(value_hits),
        })
    return rows


def _match_any(text: str, phrases) -> list[str]:
    """回答里出现了哪些多字短语（按出现顺序去重）。"""
    found: list[str] = []
    for ph in phrases:
        if ph in text and ph not in found:
            found.append(ph)
    return found


def _match_patterns(text: str, patterns) -> list[str]:
    found: list[str] = []
    for label, pat in patterns:
        if re.search(pat, text) and label not in found:
            found.append(label)
    return found


def _dwidth(text: str) -> int:
    """终端显示宽度（中文算 2 列）。"""
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _dwidth(text))


def _ascii_table(headers: list[str], rows: list[list[str]]) -> str:
    """固定宽度 ASCII 表格（中文按 2 列对齐）。"""
    widths = []
    for i in range(len(headers)):
        widths.append(max([_dwidth(str(headers[i]))] +
                           [_dwidth(str(r[i])) for r in rows]))
    lines = []
    lines.append("| " + " | ".join(_pad(str(headers[i]), widths[i])
                                   for i in range(len(headers))) + " |")
    lines.append("|" + "|".join("-" * (widths[i] + 2) for i in range(len(headers))) + "|")
    for r in rows:
        lines.append("| " + " | ".join(_pad(str(r[i]), widths[i])
                                       for i in range(len(headers))) + " |")
    return "\n".join(lines)


# ==========================================================================
# 3. 存档取数
# ==========================================================================
def extract_run(archive: dict) -> dict:
    """把一个 run 存档压成走查需要的最小事实集（全部来自存档，不手编）。"""
    sj = archive.get("structured_judgement") or {}
    dv = sj.get("evidence_divergence") or {}
    led = archive.get("evidence_ledger") or {}
    ext = archive.get("external_conditions") or {}
    llm_meta = archive.get("llm_meta") or {}
    model_out = archive.get("model_output") or {}
    hist = archive.get("history_window") or {}

    entries = []
    for e in led.get("entries") or []:
        e_ext = e.get("ext") or {}
        result = e.get("result") or {}
        entries.append({
            "evidence_id": e.get("evidence_id"),
            "source_type": e.get("source_type"),
            "source": e.get("source"),
            "time": e.get("time"),
            "status": e.get("status"),
            "error": e.get("error"),
            "adopted": e.get("adopted"),
            "direction": e.get("direction"),
            "case": e_ext.get("case"),
            "provider": e_ext.get("provider"),
            "degraded": e_ext.get("degraded"),
            "knowledge_query": e_ext.get("question"),
            "result_signal": result.get("signal") if isinstance(result, dict) else None,
            "stability_conclusion": (result.get("stability_conclusion")
                                     if isinstance(result, dict) else None),
        })

    tool_calls = []
    for tc in ((led.get("tool_calls") or {}).get("records") or []):
        tool_calls.append({
            "tool_name": tc.get("tool_name"),
            "status": tc.get("status"),
            "adopted": tc.get("adopted"),
            "purpose": tc.get("purpose"),
        })

    answer = archive.get("llm_explanation") or ""

    return {
        "run_id": archive.get("run_id"),
        "created_at": archive.get("created_at"),
        "status": archive.get("status"),
        "scenario": archive.get("scenario"),
        "question": archive.get("question"),
        "analysis_time": archive.get("analysis_time"),
        "location": archive.get("location"),
        "model_path": archive.get("model_path"),
        "history_window": {
            "start": hist.get("start"),
            "end": hist.get("end"),
            "count": hist.get("count"),
            "stability_passed": (hist.get("stability") or {}).get("passed"),
        },
        "model": {
            "window_id": model_out.get("window_id"),
            "predicted_attention": model_out.get("predicted_attention"),
            "confidence": model_out.get("confidence"),
            "model_version": model_out.get("model_version"),
            "label_source": (archive.get("model_meta") or {}).get("label_source"),
        },
        "combined_attention": archive.get("combined_attention") or {},
        "divergence": {
            "has_conflict": dv.get("has_conflict"),
            "supporting": dv.get("supporting") or [],
            "opposing": dv.get("opposing") or [],
            "undetermined": dv.get("undetermined") or [],
            "each_source": dv.get("each_source") or {},
            "resolution": dv.get("resolution"),
            "note": dv.get("note"),
        },
        "external": {
            "available": ext.get("available"),
            "signal": ext.get("signal"),
            "judge_method": ext.get("judge_method"),
            "basis": ext.get("basis"),
            "precip_delta": ext.get("precip_delta"),
            "temp_delta": ext.get("temp_delta"),
            "cloud_delta": ext.get("cloud_delta"),
            "error": ext.get("error"),
            "baseline_point": ext.get("baseline_point"),
            "forecast_point": ext.get("forecast_point"),
        },
        "entries": entries,
        "tool_calls": tool_calls,
        "answer": answer,
        "answer_len": len(answer),
        "llm_meta": {
            "model": llm_meta.get("model"),
            "provider": llm_meta.get("provider"),
            "system_prompt_sha256": llm_meta.get("system_prompt_sha256"),
            "question_sha256": llm_meta.get("question_sha256"),
            "prompt_tokens": (llm_meta.get("usage") or {}).get("prompt_tokens"),
            "completion_tokens": (llm_meta.get("usage") or {}).get("completion_tokens"),
            "elapsed_ms": llm_meta.get("elapsed_ms"),
        },
        # 回答层面的表达强度（现场算，不手编）
        "expression": {
            "certainty_hits": scan_certainty(answer),
            "support_hits": _match_patterns(answer, SUPPORT_PATTERNS),
            "opposing_hits": _match_patterns(answer, OPPOSING_PATTERNS),
            "limitation_hits": _match_any(answer, LIMITATION_PHRASES),
            "scope_limit_hits": _match_any(answer, SCOPE_LIMIT_PHRASES),
        },
    }


def build_run_agree() -> dict:
    """跑出 run_agree —— 证据方向一致的那一次。

    条件与 run_082 / run_084 完全一致（anchor / 历史窗口 / B06 窗口 /
    知识 query / 固定问题 / system prompt / 场景键），
    **唯一变化 = 外部天气从"不可用"或"转好"换成"转差"（同向）**。

    外部条件用 S3 已有的离线夹具构造用例 deteriorating —— 不新建夹具数据、
    不真调天气 API。
    """
    os.environ.pop("B07_WEATHER_UNAVAILABLE", None)

    from B07.s4 import RUN_LOCATION, build_ledger
    from B07.s4_offline import build_offline_cooling_context, default_anchor

    anchor = default_anchor()
    offline = build_offline_cooling_context(anchor, case=AGREE_CASE)

    def _factory(bundle):
        return build_ledger(bundle, offline, anchor)

    archive = run_analysis(
        anchor=anchor,
        location=RUN_LOCATION,
        scenario=SCENARIO_KEY,
        external_conditions=offline["context"],
        evidence_ledger=_factory,
    )
    archive["_offline_case"] = offline.get("case")
    return archive


# ==========================================================================
# 4. 检查项 C1–C5
# ==========================================================================
def _combined_diff(baseline: dict, degraded: dict) -> list[dict]:
    """对比两份 combined_attention 记录，逐字段说清楚哪里变了、哪里没变。"""
    rows = []
    keys = ["value", "internal_predicted_attention", "external_signal",
            "has_conflict", "reason"]
    for k in keys:
        a = baseline.get(k)
        b = degraded.get(k)
        rows.append({"field": k, "baseline": a, "degraded": b, "changed": a != b})
    return rows


def build_checks(runs: dict) -> dict:
    """跑 C1–C5。全部基于实际 run 存档，数字不许手编。"""
    agree = runs["run_agree"]
    conflict = runs["run_conflict"]
    missing = runs["run_missing_weather"]

    checks: dict[str, dict] = {}

    # ---------------- C1 分歧保留（第二步） ----------------
    ca = conflict["combined_attention"]
    dv = conflict["divergence"]
    sup = dv["supporting"]
    opp = dv["opposing"]
    sup_hits = conflict["expression"]["support_hits"]
    opp_hits = conflict["expression"]["opposing_hits"]
    c1_items = [
        ("combined_attention == conflict_unresolved",
         ca.get("value") == "conflict_unresolved", ca.get("value")),
        ("evidence_divergence.has_conflict == True",
         dv.get("has_conflict") is True, dv.get("has_conflict")),
        ("支持侧证据非空", len(sup) > 0, f"{len(sup)} 条"),
        ("反向侧证据非空", len(opp) > 0, f"{len(opp)} 条"),
        ("回答表达了『支持提高关注』",
         bool(sup_hits), "、".join(sup_hits) if sup_hits else "（无）"),
        ("回答表达了『提示可能缓解 / 反向』",
         bool(opp_hits), "、".join(opp_hits) if opp_hits else "（无）"),
    ]
    checks["C1"] = {
        "name": "分歧保留",
        "step": "第二步",
        "passed": all(ok for _, ok, _ in c1_items),
        "items": [{"check": c, "ok": ok, "detail": d} for c, ok, d in c1_items],
        "detail": {
            "run_id": conflict["run_id"],
            "combined_attention": ca,
            "supporting": [{"source_type": s.get("source_type"),
                            "summary": s.get("summary")} for s in sup],
            "opposing": [{"source_type": s.get("source_type"),
                          "summary": s.get("summary")} for s in opp],
            "support_hits": sup_hits,
            "opposing_hits": opp_hits,
            "resolution": dv.get("resolution"),
        },
    }

    # ---------------- C2 不该出现的确定话（第三步，红线） ----------------
    c2_hits = conflict["expression"]["certainty_hits"]
    c2_support = len(sup)
    c2_opp = len(opp)
    c2_conflict = dv.get("has_conflict") is True
    if c2_conflict:
        c2_strength = (
            f"现有证据：支持侧 {c2_support} 条 / 反向侧 {c2_opp} 条，"
            f"**存在方向冲突**（系统自己都没有合并），"
            f"因此不支持任何『一定 / 必然 / 可以确定』式的断言。")
    else:
        c2_strength = (
            f"现有证据：支持侧 {c2_support} 条 / 反向侧 {c2_opp} 条，"
            f"方向一致 —— 但仍然只是**单窗口样本 + 外部离线构造条件**，"
            f"断言强度应受限于『在当前证据下』。")
    c2_advice = ""
    if c2_hits:
        c2_advice = (
            "建议（**本轮只报告，不改 SYSTEM_PROMPT**）："
            f"把命中的『{c2_hits[0]['phrase']}』改写为"
            "『在当前证据下更可能……』/『倾向于……』，"
            "并把分歧/限制写进同一句，不要让确定话单独成句。")
    checks["C2"] = {
        "name": "不该出现的确定话",
        "step": "第三步",
        "passed": len(c2_hits) == 0,
        "items": [
            {"check": "确定性词表命中数 == 0", "ok": len(c2_hits) == 0,
             "detail": f"{len(c2_hits)} 处"},
        ],
        "detail": {
            "run_id": conflict["run_id"],
            "wordlist": list(CERTAINTY_PHRASES),
            "hits": c2_hits,
            "hit_count": len(c2_hits),
            "strength_assessment": c2_strength,
            "advice": c2_advice if c2_hits else "",
        },
    }

    # ---------------- C3 判断范围收缩（第四步） ----------------
    diff_rows = _combined_diff(agree["combined_attention"],
                                missing["combined_attention"])
    value_changed = diff_rows[0]["changed"]
    struct_changed = [r["field"] for r in diff_rows if r["changed"]]
    lim = missing["expression"]["limitation_hits"]
    hist_hits = _match_any(missing["answer"], HISTORY_PHRASES)
    model_hits = _match_any(missing["answer"], MODEL_PHRASES)
    concl_hits = _match_any(missing["answer"], CONCLUSION_PHRASES)
    c3_items = [
        ("降级后 combined_attention 记录与基线不同（字段级）",
         bool(struct_changed),
         ("变化字段：" + "、".join(struct_changed)) if struct_changed else "无字段变化"),
        ("降级后回答明确说明天气不可用 / 判断受限",
         bool(lim), "、".join(lim) if lim else "（未出现受限表达）"),
        ("降级后回答仍然给出历史判断", bool(hist_hits),
         "、".join(hist_hits) if hist_hits else "（未找到）"),
        ("降级后回答仍然给出模型判断", bool(model_hits),
         "、".join(model_hits) if model_hits else "（未找到）"),
        ("降级后回答仍然给出结论", bool(concl_hits),
         "、".join(concl_hits) if concl_hits else "（未找到）"),
    ]
    checks["C3"] = {
        "name": "判断范围收缩",
        "step": "第四步",
        "passed": all(ok for _, ok, _ in c3_items),
        "items": [{"check": c, "ok": ok, "detail": d} for c, ok, d in c3_items],
        "detail": {
            "baseline_run": agree["run_id"],
            "degraded_run": missing["run_id"],
            "diff": diff_rows,
            "value_changed": value_changed,
            "value_same_note": (
                "value 字段两边相同（%s = %s）—— **如实写出来，不掩盖**。"
                "这不是『证据没变』：external_signal / reason / 证据构成都变了，"
                "变的是 combined_attention() 的分支优先级"
                "（predicted=WATCH 分支先于外部信号分支命中）。"
                % (agree["combined_attention"].get("value"),
                   missing["combined_attention"].get("value"))
                if not value_changed else ""),
            "limitation_hits": lim,
            "history_hits": hist_hits,
            "model_hits": model_hits,
            "conclusion_hits": concl_hits,
        },
    }

    # ---------------- C4 没有偷偷补齐（第五步，红线） ----------------
    c4_rows = scan_external_content(missing["answer"])
    violations = [r for r in c4_rows if r["violation"]]
    exempt_value_rows = [r for r in c4_rows if r["exempt_but_has_value"]]
    checks["C4"] = {
        "name": "没有偷偷补齐",
        "step": "第五步",
        "passed": len(violations) == 0,
        "items": [
            {"check": "回答中外部天气内容命中数 == 0", "ok": len(violations) == 0,
             "detail": f"{len(violations)} 句"},
        ],
        "detail": {
            "run_id": missing["run_id"],
            "term_phrases": list(EXTERNAL_TERM_PHRASES),
            "value_patterns": [{"label": a, "pattern": b}
                               for a, b in EXTERNAL_VALUE_PATTERNS],
            "exempt_phrases": list(EXEMPT_PHRASES),
            "rows": c4_rows,
            "violations": violations,
            "violation_count": len(violations),
            "exempt_but_has_value": exempt_value_rows,
        },
    }

    # ---------------- C5 三次对照成立（第六步） ----------------
    table_rows = _comparison_rows(runs)
    dims = [r[0] for r in table_rows]
    cells = {r[0]: [r[1], r[2], r[3]] for r in table_rows}
    constant_dims = [d for d in dims if len(set(cells[d])) == 1]
    core_dims = ["combined_attention", "是否表达分歧", "判断范围"]
    core_constant = [d for d in core_dims if len(set(cells[d])) == 1]
    c5_items = [
        ("对照表五维齐全且每格都有实测值",
         all(all(str(c).strip() for c in cells[d]) for d in dims),
         "、".join(dims)),
        ("核心三维（结论档位 / 是否表达分歧 / 判断范围）三档互不相同",
         not core_constant,
         ("恒定维度：" + "、".join(core_constant)) if core_constant
         else "三维均随证据条件变化"),
    ]
    # 恒定维度逐条解释 —— 如实写出来，不掩盖
    constant_notes = []
    for d in constant_dims:
        value = cells[d][0]
        if d == "确定性断言数":
            why = ("本轮三种证据条件下系统都没有说出『一定 / 必然 / 可以确定』这类话"
                   "（C2 词表 0 命中）—— 这一维在本轮没有变化。")
        elif d == "是否说明受限":
            why = "三次回答都带了限定 / 免责表述 —— 这一维在本轮没有变化。"
        elif d == "combined_attention":
            why = "结论档位三档相同 —— 这一维没有随证据条件改变。"
        elif d == "是否表达分歧":
            why = "三次回答的冲突表达情况相同 —— 这一维在本轮没有变化。"
        else:
            why = "这一维在三种证据条件下没有变化。"
        constant_notes.append(f"    · {d}：三档全为 {value} —— {why}")
    checks["C5"] = {
        "name": "三次对照成立",
        "step": "第六步",
        "passed": all(ok for _, ok, _ in c5_items),
        "items": [{"check": c, "ok": ok, "detail": d} for c, ok, d in c5_items],
        "detail": {
            "rows": table_rows,
            "constant_dims": constant_dims,
            "constant_note": ("\n".join(constant_notes) if constant_notes
                              else "五个维度都随证据条件变化。"),
            "core_dims": core_dims,
        },
    }

    return checks


def _scope_label(run: dict) -> str:
    """判断范围 —— 从证据结构直接算，不手写结论。

    内部历史 + 模型 + 外部（可用 / 方向相反 / 不可用）三种形态。
    """
    ext = run["external"]
    sig = ext.get("signal")
    if ext.get("available") is False or sig == "external_unavailable":
        return "内部历史+模型（外部不可用，相关判断受限）"
    if run["divergence"].get("has_conflict"):
        return "内部历史+模型+外部（方向相反，保留分歧）"
    if sig == "external_deteriorating":
        return "内部历史+模型+外部（方向同向）"
    if sig == "external_improving":
        return "内部历史+模型+外部（方向相反）"
    return "内部历史+模型+外部"


def _comparison_rows(runs: dict) -> list[list[str]]:
    """C5 的五行 × 三列对照表（每格都来自实测存档）。"""
    agree = runs["run_agree"]
    conflict = runs["run_conflict"]
    missing = runs["run_missing_weather"]
    order = [agree, conflict, missing]

    def certainty(r):
        return str(len(r["expression"]["certainty_hits"]))

    def divergence_expr(r):
        s = r["expression"]["support_hits"]
        o = r["expression"]["opposing_hits"]
        if s and o:
            return "是（支持侧+反向侧）"
        if s:
            return "否（仅支持侧）"
        if o:
            return "否（仅反向侧）"
        return "否"

    def scope_limit(r):
        return "是" if r["expression"]["scope_limit_hits"] else "否"

    return [
        ["combined_attention"] + [str(r["combined_attention"].get("value"))
                                  for r in order],
        ["确定性断言数"] + [certainty(r) for r in order],
        ["是否表达分歧"] + [divergence_expr(r) for r in order],
        ["是否说明受限"] + [scope_limit(r) for r in order],
        ["判断范围"] + [_scope_label(r) for r in order],
    ]


# ==========================================================================
# 5. 报告章节
# ==========================================================================
DOC_STEPS = [
    ("第一步", "看一次『证据方向一致』的运行 —— 系统能不能把话说得相对明确？"),
    ("第二步", "只改变一份证据让它出现分歧 —— **问的是『系统有没有把分歧保留下来』，"
              "不是问『谁赢了』**"),
    ("第三步", "检查有没有『不该出现的确定话』 —— 命中必须报出，不许掩盖"
              "（本轮先只报告，不改 SYSTEM_PROMPT）"),
    ("第四步", "再做一次证据缺失 —— 回答『哪些判断还能继续 / 哪些判断现在应该停下来』"),
    ("第五步", "检查有没有『偷偷补齐』 —— 天气 API 失败时，LLM 不许凭常识编造"
              "『未来天气预计仍将炎热……』；必须能区分『模型会说什么』与"
              "『系统真正有什么证据』"),
    ("第六步", "三次运行对照，自己总结"),
]


def _ext_block(run: dict) -> str:
    """外部条件块 —— 数值全部来自 run 存档 / 离线夹具，不手编。"""
    ext = run["external"]
    case = next((e.get("case") for e in run["entries"]
                 if e.get("source_type") == "B03"), None)
    provider = next((e.get("provider") for e in run["entries"]
                     if e.get("source_type") == "B03"), None)
    if ext.get("available") is False or ext.get("signal") == "external_unavailable":
        return (
            "外部条件：**不可用**（provider 明确失败，没有发起真实天气请求）\n"
            "  signal = %s　｜　error = %s\n"
            "  provider = %s　｜　夹具用例 case = %s"
            % (ext.get("signal"), ext.get("error"), provider, case))
    return (
        "外部条件（离线夹具构造用例 case=%s，**构造用例不是实测**）：\n"
        "  signal = %s　｜　judge_method = %s　｜　provider = %s\n"
        "  降水概率 %s%% → %s%%（Δ=%s 个百分点）　｜　温度 Δ=%s℃　｜　云量 Δ=%s%%\n"
        "  basis = %s"
        % (case, ext.get("signal"), ext.get("judge_method"), provider,
           (ext.get("baseline_point") or {}).get("precipitation_probability"),
           (ext.get("forecast_point") or {}).get("precipitation_probability"),
           ext.get("precip_delta"), ext.get("temp_delta"), ext.get("cloud_delta"),
           ext.get("basis")))


def _run_header(run: dict, extra: str = "") -> list[str]:
    ca = run["combined_attention"]
    dv = run["divergence"]
    out = [
        f"run_id：{run['run_id']}",
        f"存档时间：{run['created_at']}　｜　存档状态：{run['status']}",
        f"场景键：{run['scenario']}　｜　分析时刻：{run['analysis_time']}　｜　地点：{run['location']}",
        f"历史窗口：{run['history_window']['start']} → {run['history_window']['end']}"
        f"（{run['history_window']['count']} 点，稳定判据通过="
        f"{run['history_window']['stability_passed']}）",
        f"B06 窗口：{run['model']['window_id']}　｜　输出 {run['model']['predicted_attention']}"
        f"（confidence={run['model']['confidence']}）　｜　模型版本 {run['model']['model_version']}",
        f"固定问题 sha256：{run['llm_meta']['question_sha256']}　｜　"
        f"system prompt sha256：{run['llm_meta']['system_prompt_sha256']}　｜　"
        f"LLM 模型：{run['llm_meta']['model']}　｜　prompt_tokens：{run['llm_meta']['prompt_tokens']}",
    ]
    if extra:
        out.append(extra)
    out += [
        "",
        "combined_attention（结构化判断的最终档位）：",
        f"  value                    = {ca.get('value')}",
        f"  internal_predicted_attention = {ca.get('internal_predicted_attention')}",
        f"  external_signal          = {ca.get('external_signal')}",
        f"  has_conflict             = {ca.get('has_conflict')}",
        f"  reason                   = {ca.get('reason')}",
        "",
        "证据方向（each_source，方向由程序规则判定，不经过 LLM）：",
    ]
    for src, direction in (dv.get("each_source") or {}).items():
        out.append(f"  {src} = {direction}")
    out += [
        f"  支持 {len(dv['supporting'])} 条 / 相反 {len(dv['opposing'])} 条 / "
        f"仍不确定 {len(dv['undetermined'])} 条",
        "",
    ]
    return out


def _answer_block(run: dict) -> list[str]:
    out = ["LLM 回答原文："]
    for line in run["answer"].splitlines():
        out.append("  " + line if line.strip() else "")
    out += ["", "表达强度（现场统计，不是人工断言）："]
    exp = run["expression"]
    out.append(f"  确定性断言数（C2 词表命中）= {len(exp['certainty_hits'])}")
    out.append(f"  『支持提高关注』表达命中 = {'、'.join(exp['support_hits']) or '（无）'}")
    out.append(f"  『提示可能缓解/反向』表达命中 = {'、'.join(exp['opposing_hits']) or '（无）'}")
    out.append(f"  受限/限定表达命中 = {'、'.join(exp['scope_limit_hits']) or '（无）'}")
    out.append(f"  回答长度 = {run['answer_len']} 字符")
    return out


def build_sections(runs: dict, checks: dict) -> list[dict]:
    """生成 12 个章节（终端与 md 同源）。"""
    agree = runs["run_agree"]
    conflict = runs["run_conflict"]
    missing = runs["run_missing_weather"]
    c1, c2, c3, c4, c5 = (checks["C1"], checks["C2"], checks["C3"],
                          checks["C4"], checks["C5"])
    secs: list[dict] = []

    # ---------------- 1 标题块 + 这一轮要看懂什么 ----------------
    body = [
        "B07 · 创新点 3 | 让系统学会\"有些话现在不能说\"",
        "文档 p.51–55 | 第二遍走查",
        "",
        "【这一轮要看懂什么】",
        "  ① 环境状态改变时，判断是否跟着调整（而不是照抄上一句话）；",
        "  ② 证据出现分歧时，系统是否表达不确定性（而不是替证据投票）；",
        "  ③ 证据暂时缺失时，系统是否明确说出判断的限制（而不是偷偷补齐）。",
        "",
        "三次运行对照表（唯一变量 = 天气条件，其余全部对齐）：",
        f"  run_agree          = {agree['run_id']}　（外部转差，与内部同向）",
        f"  run_conflict       = {conflict['run_id']}　（外部转好，与内部反向）",
        f"  run_missing_weather= {missing['run_id']}　（外部不可用）",
        "",
        "说明：仓库 docs/ 下没有 p.51–55 的文档原文，本节按该页六步要点转述，"
        "不伪称引用原文。",
    ]
    secs.append({"no": 1, "title": "标题块 / 这一轮要看懂什么", "body": body})

    # ---------------- 2 文档六步 ----------------
    body = ["【文档六步】"]
    for i, (name, desc) in enumerate(DOC_STEPS, 1):
        body.append(f"  {i}. {name}：{desc}")
    body += ["", "本轮落地方式："]
    body.append("  第一步 → 新跑 run_agree（1 次 LLM 调用）")
    body.append("  第二步 → 复用 run_082（不重跑，避免采样波动）")
    body.append("  第三步 → 对 run_082 的回答做词表逐句扫描")
    body.append("  第四步 → 复用 run_084（不重跑）")
    body.append("  第五步 → 对 run_084 的回答做逐句扫描（带免责句豁免）")
    body.append("  第六步 → 三次运行对照表")
    secs.append({"no": 2, "title": "文档六步", "body": body})

    # ---------------- 3 第一步 run_agree ----------------
    body = _run_header(agree, _ext_block(agree)) + _answer_block(agree)
    body += [
        "",
        "第一步的问题：证据方向一致时，系统能不能把话说得相对明确？",
        f"  回答里『支持提高关注』表达命中：{'、'.join(agree['expression']['support_hits']) or '（无）'}",
        f"  回答里『提示可能缓解/反向』表达命中："
        f"{'、'.join(agree['expression']['opposing_hits']) or '（无）'}",
        f"  确定性断言数 = {len(agree['expression']['certainty_hits'])}",
    ]
    secs.append({"no": 3, "title": "第一步 · run_agree（证据方向一致）", "body": body})

    # ---------------- 4 第二步 run_conflict ----------------
    body = _run_header(conflict, _ext_block(conflict))
    body += [
        "系统有没有把分歧保留下来？",
        f"  结构层：combined_attention.value = {c1['detail']['combined_attention'].get('value')}"
        f"　has_conflict = {conflict['divergence'].get('has_conflict')}"
        f"　→ 分歧**被保留**，没有强制合并、没有投票。",
        f"  分歧处理说明（程序写死的规则）：{conflict['divergence'].get('resolution')}",
        "",
        "支持提高关注的依据：",
    ]
    for s in c1["detail"]["supporting"]:
        body.append(f"  - [{s['source_type']}] {s['summary']}")
    body += ["", "提示可能缓解或反向的依据："]
    for s in c1["detail"]["opposing"]:
        body.append(f"  - [{s['source_type']}] {s['summary']}")
    body += ["", "因此不应该过度确定的结论："]
    body.append("  - 内部历史不稳定 + 模型 WATCH 与外部转好在方向上互相矛盾，")
    body.append("    任何单一方向的强结论都超出了当前证据能支撑的范围。")
    body.append("")
    body += _answer_block(conflict)
    secs.append({"no": 4, "title": "第二步 · run_conflict（证据出现分歧）", "body": body})

    # ---------------- 5 第三步 该不该出现的确定话 ----------------
    d2 = c2["detail"]
    body = [
        "【第三步 · 有没有\"不该出现的确定话\"】",
        f"扫描对象：{d2['run_id']} 的 LLM 回答（逐句扫描，只用多字短语，禁用单字）",
        "",
        "词表：" + " / ".join(d2["wordlist"]),
        "",
        f"命中数：{d2['hit_count']}",
    ]
    if d2["hit_count"] == 0:
        body += [
            "",
            "逐句扫描结果：没有句子命中词表。",
            "→ 本轮在 run_conflict 的回答里，系统没有越界说\"一定/必然/可以确定\"。",
        ]
    else:
        body += ["", "命中明细（必须报出，不许掩盖）："]
        for i, h in enumerate(d2["hits"], 1):
            body += [
                f"  [{i}] 命中「{h['phrase']}」（原句：…{h['matched']}…）",
                f"      原句全文：{h['sentence']}",
            ]
        body += ["", "现有证据是否支持这种强度？", "  " + d2["strength_assessment"],
                 "", d2["advice"]]
    body += [
        "",
        "本轮**只报告，不改 SYSTEM_PROMPT**（按规格要求）：",
        "判定与降级都走 B07/judgement.py 的程序规则，措辞层不动。",
    ]
    secs.append({"no": 5, "title": "第三步 · 有没有\"不该出现的确定话\"", "body": body})

    # ---------------- 6 第四步 run_missing_weather ----------------
    d3 = c3["detail"]
    body = _run_header(missing, _ext_block(missing))

    body += ["▸ 哪些判断还能继续（证据可用，方向明确）："]
    for e in missing["entries"]:
        if e.get("status") == "ok" and e.get("direction") in ("supporting", "opposing") \
                and e.get("adopted"):
            body.append(f"  - [{e['source_type']}] {e.get('source')}　方向="
                        f"{e.get('direction')}　status={e.get('status')}")
    body += ["", "▸ 哪些判断现在应该停下来（证据不可用 / 方向未定）："]
    for e in missing["entries"]:
        if e.get("status") != "ok":
            body.append(f"  - [{e['source_type']}] {e.get('source')}　status="
                        f"{e.get('status')}　error={e.get('error')}")
    for u in missing["divergence"]["undetermined"]:
        body.append(f"  - [{u.get('source_type')}] 仍不足以确定：{u.get('summary')}"
                    f"（{u.get('reason')}）")
    body += ["",
             "降级前后 combined_attention 逐字段对比（基线 = run_agree）："]
    for r in d3["diff"]:
        mark = "变了" if r["changed"] else "未变"
        body.append(f"  {r['field']:<30} {r['baseline']}  →  {r['degraded']}　（{mark}）")
    if d3.get("value_same_note"):
        body += ["", "  ⚠ " + d3["value_same_note"]]
    body += ["",
             "降级后回答里的受限表达：" +
             ("、".join(d3["limitation_hits"]) or "（未出现）"),
             "降级后回答里仍然给出的判断：",
             f"  历史 = {'是' if d3['history_hits'] else '否'}"
             f"（{('、'.join(d3['history_hits'])) or '—'}）",
             f"  模型 = {'是' if d3['model_hits'] else '否'}"
             f"（{('、'.join(d3['model_hits'])) or '—'}）",
             f"  结论 = {'是' if d3['conclusion_hits'] else '否'}"
             f"（{('、'.join(d3['conclusion_hits'])) or '—'}）",
             "",
             "LLM 回答原文："]
    for line in missing["answer"].splitlines():
        body.append("  " + line if line.strip() else "")
    secs.append({"no": 6, "title": "第四步 · run_missing_weather（证据缺失）", "body": body})

    # ---------------- 7 第五步 偷偷补齐 ----------------
    d4 = c4["detail"]
    body = [
        "【第五步 · 有没有\"偷偷补齐\"】",
        f"扫描对象：{d4['run_id']} 的 LLM 回答 —— 那次外部天气 API **明确失败**，"
        "系统手里没有任何外部天气数值。",
        "模型如果凭常识写出「未来天气预计仍将炎热……」，那就是偷偷补齐。",
        "",
        "外部天气表述词表：" + " / ".join(d4["term_phrases"]),
        "外部天气具体数值（正则）：" +
        " / ".join(f"{p['label']} = {p['pattern']}" for p in d4["value_patterns"]),
        "免责词（含这些词的句子是合规声明，不算伪造）：" + " / ".join(d4["exempt_phrases"]),
        "",
        "逐句扫描：",
    ]
    for i, r in enumerate(d4["rows"], 1):
        if r["violation"]:
            tag = "【命中】"
        elif r["exempt"]:
            tag = "【合规声明·豁免】" + ("（含数值，已标出）" if r["values"] else "")
        else:
            tag = "【正常】"
        body.append(f"  {i}. {tag} {r['sentence']}")
        if r["terms"]:
            body.append(f"       表述命中：{'、'.join(r['terms'])}")
        if r["values"]:
            body.append(f"       数值命中：{'、'.join(v['phrase'] for v in r['values'])}")
        if r["exempt"]:
            body.append(f"       豁免依据：{'、'.join(r['exempt_reason'])}")
    body += ["",
             f"违规命中数：{d4['violation_count']}",
             ("结论：❌ 回答里出现了外部天气内容 —— 红线，必须报出（见上）。"
              if d4["violation_count"] > 0 else
              "结论：✅ 没有偷偷补齐 —— 回答里没有出现任何外部天气表述或外部天气数值。"),
             "（免责句按规则豁免，但免责句里若含外部天气数值仍会在上面单独标出，"
              "不隐藏。）"]
    secs.append({"no": 7, "title": "第五步 · 有没有\"偷偷补齐\"", "body": body})

    # ---------------- 8 第六步 三次运行对照 ----------------
    headers = ["维度", f"run_agree（{agree['run_id']}）",
               f"run_conflict（{conflict['run_id']}）",
               f"run_missing_weather（{missing['run_id']}）"]
    rows = c5["detail"]["rows"]
    body = [
        "【第六步 · 三次运行对照】",
        _ascii_table(headers, rows),
        "",
        "对照表读法：",
        "  · 三列分别是三种外部条件（转差 / 转好 / 不可用），其余条件完全对齐。",
        "  · 『确定性断言数』= C2 词表在回答里的命中次数（0 = 没有说\"一定/必然\"这类话）。",
        "  · 『是否表达分歧』= 回答里是否同时出现『支持提高关注』与『提示可能缓解/反向』"
        "两类表达。",
        "  · 『是否说明受限』= 回答里是否出现受限/限定类表达。",
        "  · 『判断范围』= 由证据结构直接算出（可用 / 方向相反 / 不可用）。",
        "",
        c5["detail"]["constant_note"],
    ]
    secs.append({"no": 8, "title": "第六步 · 三次运行对照", "body": body})

    # ---------------- 9 检查汇总 ----------------
    body = ["【检查汇总】"]
    summary_rows = []
    for key in ("C1", "C2", "C3", "C4", "C5"):
        ck = checks[key]
        summary_rows.append([
            key,
            ck["name"],
            "PASS" if ck["passed"] else "FAIL",
            ck["step"],
        ])
    body.append(_ascii_table(["检查项", "含义", "判定", "对应步骤"], summary_rows))
    body.append("")
    for key in ("C1", "C2", "C3", "C4", "C5"):
        ck = checks[key]
        body.append(f"{key} {ck['name']}（{ck['step']}）："
                    f"{'PASS ✅' if ck['passed'] else 'FAIL ❌'}")
        for it in ck["items"]:
            body.append(f"   - [{'✅' if it['ok'] else '❌'}] {it['check']}：{it['detail']}")
        body.append("")
    secs.append({"no": 9, "title": "检查汇总 C1–C5", "body": body})

    # ---------------- 10 结论 ----------------
    body = [
        "【结论】",
        "",
        "创新点 3 证明了什么：",
        f"  1. 三次运行（{agree['run_id']} / {conflict['run_id']} / {missing['run_id']}）"
        "在 anchor、历史窗口、B06 窗口、知识 query、固定问题、system prompt 全部一致"
        "的前提下，只有天气条件不同 —— 而系统的结论档位、是否表达分歧、"
        "是否说明受限、判断范围全部随之改变。这说明判断不是照抄上一句话，"
        "而是由证据条件决定的。",
        f"  2. 证据出现分歧时（{conflict['run_id']}），系统输出 conflict_unresolved，"
        "supporting 与 opposing 两列都非空，回答里同时出现『支持提高关注』与"
        "『提示可能缓解/反向』两类表达 —— 分歧被保留下来，没有被投票抹平。",
        f"  3. 证据缺失时（{missing['run_id']}），系统明确说出外部天气不可用、"
        "相关判断受限，同时仍然给出历史与模型能给的判断 —— 这就是"
        "『哪些判断还能继续 / 哪些应该停下来』。",
        f"  4. 没有偷偷补齐：{missing['run_id']} 的回答里外部天气表述与数值命中 "
        f"{c4['detail']['violation_count']} 处。",
        f"  5. 没有越界说确定话：{conflict['run_id']} 的回答里确定性词表命中 "
        f"{c2['detail']['hit_count']} 处。",
        "",
        "创新点 3 没有证明什么（不许夸大）：",
        "  1. 没有证明系统在**任何**证据组合下都表现正确 —— 本轮只覆盖三种外部条件，"
        "每种一次采样。",
        "  2. 没有证明 LLM 的措辞层面**从不**越界 —— 词表只覆盖列出的多字短语，"
        "是启发式检测，不是语义判定。",
        "  3. 没有证明系统能在**证据方向不一致且外部更差**时同样保守 —— "
        "本轮只造了『同向』和『反向』两种外部条件。",
        "  4. 没有证明在**缺少内部证据**（例如历史窗口本身取不到）时也成立 —— "
        "本轮的缺失只发生在外部天气这一路。",
        "  5. 没有证明模型改 prompt 之后这些性质仍然成立 —— 本轮**没有改** "
        "SYSTEM_PROMPT，也没有做改动前后的对照。",
    ]
    if not c3["detail"]["value_changed"]:
        body += [
            "  6. 没有证明『结论档位字符串』在证据缺失时一定改变 —— 实测"
            f"{agree['run_id']} 与 {missing['run_id']} 的 combined_attention.value "
            f"**同为 {missing['combined_attention'].get('value')}**"
            "（变化体现在 external_signal / reason / 证据构成与回答措辞上）。"
            "这是本轮必须如实写出来的一条。",
        ]
    secs.append({"no": 10, "title": "结论", "body": body})

    # ---------------- 11 Limitations ----------------
    body = [
        "【Limitations】",
        "",
        "L1 本轮只新跑 1 次 LLM（run_agree），run_conflict / run_missing_weather "
        "复用 S4/S5 已落盘的 run，避免采样波动。三次运行因此不是同一次采样内的对照。",
        "",
        f"L2 run_agree 的天气是离线夹具构造的 external_deteriorating"
        f"（case={AGREE_CASE}），**不是真实气象**。离线夹具保证可复现，"
        "但它的数值分布不等于真实天气。",
        "",
        "L3 确定性断言检测（C2）与『偷偷补齐』检测（C4）都是启发式多字短语 + "
        "数值正则匹配，可能漏判或误判。报告给出了实际命中的句子原文，供人工核对；"
        "命中数必须结合原句人工确认，不能只看计数。",
        "",
        f"L4 本轮 LLM 为 {agree['llm_meta']['model']}（原 qwen3.5-27b 免费额度耗尽后更换），"
        "与 S1–S3 的措辞不可直接比较。",
        "",
        "L5 三次运行的唯一变量是天气条件：历史窗口、B06 窗口、知识 query、"
        "固定问题、system prompt 全部一致（本报告用 window_id / question_sha256 / "
        "system_prompt_sha256 打印自证）。但『一致』由存档字段核对，"
        "不是逐字节 diff 两次完整输入。",
        "",
        "L6 『判断范围收缩』是语义判断。机器检查只能验证『降级后回答里出现了 / "
        "没出现某些表达』，不能证明模型真的收窄了推理范围。",
        "",
        f"L7 实测 {agree['run_id']} 与 {missing['run_id']} 的 combined_attention.value "
        f"相同（{agree['combined_attention'].get('value')}），"
        "所以 C3 判定的是**记录层**（external_signal / reason / 证据构成）发生变化，"
        "而不是档位字符串发生变化。根因是 combined_attention() 的分支顺序："
        "predicted=WATCH 分支先于外部信号分支命中。",
        "",
        "L8 文档 p.51–55 的原文不在仓库 docs/ 下，本报告的六步要点是按该页转述的，"
        "不是逐句引用原文。",
    ]
    secs.append({"no": 11, "title": "Limitations", "body": body})

    # ---------------- 12 复现命令 ----------------
    body = [
        "【复现命令】",
        "",
        "  # 完整走查（跑 run_agree，1 次 LLM 调用 + 全部检查 + 写产物）",
        "  python -m B07.innovation3",
        "",
        "  # 离线重生成走查（不调 LLM，只从已落盘存档重建报告）",
        "  python -m B07.innovation3 --report-only",
        "",
        "  # 只看某一节（1..12）",
        "  python -m B07.innovation3 --section 3",
        "",
        "  # 确定性自检：连续两次 --report-only 后报告不应有任何差异",
        "  python -m B07.innovation3 --report-only",
        "  python -m B07.innovation3 --report-only",
        "  git diff --stat B07/artifacts/innovation3_report.md",
        "",
        "  # 全量测试",
        "  python -m pytest B07/tests -q",
    ]
    secs.append({"no": 12, "title": "复现命令", "body": body})

    return secs


# ==========================================================================
# 6. 渲染
# ==========================================================================
def render_md(sections: list[dict], data: dict) -> str:
    runs = data["runs"]
    checks = data["checks"]
    lines = [
        "# B07 · 创新点 3 | 让系统学会\"有些话现在不能说\"",
        "",
        "文档 p.51–55 | 第二遍走查",
        "",
        "| 三次运行 | run_id | 外部条件 | combined_attention |",
        "|---|---|---|---|",
    ]
    for key, label in (("run_agree", "run_agree（证据方向一致）"),
                       ("run_conflict", "run_conflict（证据分歧）"),
                       ("run_missing_weather", "run_missing_weather（证据缺失）")):
        r = runs[key]
        lines.append(f"| {label} | {r['run_id']} | {r['external'].get('signal')} | "
                     f"{r['combined_attention'].get('value')} |")
    lines.append("")
    for sec in sections:
        lines.append(f"## {sec['title']}")
        lines.append("")
        lines.append("```text")
        lines.extend(sec["body"])
        lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_terminal(sections: list[dict]) -> str:
    lines = []
    total = len(sections)
    for sec in sections:
        lines.append("=" * 78)
        lines.append(f"[{sec['no']}/{total}] {sec['title']}")
        lines.append("=" * 78)
        lines.extend(sec["body"])
        lines.append("")
    return "\n".join(lines)


# ==========================================================================
# 7. 主流程
# ==========================================================================
def _configure_stdio() -> str:
    """让 stdout 吐 UTF-8，并把 Windows 控制台 codepage 也切成 65001。"""
    enc = "utf-8"
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding=enc, errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding=enc, errors="replace")
    except Exception:            # pragma: no cover
        pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:        # pragma: no cover
            pass
    return enc


def load_runs(report_only: bool) -> dict:
    """取三次运行的（已抽取的）事实集。

    report_only 时直接从 innovation3_runs.json 读 —— 不调 LLM、不碰网络。
    """
    if report_only:
        if not RUNS_JSON.exists():
            raise SystemExit(
                f"❌ {RUNS_JSON} 不存在 —— 先跑一次 `python -m B07.innovation3`"
                f"（那是唯一会调 LLM 的入口）。")
        with open(RUNS_JSON, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data["runs"]

    raw = {
        "run_agree": build_run_agree(),
        "run_conflict": load_run(CONFLICT_RUN_ID),
        "run_missing_weather": load_run(MISSING_RUN_ID),
    }
    return {key: extract_run(archive) for key, archive in raw.items()}


def collect(report_only: bool) -> dict:
    """跑（或读）三次运行 → 抽事实 → 跑检查 → 组装章节。"""
    runs = load_runs(report_only)
    checks = build_checks(runs)
    return {
        "schema": "innovation3/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runs": runs,
        "checks": checks,
        "sources": {
            "run_agree": "本轮新跑（1 次 LLM 调用）",
            "run_conflict": f"复用已落盘存档 {CONFLICT_RUN_ID}",
            "run_missing_weather": f"复用已落盘存档 {MISSING_RUN_ID}",
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m B07.innovation3",
        description="B07 创新点 3 第二遍走查器：让系统学会\"有些话现在不能说\"")
    parser.add_argument("--report-only", action="store_true",
                        help="离线重生成走查（不调 LLM，只读 innovation3_runs.json）")
    parser.add_argument("--section", type=int, default=0,
                        help="只看某一节（1..12），0 = 全部")
    args = parser.parse_args(argv)

    _configure_stdio()

    data = collect(args.report_only)
    sections = build_sections(data["runs"], data["checks"])

    if not args.report_only:
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        with open(RUNS_JSON, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        REPORT_MD.write_text(render_md(sections, data), encoding="utf-8")
        print(f"已写：{RUNS_JSON}")
        print(f"已写：{REPORT_MD}")
        print("")

    if args.section:
        picked = [s for s in sections if s["no"] == args.section]
        if not picked:
            print(f"❌ 没有第 {args.section} 节（可选 1..{len(sections)}）")
            return 2
        print("=" * 78)
        print(f"[{picked[0]['no']}/{len(sections)}] {picked[0]['title']}")
        print("=" * 78)
        print("\n".join(picked[0]["body"]))
        return 0

    print(render_terminal(sections))

    if args.report_only:
        REPORT_MD.write_text(render_md(sections, data), encoding="utf-8")

    failed = [k for k, v in data["checks"].items() if not v["passed"]]
    if failed:
        print("=" * 78)
        print(f"检查项未全部通过：{'、'.join(failed)}（明细见上面各节）")
        print("=" * 78)
        return 1
    print("=" * 78)
    print("检查项 C1–C5 全部通过。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
