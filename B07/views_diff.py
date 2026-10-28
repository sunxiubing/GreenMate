"""B07 第三遍 · 视图四 | 场景回放与对比 —— 差异归因器（文档 p.66–68）

这个模块**只读存档，绝不重新计算**：

  · 输入只有两个 run.json；
  · 不调 LLM、不跑 B06、不取天气、不检索知识库、不重查 B02、不重跑判据；
  · 三层对比（inputs / evidence / state）**全部从存档字段算**；
  · 归因是**基于存档字段的规则推导**（R1~R5），不是因果推断，
    每条归因文本都必须引用**具体字段值**，不许写"因为环境不同"这种空话。

★ 视图四 = 回放器（`B07.replay`，唯一允许发起新运行的地方）
  + 对比器/归因器（本模块，只读存档）。
  两者职责显式分开：回放产生的新 run 落盘后，
  由视图一/二/三/四**读取同一次分析**，而不是各自重新计算。

用法：
    python -m B07.views_diff run_012 run_015
    python -m B07.views compare run_012 run_015
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if os.name == "nt":                      # Windows 控制台按 codepage 解码
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:                     # pragma: no cover
        pass

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# --------------------------------------------------------------------------
# 读取存档 —— 复用视图一/二/三 同一个 load_run()
# --------------------------------------------------------------------------

def _load(run_id: str) -> dict:
    """读一个 run 存档。**复用 `B07.views.load_run()`**，不另写读盘逻辑。

    这里用函数内延迟 import，避免 views.py ↔ views_diff.py 循环导入。
    """
    from B07.views import load_run
    return load_run(run_id)


def source_path(run_id: str) -> Path:
    from B07.views import RUNS_DIR
    return RUNS_DIR / run_id / "run.json"


# --------------------------------------------------------------------------
# 归因规则（写在代码里，页面/报告都把规则原文显示出来）
# --------------------------------------------------------------------------

ATTRIBUTION_RULES = {
    "R1": "输入变 + 证据变 + 状态变 → 完整因果链：输入变 → 证据变 → 状态变",
    "R2": "输入变 + 证据层无变化 + 状态变 → 差异来自输入/判据层",
    "R3": "输入变 + 证据变 + 状态没变 → 证据变了但状态没变（系统守恒/退让）",
    "R4": "输入没变 + 证据没变 + 状态变了 → 条件没变结果却变了（**不稳定性**）",
    "R5": "状态没变 → 本次条件下判断未变",
    "R6": "（扩展）输入没变 + 证据变 + 状态变 → 差异出在证据层",
}


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _get(obj, *keys, default=None):
    cur = obj
    for k in keys:
        if not isinstance(cur, dict):
            return default
        if k not in cur:
            return default
        cur = cur[k]
    return cur if cur is not None else default


def _fmt_ts(ts) -> str:
    """时间戳压成 MM-DDTHH:MM（完整时间戳在别处也有）。"""
    s = str(ts or "")
    if len(s) >= 16:
        return s[5:16]
    return s or "—"


def _short(v, width: int = 60) -> str:
    s = str(v)
    return s if len(s) <= width else s[: width - 1] + "…"


# --------------------------------------------------------------------------
# 一侧（单个 run）的关键字段抽取
# --------------------------------------------------------------------------

def _history_points(bundle: dict) -> list[dict]:
    pts = bundle.get("history") or []
    out = []
    for p in pts:
        if not isinstance(p, dict):
            continue
        out.append({
            "timestamp": p.get("timestamp"),
            "temperature": p.get("temperature"),
            "humidity": p.get("humidity"),
            "lux": p.get("lux"),
        })
    return out


def _evidence_view(bundle: dict) -> dict:
    """证据层的一侧视图（★ 可用条数一律按 status == "ok" 统计）。"""
    ledger = bundle.get("evidence_ledger") or {}
    entries = list(ledger.get("entries") or [])
    per_type: dict[str, dict] = {}
    for e in entries:
        st = e.get("source_type")
        per_type[st] = {
            "evidence_id": e.get("evidence_id"),
            "status": e.get("status"),
            "direction": e.get("direction"),
            "error": e.get("error"),
        }
    records = (ledger.get("tool_calls") or {}).get("records") or []
    tools = [{
        "tool_name": r.get("tool_name"),
        "status": r.get("status"),
        "adopted": bool(r.get("adopted")),
    } for r in records]
    return {
        "has_ledger": bool(entries),
        "usable": sum(1 for e in entries if e.get("status") == "ok"),
        "total": len(entries),
        "entries": per_type,
        "source_types": sorted(per_type.keys()),
        "tools": tools,
        "tool_names": sorted({t["tool_name"] for t in tools}),
        "ledger_counts": ledger.get("counts") or {},
    }


def _divergence_view(bundle: dict) -> dict:
    div = _get(bundle, "structured_judgement", "evidence_divergence", default={}) or {}
    def _src(bucket):
        out = []
        for it in div.get(bucket) or []:
            if isinstance(it, dict):
                out.append(it.get("source_type"))
        return sorted({x for x in out if x})
    return {
        "has_divergence": bool(div),
        "has_conflict": bool(div.get("has_conflict")),
        "supporting": _src("supporting"),
        "opposing": _src("opposing"),
        "undetermined": _src("undetermined"),
    }


def _side(bundle: dict, run_id: str) -> dict:
    """抽出一个 run 在"五项并排 + 三层对比"里要用的全部字段。"""
    llm_meta = bundle.get("llm_meta") or {}
    sj = bundle.get("structured_judgement") or {}
    ce = sj.get("current_environment") or {}
    rt = sj.get("recent_trend") or {}
    mo = bundle.get("model_output") or {}
    mm = bundle.get("model_meta") or {}
    ca = bundle.get("combined_attention")
    ext = bundle.get("external_conditions")
    hw = bundle.get("history_window") or {}

    points = _history_points(bundle)
    history_sig = (
        f"{len(points)} 点（{hw.get('start') or '—'} → {hw.get('end') or '—'}）"
    )

    evidence = _evidence_view(bundle)
    if evidence["has_ledger"]:
        evidence_line = (f"{evidence['usable']}/{evidence['total']} 条可用"
                         f"（按 status==ok 统计）")
    else:
        evidence_line = "无台账（该 run 早于 S4 的证据层）"

    if ca:
        final_line = f"{ca.get('value')}"
        if ca.get("reason"):
            final_line += f"（{ca['reason']}）"
    else:
        final_line = "—（该 run 无 combined_attention 字段）"

    return {
        "run_id": run_id,
        "scenario": bundle.get("scenario"),
        "created_at": bundle.get("created_at"),
        "analysis_time": bundle.get("analysis_time"),
        "fingerprint": {
            "model": llm_meta.get("model"),
            "system_prompt_sha256": llm_meta.get("system_prompt_sha256"),
            "question_sha256": llm_meta.get("question_sha256"),
        },
        "current_state": {
            "timestamp": _get(bundle, "current_state", "timestamp"),
            "temperature": _get(bundle, "current_state", "temperature"),
            "humidity": _get(bundle, "current_state", "humidity"),
            "lux": _get(bundle, "current_state", "lux"),
            "source_type": _get(bundle, "current_state", "source_type"),
        },
        "history_window": {
            "start": hw.get("start"),
            "end": hw.get("end"),
            "count": hw.get("count"),
        },
        "history": points,
        "history_signature": history_sig,
        "model_path": bundle.get("model_path"),
        "model_version": (mo.get("model_version") or mm.get("model_version")),
        "model_output": mo,
        "external_conditions": ext,
        "external_present": ext is not None,
        "external_signal": (ext or {}).get("signal"),
        "current_environment": ce,
        "stability_verdict": rt.get("stability_verdict"),
        "window_span": rt.get("window_span"),
        "predicted_attention": (sj.get("predicted_attention")
                                or mo.get("predicted_attention")),
        "combined_attention": ca,
        "divergence": _divergence_view(bundle),
        "evidence": evidence,
        # ---- 五项并排用的成品文本 ----
        "five": {
            "current_state": "",       # 由 _build_five() 填
            "recent_trend": "",
            "prediction": "",
            "evidence": evidence_line,
            "final": final_line,
        },
    }


def _build_five(side: dict) -> None:
    """把"五项并排"的成品文本填好（当前状态 / 近期趋势 / 预测 / 证据 / 最终判断）。"""
    cs = side["current_state"]
    ce = side["current_environment"]
    t = ce.get("temperature") if ce.get("temperature") is not None else cs["temperature"]
    h = ce.get("humidity") if ce.get("humidity") is not None else cs["humidity"]
    l = ce.get("lux") if ce.get("lux") is not None else cs["lux"]
    stamp = ce.get("sampled_at") or cs["timestamp"]
    side["five"]["current_state"] = (
        f"温度{t}℃ 湿度{h}% 光照{l}lux @{_fmt_ts(stamp)}")
    side["five"]["recent_trend"] = (
        f"{side['stability_verdict'] or '—'}"
        f"（窗口{side['window_span'] or '—'}）")
    side["five"]["prediction"] = side["predicted_attention"] or "—"


# --------------------------------------------------------------------------
# 第 1 层：inputs
# --------------------------------------------------------------------------

def _diff_inputs(a: dict, b: dict) -> dict:
    items: list[dict] = []

    def add(key, av, bv, changed=None):
        ch = (av != bv) if changed is None else changed
        items.append({"key": key, "a": av, "b": bv, "changed": ch})

    add("analysis_time", a["analysis_time"], b["analysis_time"])
    for field, label in (("temperature", "当前温度"), ("humidity", "当前湿度"),
                          ("lux", "当前光照"), ("timestamp", "当前采样时间")):
        add(f"current_state.{field}", a["current_state"][field],
            b["current_state"][field])
    for field in ("start", "end", "count"):
        add(f"history_window.{field}", a["history_window"][field],
            b["history_window"][field])

    # ---- history 序列：逐点比 ----
    pa, pb = a["history"], b["history"]
    detail = []
    n = max(len(pa), len(pb))
    diff_points = 0
    for i in range(n):
        xa = pa[i] if i < len(pa) else None
        xb = pb[i] if i < len(pb) else None
        same = xa == xb
        if not same:
            diff_points += 1
        detail.append({"index": i + 1, "a": xa, "b": xb, "same": same})
    hist_changed = (pa != pb)
    items.append({
        "key": "history（序列逐点）",
        "a": a["history_signature"],
        "b": b["history_signature"],
        "changed": hist_changed,
        "detail": detail,
        "diff_points": diff_points,
        "total_points": n,
    })

    add("model_path", a["model_path"], b["model_path"])
    add("model_version", a["model_version"], b["model_version"])
    add("external_conditions（是否存在）",
        "存在" if a["external_present"] else "不存在",
        "存在" if b["external_present"] else "不存在")
    add("external_conditions.signal",
        a["external_signal"] or "—", b["external_signal"] or "—")

    changed_items = [it for it in items if it["changed"]]
    return {
        "changed": bool(changed_items),
        "items": items,
        "changed_items": changed_items,
        "changed_keys": [it["key"] for it in changed_items],
    }


# --------------------------------------------------------------------------
# 第 2 层：evidence
# --------------------------------------------------------------------------

def _diff_evidence(a: dict, b: dict) -> dict:
    items: list[dict] = []
    notes: list[str] = []

    ea, eb = a["evidence"], b["evidence"]

    def add(key, av, bv, changed=None):
        ch = (av != bv) if changed is None else changed
        items.append({"key": key, "a": av, "b": bv, "changed": ch})

    add("evidence_ledger（是否存在）",
        "有" if ea["has_ledger"] else "无",
        "有" if eb["has_ledger"] else "无")
    # ---- 外部证据（external_conditions）本身就是一层证据 ----
    #   S1↔S3 这类对照里，两侧都没有证据台账，但 B 侧**确实多了一份外部证据**
    #   （signal=external_deteriorating）。这一层不能因为"没有台账"就当没变。
    add("外部证据（是否存在）",
        "存在" if a["external_present"] else "不存在",
        "存在" if b["external_present"] else "不存在")
    add("外部证据.signal",
        a["external_signal"] or "—", b["external_signal"] or "—")
    add("证据条目 source_type 集合",
        ", ".join(ea["source_types"]) or "—",
        ", ".join(eb["source_types"]) or "—")
    add("可用条数（status==ok）", ea["usable"], eb["usable"])
    add("台账 counts.adopted_entries（采用数，≠有效数）",
        ea["ledger_counts"].get("adopted_entries") if ea["has_ledger"] else "—",
        eb["ledger_counts"].get("adopted_entries") if eb["has_ledger"] else "—")

    for st in sorted(set(ea["source_types"]) | set(eb["source_types"])):
        xa = ea["entries"].get(st) or {}
        xb = eb["entries"].get(st) or {}
        add(f"{st}.status", xa.get("status") or "（无此条）",
            xb.get("status") or "（无此条）")
        add(f"{st}.direction", xa.get("direction") or "（无此条）",
            xb.get("direction") or "（无此条）")

    ta = {t["tool_name"]: t for t in ea["tools"]}
    tb = {t["tool_name"]: t for t in eb["tools"]}
    for name in sorted(set(ta) | set(tb)):
        xa = ta.get(name) or {}
        xb = tb.get(name) or {}
        add(f"tool:{name}.status", xa.get("status") or "（未调用）",
            xb.get("status") or "（未调用）")
        add(f"tool:{name}.adopted",
            "true" if xa.get("adopted") else
            ("false" if xa else "（未调用）"),
            "true" if xb.get("adopted") else
            ("false" if xb else "（未调用）"))

    # ---- 可比性：两侧都没有台账 → 台账部分**无法比较**，不是"证据相同" ----
    comparable = ea["has_ledger"] and eb["has_ledger"]
    if not ea["has_ledger"] and not eb["has_ledger"]:
        notes.append("两侧均无证据台账 —— 台账部分「无法比较」，不是「证据相同」")
    elif not ea["has_ledger"] or not eb["has_ledger"]:
        notes.append("A 无台账 / B 有台账 —— 台账层不可比，按「变了」处理")

    # ---- 外部证据本身也是一层证据：存在性变了要说清楚 ----
    if a["external_present"] != b["external_present"]:
        notes.append(
            f"外部证据这一层确实变了：A "
            f"{'有' if a['external_present'] else '没有'}外部条件，"
            f"B {'有' if b['external_present'] else '没有'}外部条件"
            + (f"（B 的 signal={b['external_signal']}）"
               if b["external_present"] else ""))

    changed_items = [it for it in items if it["changed"]]
    # 台账存在性不对称 → 强制算"变了"（不能判为"没变化"）
    asym = ea["has_ledger"] != eb["has_ledger"]
    return {
        "changed": bool(changed_items) or asym,
        "comparable": comparable,
        "items": items,
        "changed_items": changed_items,
        "changed_keys": [it["key"] for it in changed_items],
        "notes": notes,
        "usable_a": ea["usable"],
        "usable_b": eb["usable"],
    }


# --------------------------------------------------------------------------
# 第 3 层：state
# --------------------------------------------------------------------------

def _diff_state(a: dict, b: dict) -> dict:
    items: list[dict] = []

    def add(key, av, bv, changed=None):
        ch = (av != bv) if changed is None else changed
        items.append({"key": key, "a": av, "b": bv, "changed": ch})

    cea, ceb = a["current_environment"], b["current_environment"]
    for field in ("temperature", "humidity", "lux"):
        add(f"current_environment.{field}",
            cea.get(field) if cea else "—",
            ceb.get(field) if ceb else "—")

    add("recent_trend.stability_verdict",
        a["stability_verdict"] or "—", b["stability_verdict"] or "—")
    add("predicted_attention",
        a["predicted_attention"] or "—", b["predicted_attention"] or "—")
    ca_a = (a["combined_attention"] or {}).get("value")
    ca_b = (b["combined_attention"] or {}).get("value")
    add("combined_attention.value", ca_a or "—", ca_b or "—")
    add("evidence_divergence.has_conflict",
        str(a["divergence"]["has_conflict"]), str(b["divergence"]["has_conflict"]))
    for bucket in ("supporting", "opposing", "undetermined"):
        add(f"evidence_divergence.{bucket}",
            ", ".join(a["divergence"][bucket]) or "—",
            ", ".join(b["divergence"][bucket]) or "—")

    changed_items = [it for it in items if it["changed"]]
    return {
        "changed": bool(changed_items),
        "items": items,
        "changed_items": changed_items,
        "changed_keys": [it["key"] for it in changed_items],
    }


# --------------------------------------------------------------------------
# 归因
# --------------------------------------------------------------------------

def _rule_of(in_c: bool, ev_c: bool, ev_cmp: bool, st_c: bool) -> str:
    if not st_c:
        return "R3" if (in_c and ev_c) else "R5"
    if not in_c and not ev_c:
        return "R4"
    if in_c and ev_c:
        return "R1"
    if in_c:
        return "R2"
    return "R6"


def _attribution(inputs: dict, evidence: dict, state: dict,
                 a: dict, b: dict) -> dict:
    """从三层差异推出归因 —— 每条文本都引用具体字段值。"""
    in_c = inputs["changed"]
    ev_c = evidence["changed"]
    ev_cmp = evidence["comparable"]
    st_c = state["changed"]
    rule = _rule_of(in_c, ev_c, ev_cmp, st_c)

    # ---- 链 ----
    if rule == "R1":
        chain = "输入变 → 证据变 → 状态变"
    elif rule == "R2":
        chain = ("输入变 → 证据层" +
                 ("变" if ev_c else "无变化") + " → 状态变")
        if not ev_cmp:
            # 证据层**不可比**（两侧都没有台账）时，说"无变化"是不准确的：
            # 只能说"没有台账可比"。
            chain = ("输入变 → 证据层无可比台账（无法比较） → 状态变")
    elif rule == "R3":
        chain = "输入变 → 证据变 → 状态未变（系统守恒/退让）"
    elif rule == "R4":
        chain = "条件没变 → 结果却变了（不稳定性）"
    elif rule == "R5":
        chain = "输入/证据/状态均未变 → 本次条件下判断未变"
    else:
        chain = "输入未变 → 证据变 → 状态变"

    # ---- 具体字段引用（变了什么）----
    refs = []
    for layer in (inputs, evidence, state):
        for it in layer["changed_items"]:
            refs.append(f"{it['key']}：{it['a']} → {it['b']}")

    # ---- 关键"没变"的字段（差别其实出在别处）----
    unchanged: list[dict] = []
    notable: list[str] = []
    for it in state["items"]:
        if not it["changed"] and it["key"] in (
                "predicted_attention", "recent_trend.stability_verdict"):
            unchanged.append(it)
            notable.append(f"{it['key']}：{it['a']} = {it['b']}（未变）")

    # ---- 由字段推出的补充说明 ----
    notes: list[str] = []
    ext_intro = ("external_conditions（是否存在）" in inputs["changed_keys"]
                 and not a["external_present"] and b["external_present"])

    # 天气证据从"可用"变"不可用"
    b03 = next((it for it in evidence["items"]
                if it["key"] == "B03.status"), None)
    if b03 and b03["changed"]:
        notes.append(f"同一份外部证据从「{b03['a']}」变成「{b03['b']}」")

    # 分歧退化为缺失
    cf = next((it for it in state["items"]
               if it["key"] == "evidence_divergence.has_conflict"), None)
    if cf and cf["changed"] and cf["a"] == "True" and cf["b"] == "False":
        notes.append("分歧退化为缺失")
    # 关注等级退让到只看内部证据
    cav = next((it for it in state["items"]
                if it["key"] == "combined_attention.value"), None)
    if cav and cav["changed"]:
        if cav["a"] == "conflict_unresolved" and cav["b"] == "watch_internal":
            notes.append("系统退让到只看内部证据"
                         f"（关注等级 {cav['a']} → {cav['b']}）")
        else:
            notes.append(f"关注等级变了：{cav['a']} → {cav['b']}")

    # 判据层与模型层的边界
    pa = next((it for it in state["items"]
               if it["key"] == "predicted_attention"), None)
    sv = next((it for it in state["items"]
               if it["key"] == "recent_trend.stability_verdict"), None)
    if pa and not pa["changed"] and sv and sv["changed"]:
        notes.append(
            f"★ 关键：**B06 预测两侧都是 {pa['a']}** —— 这个差别是"
            f"**判据层**看出来的（稳定性判定 {sv['a']} → {sv['b']}），"
            f"模型层没看出来")
    elif pa and not pa["changed"] and sv and not sv["changed"] and cav and cav["changed"]:
        if ext_intro:
            # A 没有外部条件、B 有 → 证据层多了一份外部证据
            tail = "—— 这正是外部条件接入带来的唯一效果"
        elif ev_c:
            tail = ("—— 变化来自**证据层**（外部证据失效 → 分歧退化为缺失 → "
                    "关注等级退让到只看内部证据）")
        else:
            tail = ""
        notes.append(
            f"★ 关键：稳定性判定与 B06 预测（{pa['a']}）**都没变**，"
            f"变的是关注等级（{cav['a']} → {cav['b']}）" + tail)
    elif pa and not pa["changed"] and sv and not sv["changed"] and not cav["changed"]:
        notes.append(f"B06 预测两侧都是 {pa['a']}（未变）")

    if not ev_cmp:
        notes.append("**台账层无法比较**（两侧均无证据台账），"
                     "所以不能说「证据相同」，只能说「没有台账可比」")

    # ---- 归因正文 ----
    parts = [f"归因规则命中 {rule}：{ATTRIBUTION_RULES[rule]}"]
    parts.append(f"归因链：{chain}")
    if refs:
        parts.append("变了的具体字段：" + "；".join(refs))
    else:
        parts.append("没有字段级差异")
    if notable:
        parts.append("关键未变字段：" + "；".join(notable))
    for n in notes:
        parts.append(n)
    text = "\n".join(parts)

    instability = (rule == "R4")
    if instability:
        text = ("★ 归因：**同一条件下重跑结果不一致（不稳定性）** —— "
                "输入层与证据层都没有变化，状态层却变了。\n" + text)
        if refs:
            text += ("\n不一致的具体字段：" + "；".join(refs))
        elif state["changed_items"]:
            text += ("\n不一致的具体字段：" + "；".join(
                f"{it['key']}：{it['a']} → {it['b']}"
                for it in state["changed_items"]))

    return {
        "rule": rule,
        "rule_text": ATTRIBUTION_RULES[rule],
        "chain": chain,
        "refs": refs,
        "notable": notable,
        "notes": notes,
        "text": text,
        "instability_detected": instability,
        "attribution_complete": True,     # 三层都检查过，且命中了 R1~R6 之一
        "evidence_comparable": ev_cmp,
    }


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------

def diff_runs(run_a: str, run_b: str) -> dict:
    """对比两个 run 存档：三层差异 + 归因。**只读存档，绝不重算。**"""
    ba = _load(run_a)
    bb = _load(run_b)
    a = _side(ba, run_a)
    b = _side(bb, run_b)
    _build_five(a)
    _build_five(b)

    inputs = _diff_inputs(a, b)
    evidence = _diff_evidence(a, b)
    state = _diff_state(a, b)
    attribution = _attribution(inputs, evidence, state, a, b)

    data = {
        "run_a": run_a,
        "run_b": run_b,
        "a": a,
        "b": b,
        "inputs": inputs,
        "evidence": evidence,
        "state": state,
        "attribution": attribution,
        "attribution_complete": attribution["attribution_complete"],
        "instability_detected": attribution["instability_detected"],
        "rules": ATTRIBUTION_RULES,
    }
    payload = {k: v for k, v in data.items() if k != "data_sha256"}
    import hashlib
    data["data_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()
    return data


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    import argparse

    from B07.views import render_compare

    ap = argparse.ArgumentParser(
        description="B07 视图四：场景回放与对比（只读存档，绝不重新计算）")
    ap.add_argument("run_a")
    ap.add_argument("run_b")
    args = ap.parse_args(argv)

    try:
        print(render_compare(args.run_a, args.run_b))
    except FileNotFoundError as exc:
        print(f"[FAIL] {exc}")
        return 2
    except Exception as exc:              # 失败显式暴露，不伪装成功
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
