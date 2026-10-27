"""B07 S4-2 —— 证据分歧场景编排

把四类证据（历史 / 模型 / 天气 / 知识）装配成**证据台账** → 算方向 →
算证据分歧（第 7 个结构化字段）→ 进 ⑦ 上下文 → 调 LLM → 存档。

规矩（S4-2）：

  1. **方向判定是程序规则**，不经过 LLM，也不由本模块决定 ——
     唯一出处是 `B07.evidence_ledger.judge_direction()`。
  2. **只有被本次判断采用的工具结果才升格成证据**。
     调用过 ≠ 采用过（文档第三步 / 第四步）。
  3. **外部条件从台账派生**（补充1）—— judgement.py 不另写一套天气字段组装。
  4. **不投票、不强制合并**。有方向冲突时 combined_attention 返回
     "conflict_unresolved"，那不是错误，是"保留分歧"的表达。
  5. 模型输出用 B06 的真实结果，不做任何修改；天气用 S4-1 造的离线夹具；
     知识用 S4-1 选好的默认 query；不编造 citation、不编造数值。

硬约束：
  - 不改 B01 / B02 / B03 / B04 / B05 / B06 任何文件
  - 不改 SYSTEM_PROMPT、不改固定问题、不改判据阈值、不重训模型
  - 不硬编码任何实测天气数值进产品代码（夹具数值全在 B07/s3.py）

用法：

    python -m B07.s4

自检输出包含：四行证据表、has_conflict、三列表、resolution、
combined_attention、⑦ 上下文块、LLM 回答原文。

跑完还会把实测结果落盘到 `B07/artifacts/s4_raw_result.json` ——
那是 `python B07/s4_report.py` 生成 S4 报告的**唯一输入**
（报告生成过程不联网、不调 LLM，可确定性重放）。
"""

from __future__ import annotations

import difflib
import json
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from B07.evidence_ledger import (
    EvidenceLedger,
    format_evidence_table,
    weather_context_from_ledger,
)
from B07.evidence_registry import make_tool_result
from B07.knowledge import DEFAULT_QUERY, search_knowledge
from B07.s4_offline import (
    OFFLINE_CASE,
    OFFLINE_CASE_LABEL,
    build_offline_cooling_context,
    default_anchor,
)

#: 天气取数地点（与 S3 一致）
WEATHER_LOCATION = "河北固安"
WEATHER_WINDOW = "6h"

#: 内部历史/模型用的温室位置标签
RUN_LOCATION = "A区-3号温室"

#: 产物目录
ARTIFACTS = _PROJECT_ROOT / "B07" / "artifacts"

#: S4 原始结果（S4-4 报告的唯一输入）
RAW_RESULT_PATH = ARTIFACTS / "s4_raw_result.json"

#: S4-3 平铺/分身份对照实验的原始结果
CONTRAST_PATH = ARTIFACTS / "s4_3_contrast.json"

#: 跨阶段模型对照表
MODEL_TABLE_PATH = ARTIFACTS / "model_table.md"


# --------------------------------------------------------------------------
# 锚点
# --------------------------------------------------------------------------

def s4_anchor() -> str:
    """S4 场景的历史锚点（ISO 字符串）。

    **为什么不是 S2 的漂移窗口（2026-10-20T12:00:00）**：
    S4-1 的窗口扫描**真的找到了 B06 输出 WATCH 的窗口** ——
    331 个窗口里 25 个在因果 DTW 与生产路径 DTW 两套参考集下都判 WATCH，
    其中 25 个里挑内部状态不稳定的、时间最晚的那个：
    2026-10-21T09:30:00（ΔT=+4.4℃，温度 24.2~28.6℃）。
    按 S4-2 的要求"S4-1 找到了 WATCH 窗口就改用它"，锚点即此窗口。
    唯一真源是 B07.s4_windows.selected_anchor()，这里不硬编码时刻。
    """
    return default_anchor()


# --------------------------------------------------------------------------
# 台账装配
# --------------------------------------------------------------------------

def _samples_from_history(history: list) -> list:
    """把存档形状的 6 点转回 B06.SPEC.Sample。"""
    from B06.SPEC import Sample

    return [
        Sample(
            timestamp=datetime.fromisoformat(h["timestamp"]),
            temperature=h["temperature"],
            humidity=h["humidity"],
            lux=h["lux"],
            source_type=h.get("source_type", ""),
            sim_scenario=h.get("sim_scenario", ""),
        )
        for h in history or []
    ]


