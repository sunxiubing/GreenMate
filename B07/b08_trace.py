"""B08 第三遍 · 12 环节运行追踪（文档 p.97–98）

    python -m B07.b08_trace run_082            # 终端输出
    python -m B07.b08_trace run_082 --json     # 结构化输出

★ 这是一个**薄包装**：数据全部来自 `B07/runs/<run_id>/run.json`。
  **不调 LLM、不跑模型、不取天气、不检索知识、不改存档。**
  唯一允许的"算"是把存档里的 history 重新喂给 `judge_stability`
  —— 那是**纯函数复算**，用来证明稳定性判定不是抄来的。

★ 12 个环节，每段都标明**它来自哪个字段**：

    ❶ 用户提出了什么问题      → question / question_sha256
    ❷ 当前状态是什么          → current_state
    ❸ 读取了哪一段历史数据    → history_window + history
    ❹ 调用了哪些工具          → evidence_ledger.tool_calls.records
    ❺ 哪些工具结果被采用为证据 → tool_calls( adopted=true ) + entries + not_adopted_reasons
    ❻ 天气返回了什么          → external_conditions + 台账 B03 条
    ❼ 知识检索到了什么        → 台账 B04 条
    ❽ 模型输出了什么          → model_output
    ❾ 哪些证据有效            → entries[*].status / direction
    ❿ 结构化判断怎样形成      → structured_judgement + judge_stability 复算
    ⓫ LLM 如何解释            → llm_explanation + llm_meta
    ⓬ 有哪些限制              → structured_judgement.limitations

    末端一段**自检**：覆盖 12/12 环节；缺哪一环就明说缺哪一环，不许静默省略。
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

RUNS_DIR = _PROJECT_ROOT / "B07" / "runs"


# --------------------------------------------------------------------------
# 读存档
# --------------------------------------------------------------------------

def load_archive(run_id: str) -> dict:
    p = RUNS_DIR / run_id / "run.json"
    if not p.exists():
        raise FileNotFoundError(f"找不到存档：{p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _entries(archive: dict) -> list[dict]:
    led = archive.get("evidence_ledger") or {}
    return led.get("entries") or []


def _entry(archive: dict, source_type: str) -> dict | None:
    for e in _entries(archive):
        if e.get("source_type") == source_type:
            return e
    return None


def _result_dict(entry: dict | None) -> dict:
    """台账条目里的 result 可能是 dict，也可能是它的字符串表示。"""
    if not entry:
        return {}
    r = entry.get("result")
    if isinstance(r, dict):
        return r
    if isinstance(r, str):
        try:
            v = json.loads(r)
            if isinstance(v, dict):
                return v
        except Exception:                   # noqa: BLE001
            pass
    return {"_raw": r}


def _fmt(v, digits=1) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


# --------------------------------------------------------------------------
# 12 个环节
# --------------------------------------------------------------------------

def step1_question(a: dict) -> dict:
    meta = a.get("llm_meta") or {}
    return {
        "no": 1, "title": "用户提出了什么问题",
        "source": "run.json.question / llm_meta.question_sha256",
        "question": a.get("question"),
        "question_sha256": meta.get("question_sha256"),
        "system_prompt_sha256": meta.get("system_prompt_sha256"),
        "missing": [k for k in ("question",) if not a.get(k)]
                   + (["question_sha256"] if not meta.get("question_sha256") else []),
    }


def step2_current(a: dict) -> dict:
    cs = a.get("current_state") or {}
    return {
        "no": 2, "title": "当前状态是什么",
        "source": "run.json.current_state",
        "timestamp": cs.get("timestamp"),
        "temperature": cs.get("temperature"),
        "humidity": cs.get("humidity"),
        "lux": cs.get("lux"),
        "source_type": cs.get("source_type"),
        "sim_scenario": cs.get("sim_scenario"),
        "missing": [] if cs else ["current_state"],
    }


def step3_history(a: dict) -> dict:
    hw = a.get("history_window") or {}
    hist = a.get("history") or []
    return {
        "no": 3, "title": "读取了哪一段历史数据",
        "source": "run.json.history_window + run.json.history",
        "window_start": hw.get("start"),
        "window_end": hw.get("end"),
        "count": hw.get("count"),
        "points": [
            {"timestamp": h.get("timestamp"), "temperature": h.get("temperature"),
             "humidity": h.get("humidity"), "lux": h.get("lux")}
            for h in hist
        ],
        "missing": [] if hist else ["history"],
    }


def step4_tools(a: dict) -> dict:
    led = a.get("evidence_ledger") or {}
    tc = led.get("tool_calls") or {}
    records = tc.get("records") or []
    return {
        "no": 4, "title": "调用了哪些工具",
        "source": "run.json.evidence_ledger.tool_calls.records",
        "total": len(records),
        "records": [
            {"tool_name": r.get("tool_name"), "arguments": r.get("arguments"),
             "status": r.get("status"), "adopted": r.get("adopted"),
             "error": r.get("error"), "purpose": r.get("purpose")}
            for r in records
        ],
        "missing": [] if records else ["evidence_ledger.tool_calls.records"],
    }


def step5_adoption(a: dict) -> dict:
    led = a.get("evidence_ledger") or {}
    tc = led.get("tool_calls") or {}
    records = tc.get("records") or []
    reasons = led.get("not_adopted_reasons") or {}
    entries = _entries(a)
    adopted_entries = [e for e in entries if e.get("adopted")]

    adopted_tools = []
    for r in records:
        if not r.get("adopted"):
            continue
        hit = [e for e in entries
               if _matches_entry(r, e)]
        adopted_tools.append({
            "tool_name": r.get("tool_name"),
            "status": r.get("status"),
            "entry_ids": [e.get("evidence_id") for e in hit],
            "matched": bool(hit),
        })

    not_adopted = []
    for r in records:
        if r.get("adopted"):
            continue
        not_adopted.append({
            "tool_name": r.get("tool_name"),
            "status": r.get("status"),
            "reason": reasons.get(r.get("tool_name"), "未声明采用原因"),
        })

    return {
        "no": 5, "title": "哪些工具结果被转换为证据记录",
        "source": "run.json.evidence_ledger.tool_calls(adopted) + entries + not_adopted_reasons",
        "adopted_tools": adopted_tools,
        "adopted_entries": [
            {"evidence_id": e.get("evidence_id"), "source_type": e.get("source_type"),
             "status": e.get("status")}
            for e in adopted_entries
        ],
        "not_adopted_tools": not_adopted,
        "adoption_text": (led.get("adoption") or {}).get("text"),
        "counts": led.get("counts"),
        "missing": [] if entries else ["evidence_ledger.entries"],
    }


#: 工具调用名 → 证据来源类型（一次调用升格成哪条证据）
#: ★ 工具名前缀（B05/B02…）与证据 source_type（B02/B03/B04/B06）**不是一一对应**：
#:   天气是 B05.get_weather_forecast → EV-B03；知识是 B05.search_tomato_knowledge → EV-B04。
TOOL_TO_SOURCE = {
    "B02.get_environment_history": "B02",
    "B05.get_environment_history": "B02",
    "B06.predict_window": "B06",
    "B05.get_weather_forecast": "B03",
    "B05.search_tomato_knowledge": "B04",
}


def _matches_entry(rec: dict, entry: dict) -> bool:
    """把一次工具调用和一条证据记录对上。"""
    name = rec.get("tool_name") or ""
    st = (entry.get("source_type") or "").upper()
    mapped = TOOL_TO_SOURCE.get(name)
    if mapped:
        return mapped == st
    upper = name.upper()
    for token in ("B02", "B03", "B04", "B05", "B06"):
        if token in upper and token == st:
            return True
    return False


def step6_weather(a: dict) -> dict:
    ec = a.get("external_conditions") or {}
    b03 = _entry(a, "B03")
    ext = (b03 or {}).get("ext") or {}
    res = _result_dict(b03)
    has_ec = bool(ec)
    return {
        "no": 6, "title": "天气返回了什么",
        "source": "run.json.external_conditions + evidence_ledger.EV-B03",
        "external_conditions_present": has_ec,
        "provider": ext.get("provider") or ec.get("provider"),
        "retrieved_at": ext.get("retrieved_at") or ec.get("retrieved_at"),
        "forecast_time": ext.get("forecast_time") or ec.get("forecast_time"),
        "signal": ec.get("signal") or res.get("signal"),
        "available": ec.get("available") if ec else (
            (b03 or {}).get("status") == "ok"),
        "basis": ec.get("basis") or res.get("basis"),
        "judge_method": ec.get("judge_method") or res.get("judge_method"),
        "precip_delta": ec.get("precip_delta", res.get("precip_delta")),
        "temp_delta": ec.get("temp_delta", res.get("temp_delta")),
        "cloud_delta": ec.get("cloud_delta", res.get("cloud_delta")),
        "error": ext.get("error") or ec.get("error") or res.get("error"),
        "ledger_status": (b03 or {}).get("status"),
        "is_constructed": ext.get("is_constructed"),
        "missing": [] if (has_ec or b03) else ["external_conditions", "EV-B03"],
    }


def step7_knowledge(a: dict) -> dict:
    b04 = _entry(a, "B04")
    if not b04:
        return {
            "no": 7, "title": "知识检索到了什么",
            "source": "run.json.evidence_ledger.EV-B04",
            "present": False,
            "missing": ["evidence_ledger.EV-B04"],
            "note": "本次运行没有 B04 证据记录（没有检索过知识库，或结果未被采用）",
        }
    res = _result_dict(b04)
    cites = res.get("citations") or []
    top = cites[0] if cites else {}
    return {
        "no": 7, "title": "知识检索到了什么",
        "source": "run.json.evidence_ledger.EV-B04",
        "present": True,
        "status": b04.get("status"),
        "query": res.get("query"),
        "top_k": res.get("top_k"),
        "has_relevant": res.get("has_relevant"),
        "citation_count": len(cites),
        "top_hit": {
            "chunk_id": top.get("chunk_id"),
            "source_id": top.get("source_id"),
            "section": top.get("section"),
            "page": top.get("page"),
            "score": top.get("score"),
            "summary": (top.get("content") or "")[:120],
        },
        "missing": [],
    }


def step8_model(a: dict) -> dict:
    mo = a.get("model_output") or {}
    meta = a.get("model_meta") or {}
    coefs = mo.get("coef_top5") or []
    return {
        "no": 8, "title": "模型输出了什么",
        "source": "run.json.model_output + run.json.model_meta",
        "predicted_attention": mo.get("predicted_attention"),
        "confidence": mo.get("confidence"),
        "model_version": mo.get("model_version"),
        "window_id": mo.get("window_id"),
        "dtw": {
            "temperature": mo.get("dtw_temperature"),
            "humidity": mo.get("dtw_humidity"),
            "lux": mo.get("dtw_lux"),
        },
        "feature_count": mo.get("feature_count"),
        "coef_top5": coefs,
        "stability_baseline": mo.get("stability_baseline"),
        "label_source": meta.get("label_source"),
        "metrics": meta.get("metrics"),
        "sklearn_version": meta.get("sklearn_version"),
        "missing": [] if mo else ["model_output"],
    }


def step9_evidence(a: dict) -> dict:
    entries = _entries(a)
    rows = [
        {"evidence_id": e.get("evidence_id"), "source_type": e.get("source_type"),
         "status": e.get("status"), "direction": e.get("direction"),
         "adopted": e.get("adopted"), "error": e.get("error"),
         "time": e.get("time"), "source": e.get("source")}
        for e in entries
    ]
    return {
        "no": 9, "title": "哪些证据有效",
        "source": "run.json.evidence_ledger.entries[*].status / direction",
        "entries": rows,
        "counts": {
            "total": len(rows),
            "ok": sum(1 for r in rows if r.get("status") == "ok"),
            "unavailable": sum(1 for r in rows if r.get("status") == "unavailable"),
            "supporting": sum(1 for r in rows if r.get("direction") == "supporting"),
            "opposing": sum(1 for r in rows if r.get("direction") == "opposing"),
            "undetermined": sum(1 for r in rows if r.get("direction") == "undetermined"),
        },
        "missing": [] if rows else ["evidence_ledger.entries"],
    }


def step10_judgement(a: dict) -> dict:
    """结构化判断 + **稳定判定复算**（不是抄存档值）。"""
    j = a.get("structured_judgement") or {}
    rt = j.get("recent_trend") or {}
    recomputed = None
    recompute_err = None
    try:
        from B07.judgement import _judge_stability
        bundle = {
            "analysis_time": a.get("analysis_time"),
            "current_state": a.get("current_state"),
            "history": a.get("history") or [],
            "model_output": a.get("model_output"),
            "scenario": a.get("scenario"),
        }
        recomputed = _judge_stability(bundle)
    except Exception as exc:                 # noqa: BLE001
        recompute_err = f"{type(exc).__name__}: {exc}"

    archived_verdict = rt.get("stability_verdict")
    new_verdict = (recomputed or {}).get("stability_verdict")
    return {
        "no": 10, "title": "结构化判断怎样形成",
        "source": "run.json.structured_judgement + judge_stability(存档.history) 复算",
        "fields": list(j.keys()),
        "current_environment": j.get("current_environment"),
        "recent_trend_archived": {
            "window_span": rt.get("window_span"),
            "window_start": rt.get("window_start"),
            "window_end": rt.get("window_end"),
            "stability_verdict": archived_verdict,
            "conclusion": rt.get("conclusion"),
            "criteria": rt.get("criteria"),
            "thresholds_used": rt.get("thresholds_used"),
        },
        "predicted_attention": j.get("predicted_attention"),
        "evidence_status": j.get("evidence_status"),
        "external_conditions_in_judgement": j.get("external_conditions"),
        "evidence_divergence": j.get("evidence_divergence"),
        "stability_recomputed": {
            "note": "★ 这是**复算**，不是存档抄写 —— 把存档里的 history 重新喂给 "
                    "judge_stability 跑一遍（纯函数，不调 LLM）",
            "stability_verdict": new_verdict,
            "passed": (recomputed or {}).get("passed"),
            "conclusion": (recomputed or {}).get("conclusion"),
            "matches_archive": (archived_verdict == new_verdict),
            "error": recompute_err,
        },
        "combined_attention": a.get("combined_attention"),
        "judgement_self_check": a.get("judgement_self_check"),
        "missing": [] if j else ["structured_judgement"],
    }


def step11_llm(a: dict) -> dict:
    meta = a.get("llm_meta") or {}
    return {
        "no": 11, "title": "LLM 如何解释这份结构化判断",
        "source": "run.json.llm_explanation + run.json.llm_meta",
        "llm_explanation": a.get("llm_explanation"),
        "llm_meta": {
            "provider": meta.get("provider"),
            "model": meta.get("model"),
            "elapsed_ms": meta.get("elapsed_ms"),
            "content_length": meta.get("content_length"),
            "usage": meta.get("usage"),
            "system_prompt_sha256": meta.get("system_prompt_sha256"),
            "question_sha256": meta.get("question_sha256"),
        },
        "has_reasoning_content": bool(meta.get("reasoning_content")),
        "missing": [] if a.get("llm_explanation") else ["llm_explanation"],
    }


def step12_limitations(a: dict) -> dict:
    j = a.get("structured_judgement") or {}
    lim = j.get("limitations") or {}
    rows = []
    if isinstance(lim, dict):
        for key, v in lim.items():
            if key == "all_keys":
                continue
            if isinstance(v, dict) and v.get("text"):
                rows.append({"key": key, "text": v.get("text"),
                             "source_type": v.get("source_type")})
    return {
        "no": 12, "title": "有哪些限制",
        "source": "run.json.structured_judgement.limitations",
        "limitations": rows,
        "count": len(rows),
        "missing": [] if rows else ["structured_judgement.limitations"],
    }


STEPS = (step1_question, step2_current, step3_history, step4_tools,
         step5_adoption, step6_weather, step7_knowledge, step8_model,
         step9_evidence, step10_judgement, step11_llm, step12_limitations)


def trace(run_id: str) -> dict:
    a = load_archive(run_id)
    steps = []
    for fn in STEPS:
        try:
            steps.append(fn(a))
        except Exception as exc:             # noqa: BLE001
            steps.append({
                "no": fn.__name__, "title": "（这一环节读取失败）",
                "source": "-", "missing": [f"{type(exc).__name__}: {exc}"],
                "error": f"{type(exc).__name__}: {exc}",
            })

    missing_all = []
    for s in steps:
        for m in s.get("missing") or []:
            missing_all.append(f"环节{s.get('no')}（{s.get('title')}）：{m}")

    return {
        "run_id": run_id,
        "archive_path": str((RUNS_DIR / run_id / "run.json")),
        "created_at": a.get("created_at"),
        "status": a.get("status"),
        "scenario": a.get("scenario"),
        "analysis_time": a.get("analysis_time"),
        "location": a.get("location"),
        "steps": steps,
        "self_check": {
            "covered": f"{sum(1 for s in steps if not s.get('error'))}/12",
            "missing": missing_all,
            "text": (f"本次追踪覆盖 {sum(1 for s in steps if not s.get('error'))}/12 环节；"
                     + ("无缺失环节。" if not missing_all
                        else "缺失：" + "；".join(missing_all))),
        },
        "disclaimer": ("本追踪只读存档：不调 LLM、不跑模型、不取天气、不检索知识。"
                       "唯一重算的是稳定性判定（judge_stability，纯函数）。"),
    }


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------

def render(t: dict) -> str:
    L: list[str] = []
    A = L.append
    A("=" * 78)
    A(f" B08 第三遍 · 运行追踪 · {t['run_id']}")
    A("=" * 78)
    A(f"  场景={t['scenario']}  分析时刻={t['analysis_time']}  位置={t['location']}")
    A(f"  存档={t['archive_path']}")
    A(f"  落盘时刻={t['created_at']}  status={t['status']}")
    A("")

    for s in t["steps"]:
        n = s.get("no")
        A("-" * 78)
        A(f" ❶❷❸❹❺❻❼❽❾❿⓫⓬[{n}] {s.get('title')}")
        A(f"    来源：{s.get('source')}")
        miss = s.get("missing") or []
        if miss:
            A(f"    ⚠️ 缺失：{miss}")

        if n == 1:
            A(f"    问题      ：{s.get('question')}")
            A(f"    问题指纹  ：{s.get('question_sha256')}")
            A(f"    提示词指纹：{s.get('system_prompt_sha256')}")
        elif n == 2:
            A(f"    采样时刻  ：{s.get('timestamp')}")
            A(f"    温度={_fmt(s.get('temperature'))}℃  "
              f"湿度={_fmt(s.get('humidity'))}%  "
              f"光照={_fmt(s.get('lux'))} lux")
            A(f"    数据来源  ：source_type={s.get('source_type')}  "
              f"sim_scenario={s.get('sim_scenario')}")
        elif n == 3:
            A(f"    窗口      ：{s.get('window_start')} → {s.get('window_end')}"
              f"（{s.get('count')} 点）")
            for p in s.get("points") or []:
                A(f"      · {p.get('timestamp')}  "
                  f"T={_fmt(p.get('temperature'))}℃  "
                  f"H={_fmt(p.get('humidity'))}%  "
                  f"L={_fmt(p.get('lux'))} lux")
        elif n == 4:
            A(f"    共调用 {s.get('total')} 个工具：")
            for r in s.get("records") or []:
                flag = "✔ 采用" if r.get("adopted") else "✘ 未采用"
                A(f"      · {r.get('tool_name')}  status={r.get('status')}  "
                  f"{flag}")
                A(f"        参数：{r.get('arguments')}")
                A(f"        用途：{r.get('purpose')}")
                if r.get("error"):
                    A(f"        错误：{r.get('error')}")
        elif n == 5:
            A(f"    {s.get('adoption_text')}")
            A(f"    采用为证据的工具：")
            for r in s.get("adopted_tools") or []:
                A(f"      · {r.get('tool_name')} → {r.get('entry_ids')}"
                  f"（对上：{'是' if r.get('matched') else '否'}）")
            A(f"    证据记录：")
            for r in s.get("adopted_entries") or []:
                A(f"      · {r.get('evidence_id')}（{r.get('source_type')}）"
                  f" status={r.get('status')}")
            A(f"    未采用（附理由）：")
            for r in s.get("not_adopted_tools") or []:
                A(f"      · {r.get('tool_name')}（status={r.get('status')}）"
                  f" —— {r.get('reason')}")
        elif n == 6:
            if not s.get("external_conditions_present") and not s.get("ledger_status"):
                A("    本次运行没有外部天气证据。")
            else:
                A(f"    provider    ：{s.get('provider')}")
                A(f"    retrieved_at：{s.get('retrieved_at')}")
                A(f"    forecast_time：{s.get('forecast_time')}")
                A(f"    signal      ：{s.get('signal')}")
                A(f"    available   ：{s.get('available')}   "
                  f"台账 status={s.get('ledger_status')}")
                A(f"    judge_method：{s.get('judge_method')}")
                A(f"    相对变化    ：precip={_fmt(s.get('precip_delta'))}%  "
                  f"temp={_fmt(s.get('temp_delta'))}℃  "
                  f"cloud={_fmt(s.get('cloud_delta'))}%")
                A(f"    判定依据    ：{s.get('basis')}")
                if s.get("error"):
                    A(f"    错误        ：{s.get('error')}")
                A(f"    是否离线构造：{s.get('is_constructed')}")
        elif n == 7:
            if not s.get("present"):
                A(f"    {s.get('note')}")
            else:
                A(f"    status      ：{s.get('status')}")
                A(f"    query       ：{s.get('query')}")
                A(f"    top_k={s.get('top_k')}  命中 {s.get('citation_count')} 条  "
                  f"has_relevant={s.get('has_relevant')}")
                th = s.get("top_hit") or {}
                A(f"    主命中      ：{th.get('chunk_id')} "
                  f"(source={th.get('source_id')}, score={_fmt(th.get('score'), 4)})")
                A(f"      章节：{th.get('section')}  出处：{th.get('page')}")
                A(f"      摘要：{th.get('summary')}")
        elif n == 8:
            A(f"    预测关注度  ：{s.get('predicted_attention')}  "
              f"confidence={_fmt(s.get('confidence'), 3)}")
            A(f"    模型版本    ：{s.get('model_version')}  "
              f"窗口={s.get('window_id')}")
            d = s.get("dtw") or {}
            A(f"    DTW 距离    ：T={_fmt(d.get('temperature'), 3)}  "
              f"H={_fmt(d.get('humidity'), 2)}  L={_fmt(d.get('lux'), 2)}")
            A(f"    特征数={s.get('feature_count')}  "
              f"标签来源={s.get('label_source')}  "
              f"sklearn={s.get('sklearn_version')}")
            A(f"    系数 Top5：")
            for c in s.get("coef_top5") or []:
                A(f"      · {json.dumps(c, ensure_ascii=False)}")
            mt = s.get("metrics") or {}
            if mt:
                A(f"    训练指标    ：accuracy={_fmt(mt.get('accuracy'), 3)} "
                  f"precision={_fmt(mt.get('precision'), 3)} "
                  f"recall={_fmt(mt.get('recall'), 3)} f1={_fmt(mt.get('f1'), 3)}")
        elif n == 9:
            c = s.get("counts") or {}
            A(f"    共 {c.get('total')} 条证据：ok={c.get('ok')}  "
              f"unavailable={c.get('unavailable')}")
            A(f"    方向分布    ：supporting={c.get('supporting')}  "
              f"opposing={c.get('opposing')}  undetermined={c.get('undetermined')}")
            for e in s.get("entries") or []:
                A(f"      · {e.get('evidence_id')}（{e.get('source_type')}）"
                  f" status={e.get('status')} direction={e.get('direction')}")
                if e.get("error"):
                    A(f"        错误：{e.get('error')}")
        elif n == 10:
            A(f"    字段清单    ：{s.get('fields')}")
            rt = s.get("recent_trend_archived") or {}
            A(f"    趋势窗口    ：{rt.get('window_start')} → {rt.get('window_end')}"
              f"（{rt.get('window_span')}）")
            A(f"    稳定判定    ：{rt.get('stability_verdict')}（存档值）")
            A(f"      {rt.get('conclusion')}")
            crit = rt.get("criteria") or {}
            if isinstance(crit, dict):
                for k, v in crit.items():
                    if isinstance(v, dict):
                        A(f"        · {k}：值={_fmt(v.get('value'))} "
                          f"阈值={_fmt(v.get('threshold'))} "
                          f"通过={v.get('passed')}  {v.get('note') or ''}")
            elif isinstance(crit, list):
                for v in crit:
                    if isinstance(v, dict):
                        A(f"        · {v.get('name')}：值={_fmt(v.get('value'))} "
                          f"阈值={_fmt(v.get('threshold'))} "
                          f"通过={v.get('passed')}  {v.get('note') or ''}")
            A(f"    B06 预测    ：{s.get('predicted_attention')}")
            rc = s.get("stability_recomputed") or {}
            A(f"    稳定判定**复算**：{rc.get('stability_verdict')}"
              f"（与存档一致：{rc.get('matches_archive')}）")
            A(f"      {rc.get('note')}")
            if rc.get("error"):
                A(f"      复算失败：{rc.get('error')}")
            sc = s.get("judgement_self_check") or {}
            A(f"    结构化自检  ：passed={sc.get('passed')}  "
              f"字段数={sc.get('field_count')}/{sc.get('expected_field_count')}")
            ca = s.get("combined_attention")
            if ca:
                A(f"    合成关注    ：{ca.get('value')}  "
                  f"（内部={ca.get('internal_predicted_attention')}, "
                  f"外部={ca.get('external_signal')}, "
                  f"has_conflict={ca.get('has_conflict')}）")
                A(f"      理由：{ca.get('reason')}")
            else:
                A("    合成关注    ：（该 run 无 combined_attention 字段）")
        elif n == 11:
            A(f"    LLM 解释原文：")
            for line in (s.get("llm_explanation") or "").splitlines():
                A(f"      {line}")
            m = s.get("llm_meta") or {}
            u = m.get("usage") or {}
            A(f"    provider={m.get('provider')}  model={m.get('model')}  "
              f"elapsed_ms={m.get('elapsed_ms')}  "
              f"content_length={m.get('content_length')}")
            A(f"    token 用量：prompt={u.get('prompt_tokens')} "
              f"completion={u.get('completion_tokens')} "
              f"total={u.get('total_tokens')}")
        elif n == 12:
            A(f"    共 {s.get('count')} 条限制：")
            for lim in s.get("limitations") or []:
                A(f"      · [{lim.get('key')}] {lim.get('text')}")

    A("-" * 78)
    A("  自检")
    A(f"    {t['self_check']['text']}")
    A(f"    {t['disclaimer']}")
    A("=" * 78)
    return "\n".join(L)


# --------------------------------------------------------------------------
# 证明一：同题对照（只改变一个主要条件）
# --------------------------------------------------------------------------
#
# ★ 对照层直接复用**视图四**的机器判定（C1~C5 过程量比较 + R1~R6 差异归因），
#   不另写一套 —— 免得两套判定口径不一致。
# ★ 因果证明用**纯函数重算**：改一个输入 → 重算 → 看输出跟不跟着变。
#   ★ 重算只在内存里做，**不落盘、不产生 run、不进索引**。

#: 四组对照（正式 run = 第二遍的正式验证记录；文档靶子 = B07 开发期存档）
CONTRAST_GROUPS = [
    {
        "key": "A",
        "title": "组 A · S1 ↔ S2 —— 证明历史过程真的有作用",
        "primary": ("run_121", "run_122"),
        "doc_pair": ("run_012", "run_015"),
        "target": "历史过程",
        "clean": True,
    },
    {
        "key": "B",
        "title": "组 B · S2 ↔ S3 —— 文档要求的对照（★ 不干净）",
        "primary": ("run_122", "run_123"),
        "doc_pair": ("run_015", "run_023"),
        "target": "外部条件（但同时混进了历史窗口与稳定判定）",
        "clean": False,
    },
    {
        "key": "C",
        "title": "组 C · S1 ↔ S3 —— 补做的干净对照（唯一变量 = 外部天气）",
        "primary": ("run_121", "run_123"),
        "doc_pair": ("run_012", "run_023"),
        "target": "外部条件",
        "clean": True,
    },
    {
        "key": "D",
        "title": "组 D · S4 ↔ S5 —— 证明『分歧』和『缺失』不是一回事",
        "primary": ("run_124", "run_125"),
        "doc_pair": ("run_057", "run_084"),
        "target": "B03 证据状态（ok/opposing → unavailable/undetermined）",
        "clean": True,
    },
]


def _arch(run_id: str) -> dict:
    return load_archive(run_id)


def _side(a: dict) -> dict:
    """一个 run 的"五项并排"要点。"""
    j = a.get("structured_judgement") or {}
    rt = j.get("recent_trend") or {}
    mo = a.get("model_output") or {}
    ca = a.get("combined_attention") or {}
    hw = a.get("history_window") or {}
    cs = a.get("current_state") or {}
    hist = a.get("history") or []
    temps = [h.get("temperature") for h in hist]
    div = j.get("evidence_divergence") or {}
    b03 = _entry(a, "B03")
    return {
        "run_id": a.get("run_id"),
        "scenario": a.get("scenario"),
        "analysis_time": a.get("analysis_time"),
        "question_sha256": (a.get("llm_meta") or {}).get("question_sha256"),
        "current_state": {
            "timestamp": cs.get("timestamp"),
            "temperature": cs.get("temperature"),
            "humidity": cs.get("humidity"),
            "lux": cs.get("lux"),
        },
        "history_window": {
            "start": hw.get("start"), "end": hw.get("end"), "count": hw.get("count"),
        },
        "trend": {
            "stability_verdict": rt.get("stability_verdict"),
            "conclusion": rt.get("conclusion"),
            "temp_first_last_delta": (round(temps[-1] - temps[0], 2)
                                      if len(temps) >= 2 else None),
            "temp_max_min": (round(max(temps) - min(temps), 2)
                             if temps else None),
            "temp_monotone_up": all(
                (temps[i + 1] - temps[i]) >= 0 for i in range(len(temps) - 1))
            if len(temps) >= 2 else None,
        },
        "model_result": {
            "predicted_attention": mo.get("predicted_attention"),
            "confidence": mo.get("confidence"),
            "model_version": mo.get("model_version"),
            "dtw_temperature": mo.get("dtw_temperature"),
        },
        "final_state": {
            "combined_attention": ca.get("value"),
            "combined_reason": ca.get("reason"),
            "has_conflict": div.get("has_conflict"),
            "divergence_supporting": len(div.get("supporting") or []),
            "divergence_opposing": len(div.get("opposing") or []),
            "divergence_undetermined": len(div.get("undetermined") or []),
            "b03_status": (b03 or {}).get("status"),
            "b03_direction": (b03 or {}).get("direction"),
            "b03_error": (b03 or {}).get("error"),
            "external_signal": ca.get("external_signal"),
        },
    }


def compare_pair(run_a: str, run_b: str) -> dict:
    """逐项比较两个 run（当前值 / 历史窗口 / 趋势 / 模型结果 / 最终状态）。"""
    a = _arch(run_a)
    b = _arch(run_b)

    # ---- 哪些输入变了 / 没变（自己逐字段核，不靠别处的结论）----
    def changed(field_a, field_b):
        return field_a != field_b

    sa, sb = _side(a), _side(b)
    hist_a = a.get("history") or []
    hist_b = b.get("history") or []
    diff_points = sum(
        1 for x, y in zip(hist_a, hist_b)
        if x != y) if len(hist_a) == len(hist_b) else None

    inputs = {
        "history_逐点相同": {
            "same": hist_a == hist_b,
            "diff_points": diff_points,
            "count_a": len(hist_a), "count_b": len(hist_b),
        },
        "history_window": {
            "same": sa["history_window"] == sb["history_window"],
            "a": sa["history_window"], "b": sb["history_window"],
        },
        "current_state": {
            "same": sa["current_state"] == sb["current_state"],
            "a": sa["current_state"], "b": sb["current_state"],
        },
        "model_output": {
            "same": a.get("model_output") == b.get("model_output"),
            "a": sa["model_result"], "b": sb["model_result"],
        },
        "external_conditions": {
            "same": a.get("external_conditions") == b.get("external_conditions"),
            "a_present": bool(a.get("external_conditions")),
            "b_present": bool(b.get("external_conditions")),
        },
        "analysis_time": {
            "same": a.get("analysis_time") == b.get("analysis_time"),
            "a": a.get("analysis_time"), "b": b.get("analysis_time"),
        },
        "question_sha256": {
            "same": sa["question_sha256"] == sb["question_sha256"],
            "a": sa["question_sha256"], "b": sb["question_sha256"],
        },
    }

    # ---- 机器判定：直接复用视图四 ----
    c_assertions = []
    r_attribution = None
    try:
        from B07.views import compare_data, view_compare_data
        cd = compare_data(run_a, run_b)
        c_assertions = cd.get("assertions") or []
        vd = view_compare_data(run_a, run_b)
        r_attribution = {
            "rule": vd["attribution"].get("rule"),
            "rule_text": vd["attribution"].get("rule_text"),
            "chain": vd["attribution"].get("chain"),
            "attribution_complete": vd.get("attribution_complete"),
            "instability_detected": vd.get("instability_detected"),
            "refs": vd["attribution"].get("refs"),
            "notable": vd["attribution"].get("notable"),
            "notes": vd["attribution"].get("notes"),
        }
    except Exception as exc:                 # noqa: BLE001
        r_attribution = {"error": f"{type(exc).__name__}: {exc}"}

    changed_inputs = [k for k, v in inputs.items() if not v["same"]]
    unchanged_inputs = [k for k, v in inputs.items() if v["same"]]

    return {
        "run_a": run_a, "run_b": run_b,
        "a": sa, "b": sb,
        "inputs": inputs,
        "changed_inputs": changed_inputs,
        "unchanged_inputs": unchanged_inputs,
        "single_variable": (len(changed_inputs) == 1),
        "c_assertions": c_assertions,
        "attribution": r_attribution,
    }


# --------------------------------------------------------------------------
# 因果证明（纯函数重算，零 LLM）
# --------------------------------------------------------------------------

def _bundle(archive: dict, history=None) -> dict:
    return {
        "analysis_time": archive.get("analysis_time"),
        "current_state": archive.get("current_state"),
        "history": history if history is not None else (archive.get("history") or []),
        "model_output": archive.get("model_output"),
        "scenario": archive.get("scenario"),
    }


def causal_proof_ca1(s1_run: str, s2_run: str) -> dict:
    """CA-1：把 S1 的 history 塞进 S2 的 run_bundle → 稳定判定应变『稳定』。"""
    from B07.judgement import build_judgement

    a1 = _arch(s1_run)
    a2 = _arch(s2_run)
    base = (a2.get("structured_judgement") or {}).get(
        "recent_trend", {}).get("stability_verdict")
    swapped = build_judgement(_bundle(a2, history=a1.get("history") or []),
                              external_conditions=None)
    new = (swapped.get("recent_trend") or {}).get("stability_verdict")
    return {
        "id": "CA-1",
        "what": f"把 {s1_run}（S1）的 history 塞进 {s2_run}（S2）的 run_bundle",
        "recomputed": "B07.judgement.build_judgement（纯函数，不调 LLM）",
        "changed_input": "run_bundle.history（其余输入保持 S2 原值，含 model_output）",
        "expected": "stability_verdict 由『不稳定』变成『稳定』",
        "before": base, "after": new,
        "passed": (base == "不稳定" and new == "稳定"),
        "note": "只改 history 一个输入 → 结论跟着变 ⇒ 历史过程真正参与了当前状态理解。",
    }


def causal_proof_ca2(s3_run: str) -> dict:
    """CA-2：把 S3 的 external_conditions 置 None → 关注等级不再是 watch_external。"""
    from B07.judgement import build_combined_attention

    a = _arch(s3_run)
    mo = a.get("model_output")
    ec = a.get("external_conditions")
    with_ec = build_combined_attention({"model_output": mo,
                                        "external_conditions": ec})
    without = build_combined_attention({"model_output": mo,
                                        "external_conditions": None})
    return {
        "id": "CA-2",
        "what": f"把 {s3_run}（S3）的 external_conditions 置 None",
        "recomputed": "B07.judgement.build_combined_attention（纯函数，不调 LLM）",
        "changed_input": "external_conditions（model_output 与 history 保持原值）",
        "expected": "combined_attention.value 由 watch_external 变成别的",
        "before": with_ec.get("value"), "after": without.get("value"),
        "passed": (with_ec.get("value") == "watch_external"
                   and without.get("value") != "watch_external"),
        "note": "只改外部条件一个输入 → 关注等级跟着变 ⇒ 外部天气真正参与了未来判断，"
                "不是只显示在页面上。",
    }


def causal_proof_ca3(ok_run: str, missing_run: str) -> dict:
    """CA-3：把 ok 那条 run 的 B03 证据塞进缺失那条 run 的台账 → 分歧应回来。"""
    from B07.evidence_ledger import EvidenceLedger
    from B07.judgement import build_combined_attention, build_evidence_divergence

    a_ok = _arch(ok_run)
    a_miss = _arch(missing_run)

    def b03_of(archive):
        e = _entry(archive, "B03")
        return e

    base_led = a_miss.get("evidence_ledger") or {}
    swapped_data = json.loads(json.dumps(base_led))
    src_b03 = b03_of(a_ok)
    if not src_b03:
        return {"id": "CA-3", "passed": False,
                "error": f"{ok_run} 里没有 B03 证据记录"}
    for e in swapped_data.get("entries") or []:
        if e.get("source_type") == "B03":
            e.update(src_b03)

    before_div = build_evidence_divergence(
        EvidenceLedger.from_dict(base_led))
    after_div = build_evidence_divergence(
        EvidenceLedger.from_dict(swapped_data))

    mo = a_miss.get("model_output")
    before_ca = build_combined_attention({
        "model_output": mo,
        "external_conditions": a_miss.get("external_conditions"),
        "structured_judgement": a_miss.get("structured_judgement"),
    })
    after_ca = build_combined_attention({
        "model_output": mo,
        "external_conditions": a_ok.get("external_conditions"),
        "structured_judgement": {"evidence_divergence": after_div},
    })

    return {
        "id": "CA-3",
        "what": f"把 {ok_run} 的 B03 那条（{src_b03.get('status')}/"
                f"{src_b03.get('direction')}）塞进 {missing_run} 的台账",
        "recomputed": "B07.judgement.build_evidence_divergence + "
                      "build_combined_attention（纯函数，不调 LLM）",
        "changed_input": "evidence_ledger 里 B03 那一条（其余三条不动）",
        "expected": "has_conflict 由 False 变回 True；关注等级由 watch_internal "
                    "变回 conflict_unresolved",
        "before": {
            "has_conflict": before_div.get("has_conflict"),
            "supporting": len(before_div.get("supporting") or []),
            "opposing": len(before_div.get("opposing") or []),
            "combined_attention": before_ca.get("value"),
            "b03_status": (b03_of(a_miss) or {}).get("status"),
            "b03_direction": (b03_of(a_miss) or {}).get("direction"),
        },
        "after": {
            "has_conflict": after_div.get("has_conflict"),
            "supporting": len(after_div.get("supporting") or []),
            "opposing": len(after_div.get("opposing") or []),
            "combined_attention": after_ca.get("value"),
            "b03_status": src_b03.get("status"),
            "b03_direction": src_b03.get("direction"),
        },
        "passed": (before_div.get("has_conflict") is False
                   and after_div.get("has_conflict") is True),
        "note": "只改 B03 那一条证据 → 分歧与关注等级一起变 ⇒ 『分歧』与『缺失』的"
                "差别就落在这一条证据上。",
    }


def contrast_suite(formal: bool = True) -> dict:
    """四组对照 + 三条因果证明（全部在内存里算，不落盘）。"""
    out = {"groups": [], "causal_proofs": []}
    for g in CONTRAST_GROUPS:
        pair = g["primary"] if formal else g["doc_pair"]
        try:
            cmp_data = compare_pair(*pair)
        except Exception as exc:             # noqa: BLE001
            cmp_data = {"error": f"{type(exc).__name__}: {exc}"}
        item = dict(g)
        item["pair_used"] = list(pair)
        item["compare"] = cmp_data
        # 正式/文档两对都算，便于交叉核对
        other = g["doc_pair"] if formal else g["primary"]
        try:
            item["compare_other"] = compare_pair(*other)
        except Exception as exc:             # noqa: BLE001
            item["compare_other"] = {"error": f"{type(exc).__name__}: {exc}"}
        out["groups"].append(item)

    # CA-1/2/3 用正式 run（第二遍的正式验证记录）
    out["causal_proofs"] = [
        causal_proof_ca1("run_121", "run_122"),
        causal_proof_ca2("run_123"),
        causal_proof_ca3("run_124", "run_125"),
    ]
    return out


def render_contrast(suite: dict) -> str:
    L: list[str] = []
    A = L.append
    A("=" * 78)
    A(" B08 第三遍 · 证明一：同题对照 + 因果证明")
    A("=" * 78)
    for g in suite["groups"]:
        A("-" * 78)
        A(f"  {g['title']}")
        A(f"    正式 run 对：{g['pair_used'][0]} ↔ {g['pair_used'][1]}")
        A(f"    文档靶子对：{g['doc_pair'][0]} ↔ {g['doc_pair'][1]}")
        A(f"    干净对照：{'是' if g['clean'] else '★ 否 —— ' + g['target']}")
        c = g.get("compare") or {}
        if "error" in c:
            A(f"    ❌ {c['error']}")
            continue
        A(f"    变化了：{c['changed_inputs']}")
        A(f"    没变  ：{c['unchanged_inputs']}")
        A(f"    单一变量：{c['single_variable']}")
        for side in ("a", "b"):
            s = c[side]
            A(f"    [{side}] {s['run_id']} ({s['scenario']})")
            A(f"        当前值：T={_fmt(s['current_state']['temperature'])}℃ "
              f"H={_fmt(s['current_state']['humidity'])}% "
              f"L={_fmt(s['current_state']['lux'])} lux")
            A(f"        历史窗口：{s['history_window']['start']} → "
              f"{s['history_window']['end']}（{s['history_window']['count']} 点）")
            A(f"        趋势：{s['trend']['stability_verdict']}  "
              f"首尾差={_fmt(s['trend']['temp_first_last_delta'])}℃  "
              f"波动={_fmt(s['trend']['temp_max_min'])}℃  "
              f"单调上升={s['trend']['temp_monotone_up']}")
            A(f"        模型结果：{s['model_result']['predicted_attention']}  "
              f"conf={_fmt(s['model_result']['confidence'], 3)}  "
              f"dtw_T={_fmt(s['model_result']['dtw_temperature'], 3)}")
            fs = s["final_state"]
            A(f"        最终状态：CA={fs['combined_attention']}  "
              f"has_conflict={fs['has_conflict']}  "
              f"B03={fs['b03_status']}/{fs['b03_direction']}  "
              f"三列 支{fs['divergence_supporting']}/反{fs['divergence_opposing']}"
              f"/待{fs['divergence_undetermined']}")
        A("    C1~C5（视图四机器判定）：")
        for x in c.get("c_assertions") or []:
            A(f"      {'✅' if x.get('passed') else '⚠️'} {x.get('id')} "
              f"{x.get('claim')}  |  A={x.get('a_value')}  B={x.get('b_value')}"
              f"  Δ={x.get('delta')}")
        att = c.get("attribution") or {}
        A(f"    归因：{att.get('rule')} —— {att.get('rule_text')}")
        for n in att.get("notes") or []:
            A(f"      · {n}")
    A("-" * 78)
    A("  因果证明（纯函数重算，不落盘 / 不产生 run / 不进索引）")
    for cp in suite["causal_proofs"]:
        A("-" * 78)
        A(f"    {cp.get('id')}  {'✅' if cp.get('passed') else '❌'}")
        A(f"      做法：{cp.get('what')}")
        A(f"      重算：{cp.get('recomputed')}")
        A(f"      改了哪个输入：{cp.get('changed_input')}")
        A(f"      期望：{cp.get('expected')}")
        A(f"      实际：before={cp.get('before')}  after={cp.get('after')}")
        A(f"      结论：{cp.get('note')}")
    A("=" * 78)
    return "\n".join(L)


# --------------------------------------------------------------------------
# 证明二：主动制造一次真实失败（离线判定）
# --------------------------------------------------------------------------

#: F-3 要找的"限定说明"（免责句是**期望出现**的，不是违规）
LIMITATION_PHRASES = ("不可用", "无法判断", "判断受到限制", "受限",
                      "未取得", "无法获取", "不参与", "未参与")

#: F-3 附带检查：天气事实断言（多字短语 + 断言语气）—— 出现即 FAIL
WEATHER_FACT_SUBJECTS = ("外部天气", "天气情况", "天气预报", "降水概率",
                         "降雨概率", "云量", "外部条件", "外部信号", "气温")
WEATHER_FACT_ASSERTIONS = ("显示", "表明", "说明", "证实", "证明", "预计",
                           "将会", "即将", "达到", "升至", "降至", "确认",
                           "预报", "恶化", "好转", "缓解", "加重", "趋于")
WEATHER_DISCLAIMERS = ("无法", "不能", "未能", "未取得", "未获取", "未知",
                       "不可用", "不可", "没有", "并非", "不涉及", "不包含",
                       "不提及", "不要", "缺失", "不予", "限制", "受影响",
                       "不参与", "不作", "无数据", "无记录", "拿不到",
                       "取不到", "暂缺", "空缺")

#: F-4：内部判据引用的特征（说明"仍基于内部证据给出了结论"）
INTERNAL_CITATION_PATTERNS = ("超过阈值", "超过设定", "阈值", "波动",
                              "首尾", "单调", "判据")


def _sentences(text: str) -> list[str]:
    if not text:
        return []
    out, buf = [], []
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


def find_weather_facts(text: str) -> list[dict]:
    """扫"凭空断言天气事实"（免责句不算命中）。"""
    hits = []
    for sent in _sentences(text or ""):
        subs = [p for p in WEATHER_FACT_SUBJECTS if p in sent]
        if not subs:
            continue
        asserts = [m for m in WEATHER_FACT_ASSERTIONS if m in sent]
        if not asserts:
            continue
        rest = sent
        for p in subs + asserts:
            rest = rest.replace(p, "", 1)
        disclaimed = any(m in rest for m in WEATHER_DISCLAIMERS)
        hits.append({"subjects": subs, "assertions": asserts,
                     "sentence": sent, "disclaimed": disclaimed,
                     "counted": not disclaimed})
    return hits


def failure_suite(fail_run: str, base_run: str) -> dict:
    """F-1 ~ F-4 + 反向对照（全部离线判定，只读存档）。"""
    a = _arch(fail_run)
    b = _arch(base_run)
    led = a.get("evidence_ledger") or {}
    records = (led.get("tool_calls") or {}).get("records") or []
    entries = _entries(a)
    text = a.get("llm_explanation") or ""

    # ---- F-1 工具留下失败记录 ----
    weather_calls = [r for r in records
                     if r.get("tool_name") == "B05.get_weather_forecast"]
    f1_rows = [{
        "tool_name": r.get("tool_name"), "status": r.get("status"),
        "error": r.get("error"),
        "passed": (r.get("status") == "unavailable" and bool(r.get("error"))),
    } for r in weather_calls]

    # ---- F-2 证据变为 unavailable ----
    b03 = _entry(a, "B03")
    f2 = {
        "b03_status": (b03 or {}).get("status"),
        "b03_direction": (b03 or {}).get("direction"),
        "b03_error": (b03 or {}).get("error"),
        "passed": ((b03 or {}).get("status") == "unavailable"
                   and (b03 or {}).get("direction") == "undetermined"),
    }

    # ---- F-3 最终结果明确显示限制（+ 不得有天气事实断言）----
    lim_hits = [p for p in LIMITATION_PHRASES if p in text]
    fact_hits = [h for h in find_weather_facts(text) if h["counted"]]
    f3 = {
        "limitation_phrases_found": lim_hits,
        "has_limitation": bool(lim_hits),
        "weather_fact_hits": fact_hits,
        "has_weather_fact": bool(fact_hits),
        "passed": bool(lim_hits) and not fact_hits,
    }

    # ---- F-4 其他能力继续运行 ----
    others = {}
    for st in ("B02", "B06", "B04"):
        e = _entry(a, st)
        others[st] = (e or {}).get("status")
    ca = a.get("combined_attention") or {}
    stab = (a.get("structured_judgement") or {}).get(
        "recent_trend", {}).get("stability_verdict")
    internal_cites = [p for p in INTERNAL_CITATION_PATTERNS if p in text]
    f4 = {
        "other_statuses": others,
        "other_all_ok": all(v == "ok" for v in others.values()),
        "combined_attention": ca.get("value"),
        "combined_attention_not_none": ca.get("value") is not None,
        "stability_verdict": stab,
        "stability_computable": bool(stab),
        "internal_citations": internal_cites,
        "has_internal_conclusion": bool(internal_cites),
        "passed": (all(v == "ok" for v in others.values())
                   and ca.get("value") is not None
                   and bool(stab)
                   and bool(internal_cites)),
    }

    # ---- 反向对照：同一场景的基线变体（不降级）----
    b_led = b.get("evidence_ledger") or {}
    b_b03 = _entry(b, "B03")
    b_ca = b.get("combined_attention") or {}
    b_div = (b.get("structured_judgement") or {}).get(
        "evidence_divergence") or {}
    counter = {
        "baseline_run": base_run,
        "baseline_b03_status": (b_b03 or {}).get("status"),
        "baseline_b03_direction": (b_b03 or {}).get("direction"),
        "baseline_combined_attention": b_ca.get("value"),
        "baseline_has_conflict": b_div.get("has_conflict"),
        "fail_b03_status": (b03 or {}).get("status"),
        "fail_combined_attention": ca.get("value"),
        "fail_has_conflict": (a.get("structured_judgement") or {}).get(
            "evidence_divergence", {}).get("has_conflict"),
        "passed": ((b_b03 or {}).get("status") == "ok"
                   and b_ca.get("value") == "conflict_unresolved"
                   and (b03 or {}).get("status") == "unavailable"),
    }

    return {
        "fail_run": fail_run,
        "base_run": base_run,
        "F-1": {"claim": "工具留下了失败记录", "rows": f1_rows,
                "passed": bool(f1_rows) and all(r["passed"] for r in f1_rows)},
        "F-2": {"claim": "证据变为 unavailable（status + direction）", **f2},
        "F-3": {"claim": "最终结果明确显示限制，且不伪造天气事实", **f3},
        "F-4": {"claim": "其他仍正常的能力继续运行", **f4},
        "counter_evidence": {"claim": "反向对照：同一场景的基线变体（不降级）",
                              **counter},
        "verdict": "PASS",
    }


def render_failure(s: dict) -> str:
    L: list[str] = []
    A = L.append
    A("=" * 78)
    A(f" B08 第三遍 · 证明二：主动制造一次真实失败 · {s['fail_run']}")
    A("=" * 78)
    A(f"  失败 run：{s['fail_run']}   反向对照基线 run：{s['base_run']}")
    A("")
    A(f"  {'✅' if s['F-1']['passed'] else '❌'} F-1 {s['F-1']['claim']}")
    for r in s["F-1"]["rows"]:
        A(f"      · {r['tool_name']}  status={r['status']}  "
          f"error={r['error']}")
    A(f"  {'✅' if s['F-2']['passed'] else '❌'} F-2 {s['F-2']['claim']}")
    A(f"      · B03 status={s['F-2']['b03_status']}  "
      f"direction={s['F-2']['b03_direction']}")
    A(f"      · error={s['F-2']['b03_error']}")
    A(f"  {'✅' if s['F-3']['passed'] else '❌'} F-3 {s['F-3']['claim']}")
    A(f"      · 找到的限定说明：{s['F-3']['limitation_phrases_found']}")
    A(f"      · 天气事实断言命中：{len(s['F-3']['weather_fact_hits'])} 处")
    for h in s["F-3"]["weather_fact_hits"]:
        A(f"        · {h['subjects']}+{h['assertions']} → {h['sentence']}")
    A(f"  {'✅' if s['F-4']['passed'] else '❌'} F-4 {s['F-4']['claim']}")
    A(f"      · 其他证据状态：{s['F-4']['other_statuses']}")
    A(f"      · 合成关注等级：{s['F-4']['combined_attention']}（非 None）")
    A(f"      · 稳定判定仍可计算：{s['F-4']['stability_verdict']}")
    A(f"      · 解释里的内部判据引用：{s['F-4']['internal_citations']}")
    ce = s["counter_evidence"]
    A(f"  {'✅' if ce['passed'] else '❌'} 反向对照 {ce['claim']}")
    A(f"      · 基线 {ce['baseline_run']}：B03={ce['baseline_b03_status']}"
      f"/{ce['baseline_b03_direction']}  "
      f"CA={ce['baseline_combined_attention']}  "
      f"has_conflict={ce['baseline_has_conflict']}")
    A(f"      · 失败 {s['fail_run']}：B03={ce['fail_b03_status']}  "
      f"CA={ce['fail_combined_attention']}  "
      f"has_conflict={ce['fail_has_conflict']}")
    A("=" * 78)
    return "\n".join(L)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="B08 第三遍 · 12 环节运行追踪（只读存档）")
    ap.add_argument("run_id", nargs="?", help="run_id（如 run_082）")
    ap.add_argument("--json", action="store_true", help="输出结构化 JSON")
    ap.add_argument("--contrast", action="store_true",
                    help="跑证明一：四组对照 + 三条因果证明")
    ap.add_argument("--doc-pairs", action="store_true",
                    help="对照用文档指定的靶子 run（默认用第二遍的正式 run）")
    ap.add_argument("--failure", nargs=2, metavar=("FAIL_RUN", "BASE_RUN"),
                    help="跑证明二：F-1~F-4 + 反向对照")
    args = ap.parse_args(argv)

    if args.failure:
        s = failure_suite(args.failure[0], args.failure[1])
        s["verdict"] = ("PASS" if all(s[k]["passed"] for k in
                                      ("F-1", "F-2", "F-3", "F-4"))
                        and s["counter_evidence"]["passed"] else "FAIL")
        if args.json:
            print(json.dumps(s, ensure_ascii=False, indent=2))
        else:
            print(render_failure(s))
        return 0 if s["verdict"] == "PASS" else 1

    if args.contrast:
        suite = contrast_suite(formal=not args.doc_pairs)
        if args.json:
            print(json.dumps(suite, ensure_ascii=False, indent=2))
        else:
            print(render_contrast(suite))
        ok = all(cp.get("passed") for cp in suite["causal_proofs"])
        return 0 if ok else 1

    if not args.run_id:
        ap.error("缺少 run_id（或用 --contrast / --failure 跑证明一 / 证明二）")

    try:
        t = trace(args.run_id)
    except FileNotFoundError as exc:
        print(f"❌ {exc}")
        return 1
    except Exception as exc:                 # noqa: BLE001
        print(f"❌ 追踪失败：{type(exc).__name__}: {exc}")
        return 1

    if args.json:
        print(json.dumps(t, ensure_ascii=False, indent=2))
    else:
        print(render(t))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
