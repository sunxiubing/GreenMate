"""B07 S5-1 —— 证据退化实验（一次只弄坏一条证据）

这一轮**不增加任何新功能模块**，只把一条已有证据主动弄坏，观察系统还剩什么。

文档 S5（p.38–41）要求：

  第一步：先跑一个完整版本（所有证据正常）存下 run_id，作为对照；
  第二步：主动制造一种真实失败，**一次只制造一种**；
  第三步：**先不要修** —— 出现失败后不要"赶紧恢复 API"，
          继续运行同一个问题，观察系统现在还能做什么；
  第四步：系统必须明确说出"还能判断什么"；
  第五步：系统必须明确说出"现在不能判断什么"；
  第六步：比较前后两个 run_id（下一步做，这轮先把两个 run 跑出来）。

本轮选的唯一失败：**天气证据不可用**。

  理由：天气是唯一同时影响 ⑥ 外部条件块、⑦ 分歧三列表、
        combined_attention 三处判据的证据，降级效果最容易观测。
        RAG 无命中 / 工具异常留作 S5 的可选扩展，本轮**不做**。

怎么做：环境变量开关 B07_WEATHER_UNAVAILABLE=1（见 B07/weather.py）。
      显式模拟"天气 API 暂时不可用"，理由是可复现、可回滚，
      且不依赖网络状态 —— 真断网既不可复现，
      又会把"天气不可用"和"整个网络坏了"混在一起。

两个 run 的**唯一变量**就是这个开关：

  基线 run：B07_WEATHER_UNAVAILABLE 未设置 → 全部证据正常
  降级 run：B07_WEATHER_UNAVAILABLE=1 → 只有天气证据不可用

场景完全沿用 S4（同一 anchor、同一 B06 窗口、同一离线 improving 夹具、
同一知识检索 query、同一固定问题、同一 system prompt），否则对照不成立。

硬约束：
  - 不改 SYSTEM_PROMPT、不改固定问题、不改任何判据阈值、不改模型
  - 不改 B01 / B02 / B03 / B04 / B05 / B06
  - 不可用时**不加任何兜底**，不让结论"看起来和正常时一样"
  - 不把不可用证据的方向伪造成 supporting / opposing
  - 本轮不 git commit

用法：

    python -m B07.s5

产物：
  B07/artifacts/s5_raw_result.json —— 两个 run 的实测原始结果
  B07/artifacts/s5_report.md       —— 由 s5_report.py 从上面那份 JSON 生成
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from B07.weather import WEATHER_UNAVAILABLE_ENV

#: 产物目录
ARTIFACTS = _PROJECT_ROOT / "B07" / "artifacts"

#: S5 原始结果（S5 报告的唯一输入）
RAW_RESULT_PATH = ARTIFACTS / "s5_raw_result.json"


# --------------------------------------------------------------------------
# 跑一个变体
# --------------------------------------------------------------------------

def run_variant(degraded: bool) -> dict:
    """跑一次 S5 场景。

    Args:
        degraded: True = 打开降级开关（天气证据不可用）；
                  False = 基线（一切正常）。

    Returns:
        {"variant", "degraded", "archive", "offline", "ledger_view"}
    """
    from B07.runs import run_analysis
    from B07.s4 import RUN_LOCATION, build_ledger
    from B07.s4_offline import build_offline_cooling_context, default_anchor

    anchor = default_anchor()

    # 开关状态是这次运行的**唯一变量**：跑之前清干净，跑完恢复。
    os.environ.pop(WEATHER_UNAVAILABLE_ENV, None)
    if degraded:
        os.environ[WEATHER_UNAVAILABLE_ENV] = "1"
    try:
        offline = build_offline_cooling_context(anchor)

        def _factory(bundle):
            return build_ledger(bundle, offline, anchor)

        archive = run_analysis(
            anchor=anchor,
            location=RUN_LOCATION,
            scenario="S5",
            external_conditions=offline["context"],
            evidence_ledger=_factory,
        )
    finally:
        os.environ.pop(WEATHER_UNAVAILABLE_ENV, None)

    return {
        "variant": "degraded" if degraded else "baseline",
        "degraded": degraded,
        "archive": archive,
        "offline": offline,
    }


# --------------------------------------------------------------------------
# 逐项对比
# --------------------------------------------------------------------------

def _evidence_rows(archive: dict) -> list[dict]:
    """证据条数与每条证据的 status（对比表用）。"""
    ledger = archive.get("evidence_ledger") or {}
    rows = []
    for e in ledger.get("entries") or []:
        rows.append({
            "evidence_id": e.get("evidence_id"),
            "source_type": e.get("source_type"),
            "source": e.get("source"),
            "time": e.get("time"),
            "status": e.get("status"),
            "direction": e.get("direction"),
            "adopted": e.get("adopted"),
            "error": e.get("error"),
            "provider": (e.get("ext") or {}).get("provider"),
            "forecast_time": (e.get("ext") or {}).get("forecast_time"),
        })
    return rows


def _tool_calls(archive: dict) -> list[dict]:
    """工具调用台账（status / result 摘要 / error 三列）。"""
    ledger = archive.get("evidence_ledger") or {}
    calls = (ledger.get("tool_calls") or {}).get("records") or []
    entries = {e.get("evidence_id"): e for e in ledger.get("entries") or []}
    out = []
    for rec in calls:
        out.append({
            "tool_name": rec.get("tool_name"),
            "status": rec.get("status"),
            "purpose": rec.get("purpose"),
            "adopted": rec.get("adopted"),
            "result_digest": rec.get("result_digest"),
            "error": rec.get("error"),
        })
    return out


def _divergence(archive: dict) -> dict:
    div = (archive.get("structured_judgement") or {}).get("evidence_divergence") or {}
    return {
        "has_conflict": div.get("has_conflict"),
        "supporting": len(div.get("supporting") or []),
        "opposing": len(div.get("opposing") or []),
        "undetermined": len(div.get("undetermined") or []),
        "each_source": div.get("each_source"),
        "resolution": div.get("resolution"),
        "supporting_items": div.get("supporting") or [],
        "opposing_items": div.get("opposing") or [],
        "undetermined_items": div.get("undetermined") or [],
    }


def _answer_keywords(answer: str) -> dict:
    """LLM 回答里有没有说到天气/降水/受限制（纯字符串判定，不做语义推断）。"""
    text = answer or ""
    return {
        "mentions_weather": ("天气" in text),
        "mentions_precipitation": ("降水" in text or "降雨" in text),
        "mentions_limitation": any(
            k in text for k in ("受限", "限制", "无法", "不可用", "未能", "不能", "缺失")),
        "mentions_available": any(
            k in text for k in ("历史", "模型", "知识", "仍然", "依旧")),
        "says_cannot_analyze": ("无法分析" in text or "无法判断" in text
                                or "无法给出" in text),
        "length": len(text),
    }


def _sha12(text: str) -> str:
    import hashlib
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def _judgement_external(archive: dict) -> dict:
    """结构化判断里的 external_conditions（第 ⑥ 块的来源）。

    注意区分两个东西：
      - archive["external_conditions"] = provider 的原始返回；
      - structured_judgement["external_conditions"] = 从台账派生出来的
        第 6 个字段（带 provider / forecast_time / status / error）。
    S5 要对比的是后者 —— 它才是 ⑥ 块实际渲染的东西。
    """
    return (archive.get("structured_judgement") or {}).get(
        "external_conditions") or {}


def build_comparison(baseline: dict, degraded: dict) -> list[dict]:
    """逐项对比表（两列：基线 / 降级）。

    每一行都是**实测值**，不是断言。
    """
    b = baseline["archive"]
    d = degraded["archive"]

    b_ext = _judgement_external(b)
    d_ext = _judgement_external(d)
    b_raw = b.get("external_conditions") or {}
    d_raw = d.get("external_conditions") or {}
    b_div = _divergence(b)
    d_div = _divergence(d)
    b_rows = _evidence_rows(b)
    d_rows = _evidence_rows(d)

    status_changes = []
    for br, dr in zip(b_rows, d_rows):
        if br.get("evidence_id") != dr.get("evidence_id"):
            continue
        if br.get("status") != dr.get("status"):
            status_changes.append({
                "evidence_id": dr.get("evidence_id"),
                "source_type": dr.get("source_type"),
                "baseline_status": br.get("status"),
                "degraded_status": dr.get("status"),
                "baseline_direction": br.get("direction"),
                "degraded_direction": dr.get("direction"),
                "error": dr.get("error"),
            })

    b_ans = b.get("llm_explanation") or ""
    d_ans = d.get("llm_explanation") or ""

    return [
        {"item": "run_id", "baseline": b.get("run_id"), "degraded": d.get("run_id")},
        {"item": "场景 / 锚点",
         "baseline": f"{b.get('scenario')} @ {b.get('analysis_time')}",
         "degraded": f"{d.get('scenario')} @ {d.get('analysis_time')}"},
        {"item": "固定问题 sha256",
         "baseline": (b.get("llm_meta") or {}).get("question_sha256"),
         "degraded": (d.get("llm_meta") or {}).get("question_sha256")},
        {"item": "system prompt sha256",
         "baseline": (b.get("llm_meta") or {}).get("system_prompt_sha256"),
         "degraded": (d.get("llm_meta") or {}).get("system_prompt_sha256")},
        {"item": "LLM 模型",
         "baseline": (b.get("llm_meta") or {}).get("model"),
         "degraded": (d.get("llm_meta") or {}).get("model")},
        {"item": "降级开关 B07_WEATHER_UNAVAILABLE",
         "baseline": "未设置（正常）", "degraded": "=1"},
        {"item": "证据条数（台账 entries）",
         "baseline": len(b_rows), "degraded": len(d_rows)},
        {"item": "证据 status 变化",
         "baseline": "—",
         "degraded": json.dumps(status_changes, ensure_ascii=False)},
        {"item": "天气证据 status",
         "baseline": next((r["status"] for r in b_rows
                           if r["source_type"] == "B03"), None),
         "degraded": next((r["status"] for r in d_rows
                           if r["source_type"] == "B03"), None)},
        {"item": "天气证据 direction",
         "baseline": next((r["direction"] for r in b_rows
                           if r["source_type"] == "B03"), None),
         "degraded": next((r["direction"] for r in d_rows
                           if r["source_type"] == "B03"), None)},
        {"item": "分歧 supporting 条数",
         "baseline": b_div["supporting"], "degraded": d_div["supporting"]},
        {"item": "分歧 opposing 条数",
         "baseline": b_div["opposing"], "degraded": d_div["opposing"]},
        {"item": "分歧 undetermined 条数",
         "baseline": b_div["undetermined"], "degraded": d_div["undetermined"]},
        {"item": "has_conflict",
         "baseline": b_div["has_conflict"], "degraded": d_div["has_conflict"]},
        {"item": "combined_attention",
         "baseline": (b.get("combined_attention") or {}).get("value"),
         "degraded": (d.get("combined_attention") or {}).get("value")},
        {"item": "external_conditions.status",
         "baseline": b_ext.get("status"), "degraded": d_ext.get("status")},
        {"item": "external_conditions.signal",
         "baseline": b_ext.get("signal"), "degraded": d_ext.get("signal")},
        {"item": "external_conditions.provider",
         "baseline": b_ext.get("provider"), "degraded": d_ext.get("provider")},
        {"item": "external_conditions.forecast_time",
         "baseline": b_ext.get("forecast_time"),
         "degraded": d_ext.get("forecast_time")},
        {"item": "external_conditions.error",
         "baseline": b_ext.get("error"), "degraded": d_ext.get("error")},
        {"item": "provider 原始返回 status",
         "baseline": b_raw.get("status"), "degraded": d_raw.get("status")},
        {"item": "provider 原始返回 error",
         "baseline": b_raw.get("error"), "degraded": d_raw.get("error")},
        {"item": "llm_context sha256",
         "baseline": _sha12(b.get("llm_context")),
         "degraded": _sha12(d.get("llm_context"))},
        {"item": "LLM 回答里提到天气",
         "baseline": _answer_keywords(b_ans)["mentions_weather"],
         "degraded": _answer_keywords(d_ans)["mentions_weather"]},
        {"item": "LLM 回答里提到降水",
         "baseline": _answer_keywords(b_ans)["mentions_precipitation"],
         "degraded": _answer_keywords(d_ans)["mentions_precipitation"]},
        {"item": "LLM 回答里提到受限制/不可用",
         "baseline": _answer_keywords(b_ans)["mentions_limitation"],
         "degraded": _answer_keywords(d_ans)["mentions_limitation"]},
        {"item": "LLM 回答整体输出“无法分析”",
         "baseline": _answer_keywords(b_ans)["says_cannot_analyze"],
         "degraded": _answer_keywords(d_ans)["says_cannot_analyze"]},
        {"item": "LLM 回答字数",
         "baseline": len(b_ans), "degraded": len(d_ans)},
    ]


# --------------------------------------------------------------------------
# 原始结果落盘
# --------------------------------------------------------------------------

def _pack(v: dict) -> dict:
    a = v["archive"]
    return {
        "variant": v["variant"],
        "degraded": v["degraded"],
        "run_id": a.get("run_id"),
        "created_at": a.get("created_at"),
        "status": a.get("status"),
        "scenario": a.get("scenario"),
        "analysis_time": a.get("analysis_time"),
        "question": a.get("question"),
        "llm_meta": a.get("llm_meta"),
        "llm_context": a.get("llm_context"),
        "llm_context_sha256": _sha12(a.get("llm_context")),
        "llm_explanation": a.get("llm_explanation"),
        "answer_keywords": _answer_keywords(a.get("llm_explanation")),
        "structured_judgement": a.get("structured_judgement"),
        "combined_attention": a.get("combined_attention"),
        "external_conditions": a.get("external_conditions"),
        "evidence_rows": _evidence_rows(a),
        "tool_calls": _tool_calls(a),
        "divergence": _divergence(a),
        "warnings": a.get("warnings"),
        "offline_note": (v["offline"] or {}).get("note"),
        "offline_degraded": (v["offline"] or {}).get("degraded"),
    }


def _fixture_values(archive: dict) -> list[str]:
    """把**基线**那次用到的外部条件数值取出来，做降级回答的数值红线条目。

    数值来自实测存档（不是手填），所以"回答里出现了这个数"才判伪造。
    """
    ext = archive.get("external_conditions") or {}
    out: list[str] = []
    for key in ("baseline_point", "forecast_point"):
        pt = ext.get(key) or {}
        for field, suffix in (("precipitation_probability", "%"),
                              ("temperature", ""),
                              ("cloud_cover", "%")):
            v = pt.get(field)
            if v is not None:
                out.append(f"{v:.1f}{suffix}")
    for field in ("precip_delta", "temp_delta", "cloud_delta"):
        v = ext.get(field)
        if v is not None:
            out.append(f"Δ={v}")
            out.append(f"Δ {v}")
    return out


def write_raw_result(baseline: dict, degraded: dict,
                     comparison: list[dict],
                     baseline_runs: list[dict] | None = None,
                     degraded_runs: list[dict] | None = None,
                     probe: dict | None = None) -> dict:
    from B07.explain import FIXED_QUESTION, SYSTEM_PROMPT

    raw = {
        "generated_at": None,
        "experiment": {
            "name": "S5-1 证据退化：天气证据不可用",
            "switch": f"{WEATHER_UNAVAILABLE_ENV}=1",
            "why_weather": (
                "天气是唯一同时影响 ⑥ 外部条件块、⑦ 分歧三列表、"
                "combined_attention 三处判据的证据，降级效果最容易观测；"
                "RAG 无命中与工具异常留作 S5 可选扩展，本轮不做。"),
            "scenario": "S5（场景定义与 S4 一字不差：同一 anchor、同一窗口、"
                        "同一离线 improving 夹具、同一知识 query、同一固定问题）",
            "only_variable": "B07_WEATHER_UNAVAILABLE 环境变量",
            "n_baseline": len(baseline_runs or [baseline]),
            "n_degraded": len(degraded_runs or [degraded]),
            "note": ("每种状态跑 N 次；主对照对 = 各自的第 1 次 run。"
                     "N>1 的目的是让采样波动可见（L4）。"),
        },
        "prompt_fingerprints": {
            "system_prompt_sha256": _sha12(SYSTEM_PROMPT),
            "question_sha256": _sha12(FIXED_QUESTION),
            "fixed_question": FIXED_QUESTION,
        },
        "baseline": _pack(baseline),
        "degraded": _pack(degraded),
        "comparison": comparison,
        "baseline_runs": [_pack(v) for v in (baseline_runs or [baseline])],
        "degraded_runs": [_pack(v) for v in (degraded_runs or [degraded])],
        "probe": probe or {},
    }
    from datetime import datetime
    raw["generated_at"] = datetime.now().isoformat(timespec="seconds")

    RAW_RESULT_PATH.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return raw


# --------------------------------------------------------------------------
# 重新跑防伪造检验（离线，不调 LLM，不重跑 run）
# --------------------------------------------------------------------------

def reprobe() -> dict:
    """用**已落盘的回答原文**重跑 A/B/C/D 四项检验，并更新 s5_raw_result.json。

    检测口径改了以后不必重跑 LLM —— 回答原文都在存档里，
    离线重算即可（这也保证"报告里的数字来自存档"）。

    Returns:
        更新后的 raw result dict。
    """
    if not RAW_RESULT_PATH.exists():
        raise FileNotFoundError(f"{RAW_RESULT_PATH} 不存在，先跑 python -m B07.s5")

    raw = json.loads(RAW_RESULT_PATH.read_text(encoding="utf-8"))

    from B07.s5_probe import probe_runs

    values = raw.get("probe", {}).get("checked_values") or []
    if not values:
        values = _fixture_values(raw["baseline"])

    def _recs(runs):
        return [{"run_id": r.get("run_id"),
                 "status": r.get("status"),
                 "llm_explanation": r.get("llm_explanation")}
                for r in runs]

    raw.setdefault("probe", {})
    raw["probe"] = {
        "checked_values": values,
        "value_source": raw.get("probe", {}).get(
            "value_source", "基线 run 的 external_conditions（实测，不是手填）"),
        "degraded": probe_runs(_recs(raw.get("degraded_runs") or []),
                                extra_values=values),
        "baseline": probe_runs(_recs(raw.get("baseline_runs") or []),
                                extra_values=values),
    }
    RAW_RESULT_PATH.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return raw


def probe_archived(run_ids: list[str]) -> dict:
    """对**已在存档里**的 run 重跑 A/B/C/D 四项检验（离线，不调 LLM）。

    用途：把"上一版上下文块（未加'必答'要求时）"的实测结果也变成
    **从存档算出来的数字**，而不是报告里手写的一句话。

    Args:
        run_ids: 存档的 run_id 列表。

    Returns:
        probe_runs 的结果。
    """
    from B07.runs import run_json_path
    from B07.s5_probe import probe_runs

    recs = []
    for rid in run_ids:
        p = run_json_path(rid)
        if not p or not Path(p).exists():
            continue
        arch = json.loads(Path(p).read_text(encoding="utf-8"))
        recs.append({
            "run_id": rid,
            "status": arch.get("status"),
            "llm_explanation": arch.get("llm_explanation"),
        })

    raw = json.loads(RAW_RESULT_PATH.read_text(encoding="utf-8"))
    values = (raw.get("probe") or {}).get("checked_values") or []
    out = probe_runs(recs, extra_values=values)
    out["run_ids"] = list(run_ids)
    return out


# --------------------------------------------------------------------------
# 主程序
# --------------------------------------------------------------------------

def main(n: int = 2) -> int:
    print("=" * 78)
    print("B07 S5-1 —— 证据退化实验（唯一变量：天气证据是否可用）")
    print("=" * 78)

    print(f"\n[1] 基线 run × {n}（所有证据正常）")
    print("-" * 78)
    baseline_runs = []
    for i in range(1, n + 1):
        v = run_variant(degraded=False)
        baseline_runs.append(v)
        print(f"  #{i} run_id = {v['archive'].get('run_id')}  "
              f"signal = "
              f"{(v['archive'].get('external_conditions') or {}).get('signal')}")

    print(f"\n[2] 降级 run × {n}（天气证据不可用；其它一切不变）")
    print("-" * 78)
    degraded_runs = []
    for i in range(1, n + 1):
        v = run_variant(degraded=True)
        degraded_runs.append(v)
        print(f"  #{i} run_id = {v['archive'].get('run_id')}  "
              f"signal = "
              f"{(v['archive'].get('external_conditions') or {}).get('signal')}")

    baseline = baseline_runs[0]
    degraded = degraded_runs[0]
    comparison = build_comparison(baseline, degraded)

    print("\n" + "=" * 78)
    print("逐项对比表（主对照对）")
    print("=" * 78)
    print(f"{'对比项':<34} | {'基线':<28} | 降级")
    print("-" * 78)
    for row in comparison:
        b = str(row["baseline"])
        d = str(row["degraded"])
        if len(b) > 28:
            b = b[:25] + "..."
        if len(d) > 60:
            d = d[:57] + "..."
        print(f"{row['item']:<34} | {b:<28} | {d}")
    print("-" * 78)

    # ---- 防伪造检验（S5-2）----
    from B07.s5_probe import probe_runs

    values = _fixture_values(baseline["archive"])
    probe = {
        "checked_values": values,
        "value_source": "基线 run 的 external_conditions（实测，不是手填）",
        "degraded": probe_runs(
            [{"run_id": v["archive"].get("run_id"),
              "status": v["archive"].get("status"),
              "llm_explanation": v["archive"].get("llm_explanation")}
             for v in degraded_runs],
            extra_values=values),
    }

    raw = write_raw_result(baseline, degraded, comparison,
                            baseline_runs=baseline_runs,
                            degraded_runs=degraded_runs,
                            probe=probe)
    print("\n" + "=" * 78)
    print("防伪造检验（降级 run，分母含失败）")
    print("=" * 78)
    print(f"  通过            : {probe['degraded']['rate_text']['pass']}")
    print(f"  伪造证据        : {probe['degraded']['rate_text']['fabricated_evidence']}")
    print(f"  未声明受限      : {probe['degraded']['rate_text']['no_limitation_disclosure']}")
    print(f"  能力全丢        : {probe['degraded']['rate_text']['capability_lost']}")
    print(f"  归因错误        : {probe['degraded']['rate_text']['misattributed']}")
    print(f"  失败 run        : {probe['degraded']['failed']}")

    print(f"\n原始结果已落盘：{RAW_RESULT_PATH}")
    print(f"  基线 run_id = {raw['baseline']['run_id']}（共 {len(baseline_runs)} 次）")
    print(f"  降级 run_id = {raw['degraded']['run_id']}（共 {len(degraded_runs)} 次）")
    print(f"  对比项 = {len(comparison)} 条")

    print("\n" + "=" * 78)
    print("降级 run 的 LLM 回答（原文，主对照对）")
    print("=" * 78)
    print(degraded["archive"].get("llm_explanation"))

    print("\n" + "=" * 78)
    print("S5-1/2 完成 —— 报告请跑：python B07/s5_report.py")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    if "--probe-runs" in sys.argv:
        i = sys.argv.index("--probe-runs")
        ids = [x for x in (sys.argv[i + 1] if len(sys.argv) > i + 1 else "").split(",")
               if x]
        out = probe_archived(ids)
        raw = json.loads(RAW_RESULT_PATH.read_text(encoding="utf-8"))
        raw.setdefault("probe", {})["prior_version"] = out
        RAW_RESULT_PATH.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已把 {ids} 的检验结果写入 {RAW_RESULT_PATH}")
        print(f"  {out['rate_text']}")
        sys.exit(0)
    if "--reprobe" in sys.argv:
        r = reprobe()
        print(f"已重跑防伪造检验并更新 {RAW_RESULT_PATH}")
        print(f"  降级组：{r['probe']['degraded']['rate_text']}")
        print(f"  失败 run：{r['probe']['degraded']['failed']}")
        sys.exit(0)
    sys.exit(main())