def build_ledger(bundle: dict, offline: dict, anchor: str) -> EvidenceLedger:
    """把本次 run 的四类证据装配成台账。

    Args:
        bundle: run_bundle（evidence + model_output）。
        offline: S4-1 的离线外部条件（{"context":..., "raw_forecast":...}）。
        anchor: 分析时刻（ISO）。

    Returns:
        EvidenceLedger。
    """
    from B07.stability import judge_stability

    ledger = EvidenceLedger()
    history = bundle.get("history") or []
    temps = [h["temperature"] for h in history]

    # ---- 1) B02 历史（采用）----
    judged = judge_stability(_samples_from_history(history), rich_failure=True)
    tr = make_tool_result(
        source="B02/environment_samples",
        status="ok",
        data={
            "stability_passed": judged["passed"],
            "stability_conclusion": judged["conclusion"],
            "temps": temps,
            "history": history,
            "current_state": bundle.get("current_state"),
        },
        time=history[-1]["timestamp"] if history else anchor,
    )
    ledger.record_call(
        "B02.get_environment_history",
        arguments={"start": history[0]["timestamp"] if history else None,
                   "end": history[-1]["timestamp"] if history else None},
        purpose="取历史窗口 6 点（结构化判断的内部依据）",
    )
    ledger.add_evidence(
        tr, "B02",
        ext={"stability_criteria": judged["criteria"],
             "point_count": len(history)},
        tool_name="B02.get_environment_history",
    )

    # ---- 2) B05.get_current_environment（调用了，但**没被采用**）----
    # 它只作 latest_in_db 参考，不参与本次方向判定 —— 这就是
    # "工具被调用过 ≠ 工具结果被本次判断采用"。
    ledger.record_call(
        "B05.get_current_environment",
        arguments={"location": RUN_LOCATION},
        purpose="取库内最新一条（仅作 latest_in_db 参考）",
    )
    ledger.mark_not_adopted(
        "B05.get_current_environment",
        "仅作 latest_in_db 参考，不参与本次方向判定，未升格为证据",
    )

    # ---- 3) B06 模型（采用；输出原样，不做任何修改）----
    mo = bundle.get("model_output") or {}
    meta = bundle.get("model_meta") or {}
    tr = make_tool_result(
        source=f"B06/{mo.get('model_version')}",
        status="ok",
        data=mo,
        time=anchor,
    )
    ledger.record_call(
        "B06.predict_window",
        arguments={"window_id": mo.get("window_id"),
                   "model_version": mo.get("model_version")},
        purpose="B06 短时预测（只加载不重训）",
    )
    ledger.add_evidence(
        tr, "B06",
        ext={
            "model_version": mo.get("model_version"),
            "predicted_attention": mo.get("predicted_attention"),
            "confidence": mo.get("confidence"),
            "window_id": mo.get("window_id"),
            "feature_count": mo.get("feature_count"),
            "metrics": meta.get("metrics") or {},
        },
        tool_name="B06.predict_window",
    )

    # ---- 4) B03 天气（采用；S4-1 造的离线"降温"夹具）----
    # S5-1：provider 返回什么就是什么 —— 不可用时如实记成不可用，
    # 不写成 ok、不留上次成功的值、不伪造方向。
    ctx = offline["context"]
    raw = offline.get("raw_forecast") or {}
    raw_status = raw.get("status") or "error"
    raw_error = raw.get("error")
    if raw_status != "ok" and not raw_error:
        raw_error = f"天气 provider 返回 status={raw_status} 且没有给出原因"
    if raw_status == "ok":
        tr = make_tool_result(
            source="B03/Open-Meteo（离线构造用例）",
            status="ok",
            data=ctx,
            time=ctx.get("analysis_time") or anchor,
        )
    else:
        tr = make_tool_result(
            source="B03/Open-Meteo（离线构造用例）",
            status=raw_status,
            data=None,
            error=raw_error,
            time=ctx.get("analysis_time") or anchor,
        )
    ledger.record_call(
        "B05.get_weather_forecast",
        arguments={"location": WEATHER_LOCATION, "forecast_window": WEATHER_WINDOW,
                   "note": "内部调用 now + 6h 两次，取基线点与预报点"},
        purpose="取外部条件（基线点 + 未来 6h 预报点）",
        status=raw_status,
        error=raw_error,
    )
    ledger.add_evidence(
        tr, "B03",
        ext={
            "provider": raw.get("provider"),
            "forecast_time": (ctx.get("forecast_point") or {}).get("timestamp"),
            "forecast_window": ctx.get("forecast_window"),
            "location": ctx.get("location"),
            "retrieved_at": raw.get("retrieved_at"),
            "case": offline.get("case", OFFLINE_CASE),
            "is_constructed": True,
            "label": OFFLINE_CASE_LABEL,
            # S5-1：证据状态与失败原因如实带出（不可用时 forecast_time=None）
            "status": raw_status,
            "error": raw_error,
            "degraded": bool(offline.get("degraded")),
            # 不可用时，外部条件上下文一并留在 ext 里 —— ⑥ 块从台账派生，
            # 不能因为结果为 None 就让整块消失（消失 ≠ 不可用）。
            "unavailable_context": None if raw_status == "ok" else {
                "signal": ctx.get("signal"),
                "judge_method": ctx.get("judge_method"),
                "basis": ctx.get("basis"),
                "available": False,
                "future_ok": ctx.get("future_ok"),
                "warnings": list(ctx.get("warnings") or []),
                "caveats": list(ctx.get("caveats") or []),
                "location": ctx.get("location"),
                "forecast_window": ctx.get("forecast_window"),
                "analysis_time": ctx.get("analysis_time"),
                "status": raw_status,
                "error": raw_error,
            },
        },
        tool_name="B05.get_weather_forecast",
    )

    # ---- 5) B04 知识（采用；S4-1 选好的默认 query）----
    kb = search_knowledge(DEFAULT_QUERY)
    tr = make_tool_result(
        source=kb.get("source") or "B04/knowledge_base",
        status=kb.get("status") or "ok",
        data=kb.get("result"),
        error=kb.get("error"),
        time=kb.get("time"),
    )
    ledger.record_call(
        "B05.search_tomato_knowledge",
        arguments={"query": DEFAULT_QUERY},
        purpose="方法论知识检索（决策基于不完整信息的风险）",
    )
    ledger.add_evidence(
        tr, "B04",
        ext={**(kb.get("ext") or {}),
             "registry_source_type": kb.get("source_type")},
        tool_name="B05.search_tomato_knowledge",
    )

    return ledger


# --------------------------------------------------------------------------
# 跑一次 S4
# --------------------------------------------------------------------------

def run_s4(verbose: bool = True) -> dict:
    """跑一次 S4 场景，返回存档 dict。"""
    from B07.runs import run_analysis

    anchor = s4_anchor()
    offline = build_offline_cooling_context(anchor)

    def _factory(bundle):
        return build_ledger(bundle, offline, anchor)

    return run_analysis(
        anchor=anchor,
        location=RUN_LOCATION,
        scenario="S4",
        external_conditions=offline["context"],
        evidence_ledger=_factory,
    )


# --------------------------------------------------------------------------
# 对照：S3 Run B 的上下文 + ⑦ 块（逐字节对比用）
# --------------------------------------------------------------------------

def s3_context_with_divergence(run_id: str = "run_023") -> dict:
    """用 S3 某次存档重建"原上下文 + ⑦ 块"，用于证明 ⑦ 是**纯追加**。

    做法：直接用存档里的 structured_judgement（6 个字段，一个字不改），
    只按同一套规则重算 evidence_divergence，再拼一次上下文。
    这样 ①~⑥ 块与存档里的原上下文逐字节相同，差异只应该出现在 ⑦ 块。
    """
    from B07.explain import format_judgement_context
    from B07.judgement import build_evidence_divergence
    from B07.runs import load_run

    archive = load_run(run_id)
    ledger = _ledger_from_archive(archive)
    divergence = build_evidence_divergence(ledger)

    judgement = dict(archive.get("structured_judgement") or {})
    judgement["evidence_divergence"] = divergence

    bundle = {
        "analysis_time": archive.get("analysis_time"),
        "current_state": archive.get("current_state"),
        "history": archive.get("history"),
        "model_output": archive.get("model_output"),
        "external_conditions": archive.get("external_conditions"),
        "judgement": judgement,
    }
    return {
        "run_id": run_id,
        "archive": archive,
        "ledger": ledger,
        "divergence": divergence,
        "old_context": archive.get("llm_context") or "",
        "new_context": format_judgement_context(bundle),
    }


def _ledger_from_archive(archive: dict) -> EvidenceLedger:
    """从存档里重建一个台账（历史 / 模型 / 天气 / 知识四类）。"""
    from B07.knowledge import DEFAULT_QUERY, search_knowledge

    ledger = EvidenceLedger()
    history = archive.get("history") or []
    temps = [h["temperature"] for h in history]
    stability = (archive.get("history_window") or {}).get("stability") or {}

    ledger.add_evidence(
        make_tool_result(
            source="B02/environment_samples",
            status="ok",
            data={
                "stability_passed": stability.get("passed"),
                "stability_conclusion": stability.get("conclusion"),
                "temps": temps,
                "history": history,
                "current_state": archive.get("current_state"),
            },
            time=history[-1]["timestamp"] if history else None,
        ),
        "B02",
        ext={"point_count": len(history)},
    )

    mo = archive.get("model_output") or {}
    ledger.add_evidence(
        make_tool_result(
            source=f"B06/{mo.get('model_version')}",
            status="ok",
            data=mo,
            time=archive.get("analysis_time"),
        ),
        "B06",
        ext={
            "model_version": mo.get("model_version"),
            "predicted_attention": mo.get("predicted_attention"),
            "confidence": mo.get("confidence"),
            "metrics": (archive.get("model_meta") or {}).get("metrics") or {},
        },
    )

    ext_ctx = archive.get("external_conditions") or {}
    if ext_ctx:
        ledger.add_evidence(
            make_tool_result(
                source="B03/Open-Meteo",
                status="ok",
                data=ext_ctx,
                time=ext_ctx.get("analysis_time"),
            ),
            "B03",
            ext={
                "provider": ext_ctx.get("provider"),
                "forecast_time": (ext_ctx.get("forecast_point") or {}).get(
                    "timestamp"),
                "forecast_window": ext_ctx.get("forecast_window"),
                "location": ext_ctx.get("location"),
            },
        )

    kb = search_knowledge(DEFAULT_QUERY)
    ledger.add_evidence(
        make_tool_result(
            source=kb.get("source") or "B04/knowledge_base",
            status=kb.get("status") or "ok",
            data=kb.get("result"),
            error=kb.get("error"),
            time=kb.get("time"),
        ),
        "B04",
        ext={**(kb.get("ext") or {}),
             "registry_source_type": kb.get("source_type")},
    )

    return ledger


# --------------------------------------------------------------------------
# 降级探针（全部离线，不联网、不调 LLM）
# --------------------------------------------------------------------------

def degradation_probes() -> dict:
    """三条降级路径的**实测**表现（不调 LLM、不联网）。

    1) 天气不可用：把 B03 证据换成 status="unavailable"，看方向、分歧、
       combined_attention 各自变成什么（必须是"显式不可用"，不是静默消失）。
    2) 知识检索无命中：真的跑一次 B05 的检索（本地 TF-IDF，不联网），
       验证"全部 score == 0 → status 仍是 ok，result 明写无相关知识"。
    3) 工具调用失败：status="error" 的工具结果 → 方向必须是 undetermined。

    返回的每个数字都来自本次真实执行。
    """
    from B07.evidence_registry import make_tool_result
    from B07.judgement import build_evidence_divergence, combined_attention

    out: dict = {}

    # ---- 1) 天气不可用 ----
    led = EvidenceLedger()
    led.record_call("B05.get_weather_forecast",
                    arguments={"location": WEATHER_LOCATION},
                    purpose="降级探针：模拟天气服务不可用")
    led.add_evidence(
        make_tool_result("B03/Open-Meteo", "unavailable", None,
                         "模拟天气服务不可用（降级探针）"),
        "B03", tool_name="B05.get_weather_forecast",
        ext={"provider": None, "forecast_time": None},
    )
    div = build_evidence_divergence(led)
    out["weather_unavailable"] = {
        "probe": "B03 证据 status=unavailable（模拟天气服务不可用）",
        "entry_status": "unavailable",
        "entry_error": "模拟天气服务不可用（降级探针）",
        "entry_result_is_none": True,
        "direction": div["each_source"].get("B03"),
        "has_conflict": div["has_conflict"],
        "in_undetermined": any(i["source_type"] == "B03"
                               for i in div["undetermined"]),
        "undetermined_reason": next(
            (i["reason"] for i in div["undetermined"]
             if i["source_type"] == "B03"), None),
        "combined_attention_watch": combined_attention("WATCH", None),
        "combined_attention_normal": combined_attention("NORMAL", None),
    }

    # ---- 2) 知识检索无命中（真跑一次检索；本地 TF-IDF，不联网）----
    kb = search_knowledge("!!!???")
    res = kb.get("result") or {}
    out["knowledge_no_hit"] = {
        "probe": "search_knowledge('!!!???')",
        "status": kb.get("status"),
        "has_relevant": res.get("has_relevant"),
        "citation_count": len(res.get("citations") or []),
        "all_scores_zero": all(
            float(c.get("score") or 0.0) == 0.0
            for c in (res.get("citations") or [])),
        "note": res.get("note"),
        "error": kb.get("error"),
    }

    # ---- 3) 工具调用失败 ----
    led2 = EvidenceLedger()
    led2.record_call("B02.get_environment_history",
                     arguments={"start": None, "end": None},
                     purpose="降级探针：模拟工具异常")
    led2.add_evidence(
        make_tool_result("B02/environment_samples", "error", None,
                         "模拟工具异常（降级探针）"),
        "B02", tool_name="B02.get_environment_history",
    )
    div2 = build_evidence_divergence(led2)
    out["tool_error"] = {
        "probe": "B02 证据 status=error（模拟工具异常）",
        "entry_status": "error",
        "entry_error": "模拟工具异常（降级探针）",
        "entry_result_is_none": True,
        "direction": div2["each_source"].get("B02"),
        "has_conflict": div2["has_conflict"],
    }

    return out


# --------------------------------------------------------------------------
# 原始结果落盘（S4-4 报告的唯一输入）
# --------------------------------------------------------------------------

def _run_inventory() -> list[dict]:
    """全部 S4 存档的清单（成功与失败都收，不筛选）。"""
    rows = []
    runs_dir = _PROJECT_ROOT / "B07" / "runs"
    for p in sorted(runs_dir.glob("run_*/run.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("scenario") != "S4":
            continue
        lm = d.get("llm_meta") or {}
        rows.append({
            "run_id": d.get("run_id"),
            "created_at": d.get("created_at"),
            "status": d.get("status"),
            "failed_stage": d.get("failed_stage"),
            "failure_reason": d.get("failure_reason"),
            "model": lm.get("model"),
            "content_length": lm.get("content_length"),
            "usage": lm.get("usage"),
        })
    return rows


def _model_by_scenario() -> dict:
    """每个阶段实际用过的 LLM 模型（从存档统计，不手填）。"""
    import collections

    counter = collections.Counter()
    runs_dir = _PROJECT_ROOT / "B07" / "runs"
    for p in sorted(runs_dir.glob("run_*/run.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sc = d.get("scenario") or "(无场景)"
        if d.get("status") != "success":
            continue
        counter[(sc, (d.get("llm_meta") or {}).get("model"))] += 1
    out: dict = {}
    for (sc, model), n in sorted(counter.items()):
        out.setdefault(sc, {"model": model, "success_runs": n})
    return out


def write_raw_result(archive: dict) -> dict:
    """把 S4 阶段的实测结果落盘成 s4_raw_result.json。

    这是 s4_report.py 的**唯一输入**：报告里的每个数字都从这里取，
    报告生成过程不联网、不调 LLM，可确定性重放。
    """
    contrast = None
    if CONTRAST_PATH.exists():
        try:
            contrast = json.loads(CONTRAST_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            contrast = None

    from B07.explain import FIXED_QUESTION, SYSTEM_PROMPT, _sha12

    raw = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "s4_run": archive,
        "contrast": contrast,
        "degradation_probes": degradation_probes(),
        "run_inventory": _run_inventory(),
        "model_by_scenario": _model_by_scenario(),
        "prompt_fingerprints": {
            "system_prompt_sha256": _sha12(SYSTEM_PROMPT),
            "question_sha256": _sha12(FIXED_QUESTION),
            "fixed_question": FIXED_QUESTION,
        },
    }
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    RAW_RESULT_PATH.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return raw


# --------------------------------------------------------------------------
# 打印
# --------------------------------------------------------------------------

def _print_divergence(div: dict) -> None:
    print("\n" + "-" * 78)
    print("证据分歧（evidence_divergence）")
    print("-" * 78)
    print(f"  has_conflict = {div.get('has_conflict')}")
    print(f"  each_source   = {json.dumps(div.get('each_source'), ensure_ascii=False)}")
    print(f"  resolution    = {div.get('resolution')}")

    print("\n  支持提高关注（supporting）：")
    for it in div.get("supporting") or []:
        print(f"    - [{it['source_type']}] {it['source']} ｜ {it['time']}")
        print(f"        摘要：{it['summary']}")
        print(f"        依据：{it['basis']}")
    if not (div.get("supporting") or []):
        print("    （无）")

    print("\n  提示可能缓解或反向（opposing）：")
    for it in div.get("opposing") or []:
        print(f"    - [{it['source_type']}] {it['source']} ｜ {it['time']}")
        print(f"        摘要：{it['summary']}")
        print(f"        依据：{it['basis']}")
    if not (div.get("opposing") or []):
        print("    （无）")

    print("\n  仍不足以确定（undetermined）：")
    for it in div.get("undetermined") or []:
        print(f"    - [{it['source_type']}] {it['source']} ｜ {it['time']}")
        print(f"        摘要：{it['summary']}")
        print(f"        为什么仍不足以确定：{it['reason']}")
    if not (div.get("undetermined") or []):
        print("    （无）")


def _print_ledger(ledger_data: dict) -> None:
    print("\n" + "-" * 78)
    print("证据台账")
    print("-" * 78)
    for row in format_evidence_table(ledger_data):
        print(f"  {row}")

    adoption = ledger_data.get("adoption") or {}
    print(f"\n  {adoption.get('text')}")
    print("\n  工具调用明细：")
    for rec in (ledger_data.get("tool_calls") or {}).get("records") or []:
        mark = "采用" if rec.get("adopted") else "未采用"
        print(f"    - {rec.get('tool_name')}（status={rec.get('status')}，{mark}）"
              f"：{rec.get('purpose') or ''}")
    for na in adoption.get("not_adopted_calls") or []:
        print(f"    未采用原因：{na.get('tool_name')} —— {na.get('reason')}")


def main() -> int:
    print("=" * 78)
    print("B07 S4-2 —— 证据分歧场景（内部 WATCH × 外部转好）")
    print("=" * 78)

    archive = run_s4()
    ledger_data = archive.get("evidence_ledger") or {}
    judgement = archive.get("structured_judgement") or {}
    divergence = judgement.get("evidence_divergence") or {}
    ca = archive.get("combined_attention") or {}

    print(f"\nrun_id = {archive.get('run_id')}")
    print(f"锚点    = {archive.get('analysis_time')}")
    print(f"结构化判断字段 = {list(judgement.keys())}")

    _print_ledger(ledger_data)
    _print_divergence(divergence)

    print("\n" + "-" * 78)
    print("合成关注等级")
    print("-" * 78)
    print(f"  combined_attention = {ca.get('value')}")
    print(f"  理由：{ca.get('reason')}")

    # ---- 补充1：external_conditions 必须从台账派生 ----
    print("\n" + "-" * 78)
    print("补充1 核对：external_conditions 是否从台账的 B03 证据派生")
    print("-" * 78)
    weather = weather_context_from_ledger(ledger_data) or {}
    ext_cond = judgement.get("external_conditions") or {}
    b03 = None
    for e in ledger_data.get("entries") or []:
        if e.get("source_type") == "B03":
            b03 = e
            break
    b03_ext = (b03 or {}).get("ext") or {}
    print(f"  台账 B03 证据：{(b03 or {}).get('evidence_id')}")
    print(f"    ext.provider       = {b03_ext.get('provider')!r}")
    print(f"    ext.forecast_time  = {b03_ext.get('forecast_time')!r}")
    print(f"  external_conditions.provider      = {ext_cond.get('provider')!r}")
    print(f"  external_conditions.forecast_time = {ext_cond.get('forecast_time')!r}")
    same = (b03_ext.get("provider") == ext_cond.get("provider")
            and b03_ext.get("forecast_time") == ext_cond.get("forecast_time"))
    print(f"  逐值相等：{'✅ 是' if same else '❌ 否'}")

    # ---- ⑦ 上下文块 ----
    context = archive.get("llm_context") or ""
    if "【⑦ 证据分歧】" in context:
        block = context.split("【⑦ 证据分歧】", 1)[1]
        print("\n" + "-" * 78)
        print("⑦ 上下文块（原文）")
        print("-" * 78)
        print("【⑦ 证据分歧】" + block)

    print("\n" + "-" * 78)
    print("LLM 回答（本次这一次，不做采样统计）")
    print("-" * 78)
    print(archive.get("llm_explanation"))

    raw = write_raw_result(archive)
    print("\n" + "-" * 78)
    print("原始结果落盘")
    print("-" * 78)
    print(f"  {RAW_RESULT_PATH}")
    print(f"  含 S4-3 对照：{'✅ 已并入' if raw.get('contrast') else '❌ 未找到'}"
          f"（{CONTRAST_PATH.name}）")
    print(f"  降级探针：{len(raw.get('degradation_probes') or {})} 条")

    return 0


if __name__ == "__main__":
    sys.exit(main())
